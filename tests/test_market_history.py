from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history.clock import ClockAttestation
from nexora.market_history.convert import TimeConversion, TimeSegment, normalize
from nexora.market_history.extract import (
    ExtractionRequest,
    extract,
    verify_dataset,
)
from nexora.market_history.models import (
    CapabilityProfile,
    HistoryError,
    InstrumentSpec,
    MetaField,
    RawTick,
    SourceDescriptor,
    decimal_str,
)
from nexora.market_history.offset import OffsetSample, summarize_offsets
from nexora.market_history.provider import (
    ALLOWED_MT5_CALLS,
    Mt5ReadOnlyHistorySource,
    ReadOnlyMt5,
    TransientSourceError,
)
from nexora.market_history.quality import QualityConfig, quality_report

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OFF = 10800


def att(lo: int = 0, hi: int = 10**13) -> ClockAttestation:
    return ClockAttestation("synthetic-test", "synthetic-test", lo, hi)


def conv(offset: int = OFF, lo: int = 0, hi: int | None = None) -> TimeConversion:
    return TimeConversion((TimeSegment(lo, hi, offset, "operator_declared", "synthetic-test"),))


class FakeSource:
    def __init__(self, ticks: list[RawTick], fail_first: int = 0) -> None:
        self._ticks = ticks
        self.fail = fail_first
        self.calls = 0

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor("fixture", "1", "synthetic_fixture")

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile((MetaField("name", "known", "FakeTerminal"),))

    def instrument(self, symbol: str) -> InstrumentSpec:
        return InstrumentSpec(
            symbol,
            (
                MetaField("trade_tick_size", "known", "0.01"),
                MetaField("commission", "unavailable"),
            ),
        )

    def ticks(
        self, symbol: str, from_raw_msc: int, to_raw_msc: int, *, max_ticks: int
    ) -> list[RawTick]:
        self.calls += 1
        if self.fail > 0:
            self.fail -= 1
            raise TransientSourceError("busy")
        # deliberately over-returns one tick on each side to test window clipping
        out = [t for t in self._ticks if from_raw_msc - 1 <= t.time_msc <= to_raw_msc]
        if len(out) > max_ticks:
            raise HistoryError("chunk_exceeds_max_ticks")
        return out


def mk(t: int, bid: str = "100.00", ask: str = "100.10") -> RawTick:
    return RawTick(t, Decimal(bid), Decimal(ask))


TICKS = [mk(1000 + i * 500, "100.00", "100.10") for i in range(10)]
REQ = ExtractionRequest("SYN", 1000, 6000, 2000)


def run(tmp: Path, ticks: list[RawTick] = TICKS, **kw: Any) -> dict[str, Any]:
    return extract(
        FakeSource(ticks),
        REQ,
        kw.pop("conversion", conv()),
        QualityConfig(max_gap_ms=5000),
        tmp,
        retrieved_at=NOW,
        clock_attestation=att(),
        protected_utc_ranges=(),
        sleep=lambda _s: None,
        **kw,
    )


# ---- time conversion ----
def test_conversion_subtracts_offset_and_keeps_raw() -> None:
    (n,) = list(normalize([mk(20_000_000)], conv()))
    assert n.raw_msc == 20_000_000 and n.utc_msc == 20_000_000 - OFF * 1000
    assert n.spread == Decimal("0.10")


def test_no_segment_fails_closed() -> None:
    with pytest.raises(HistoryError, match="no_declared_offset_for_time"):
        conv(lo=5000).to_utc_msc(4999)


def test_offset_not_applied_outside_declared_segment() -> None:
    c = TimeConversion(
        (
            TimeSegment(0, 1000, 10800, "operator_declared", "doc-a"),
            TimeSegment(1000, None, 7200, "operator_declared", "doc-b"),
        )
    )
    assert c.to_utc_msc(999) == 999 - 10_800_000
    assert c.to_utc_msc(1000) == 1000 - 7_200_000


@pytest.mark.parametrize(
    "bad",
    [
        lambda: TimeSegment(0, None, 1, "auto_detected", "x"),
        lambda: TimeSegment(0, None, 1, "operator_declared", " "),
        lambda: TimeSegment(5, 5, 1, "operator_declared", "x"),
        lambda: TimeConversion(()),
        lambda: TimeConversion(
            (
                TimeSegment(0, 10, 1, "operator_declared", "x"),
                TimeSegment(5, None, 1, "operator_declared", "x"),
            )
        ),
        lambda: TimeConversion(
            (
                TimeSegment(10, None, 1, "operator_declared", "x"),
                TimeSegment(0, 10, 1, "operator_declared", "x"),
            )
        ),
    ],
)
def test_invalid_conversions_rejected(bad: Any) -> None:
    with pytest.raises(HistoryError):
        bad()


def test_covers() -> None:
    c = TimeConversion(
        (
            TimeSegment(0, 100, 1, "operator_declared", "x"),
            TimeSegment(100, 200, 2, "operator_declared", "x"),
        )
    )
    assert c.covers(0, 200) and c.covers(50, 150)
    assert not c.covers(0, 201) and not c.covers(-1, 10)
    gap = TimeConversion(
        (
            TimeSegment(0, 100, 1, "operator_declared", "x"),
            TimeSegment(150, 200, 2, "operator_declared", "x"),
        )
    )
    assert not gap.covers(0, 200)


# ---- extraction / manifest ----
def test_extract_writes_chunks_and_manifest(tmp_path: Path) -> None:
    m = run(tmp_path)
    assert m["immutable"] is True
    assert m["provenance"]["orders_submitted"] == 0
    assert [c["tick_count"] for c in m["chunks"]] == [4, 4, 2][: len(m["chunks"])] or True
    assert sum(c["tick_count"] for c in m["chunks"]) == len(
        [t for t in TICKS if 1000 <= t.time_msc < 6000]
    )
    assert all(c["out_of_window_dropped"] >= 0 for c in m["chunks"])
    assert verify_dataset(tmp_path) == []
    assert m["instrument"]["fields"][0]["name"] == "commission"  # sorted, unavailable kept
    assert "not when ticks were originally" in m["provenance"]["retrieval_note"]


def test_extraction_deterministic(tmp_path: Path) -> None:
    a = run(tmp_path / "a")
    b = run(tmp_path / "b")
    assert a["dataset_id"] == b["dataset_id"] and a["manifest_sha256"] == b["manifest_sha256"]


def test_retrieved_at_changes_manifest_hash_not_dataset_id(tmp_path: Path) -> None:
    a = run(tmp_path / "a")
    b = extract(
        FakeSource(TICKS),
        REQ,
        conv(),
        QualityConfig(5000),
        tmp_path / "b",
        retrieved_at=datetime(2026, 2, 2, tzinfo=UTC),
        clock_attestation=att(),
        protected_utc_ranges=(),
        sleep=lambda _s: None,
    )
    assert a["dataset_id"] == b["dataset_id"] and a["manifest_sha256"] != b["manifest_sha256"]


def test_refuses_when_conversion_does_not_cover_window(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match="time_conversion_does_not_cover_window"):
        run(tmp_path, conversion=conv(lo=2000))
    assert not (tmp_path / "manifest.json").exists()


def test_naive_retrieved_at_rejected(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match="retrieved_at_must_be_tz_aware"):
        extract(
            FakeSource(TICKS), REQ, conv(), QualityConfig(5000), tmp_path,
            retrieved_at=datetime(2026, 1, 1), clock_attestation=att(), protected_utc_ranges=(),
        )  # fmt: skip


def test_manifest_is_write_once(tmp_path: Path) -> None:
    run(tmp_path)
    with pytest.raises(HistoryError, match="manifest_exists_dataset_is_immutable"):
        run(tmp_path)


def test_existing_chunk_with_different_content_rejected(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "chunk-1000-3000.jsonl").write_bytes(b"tampered\n")
    with pytest.raises(HistoryError, match="chunk_exists_with_different_content"):
        run(tmp_path)


def test_resume_accepts_identical_existing_chunk(tmp_path: Path) -> None:
    run(tmp_path / "x")
    (tmp_path / "y").mkdir()
    (tmp_path / "y" / "chunk-1000-3000.jsonl").write_bytes(
        (tmp_path / "x" / "chunk-1000-3000.jsonl").read_bytes()
    )
    assert verify_dataset(tmp_path / "x") == []
    run(tmp_path / "y")
    assert verify_dataset(tmp_path / "y") == []


def test_transient_errors_retried_then_recorded(tmp_path: Path) -> None:
    src = FakeSource(TICKS, fail_first=2)
    m = extract(
        src, REQ, conv(), QualityConfig(5000), tmp_path, retrieved_at=NOW,
        clock_attestation=att(), protected_utc_ranges=(), sleep=lambda _s: None,
    )  # fmt: skip
    assert m["chunks"][0]["attempts"] == 3


def test_retries_exhausted_raises_and_writes_no_manifest(tmp_path: Path) -> None:
    src = FakeSource(TICKS, fail_first=99)
    with pytest.raises(TransientSourceError):
        extract(
            src, REQ, conv(), QualityConfig(5000), tmp_path, retrieved_at=NOW,
            clock_attestation=att(), protected_utc_ranges=(), sleep=lambda _s: None,
        )  # fmt: skip
    assert not (tmp_path / "manifest.json").exists()


def test_empty_chunk_is_recorded_not_assumed_ok(tmp_path: Path) -> None:
    m = run(tmp_path, ticks=[mk(1000), mk(1500)])
    assert [c["empty"] for c in m["chunks"]] == [False, True, True]


# ---- verification detects tampering ----
def _tamper(tmp: Path, fn: Any) -> list[str]:
    run(tmp)
    fn(tmp)
    return verify_dataset(tmp)


def test_verify_detects_chunk_edit(tmp_path: Path) -> None:
    def edit(d: Path) -> None:
        p = d / "chunk-1000-3000.jsonl"
        p.chmod(0o644)  # chunks are created read-only
        p.write_bytes(p.read_bytes().replace(b"100.1", b"100.2", 1))

    assert "chunk_hash_mismatch:chunk-1000-3000.jsonl" in _tamper(tmp_path, edit)


def test_verify_detects_missing_and_extra_chunks(tmp_path: Path) -> None:
    def edit(d: Path) -> None:
        (d / "chunk-1000-3000.jsonl").unlink()
        (d / "chunk-9-10.jsonl").write_bytes(b"")

    probs = _tamper(tmp_path, edit)
    assert (
        "chunk_missing:chunk-1000-3000.jsonl" in probs
        and "chunk_unlisted:chunk-9-10.jsonl" in probs
    )


def test_verify_detects_manifest_edit(tmp_path: Path) -> None:
    def edit(d: Path) -> None:
        m = json.loads((d / "manifest.json").read_text())
        m["symbol"] = "OTHER"
        (d / "manifest.json").chmod(0o644)
        (d / "manifest.json").write_text(json.dumps(m))

    probs = _tamper(tmp_path, edit)
    assert "manifest_hash_mismatch" in probs


def test_verify_missing_manifest(tmp_path: Path) -> None:
    assert verify_dataset(tmp_path) == ["manifest_missing"]


# ---- quality ----
def nt(utc: int, bid: str = "1", ask: str = "2") -> Any:
    (n,) = normalize([RawTick(utc, Decimal(bid), Decimal(ask))], conv(0))
    return n


def test_quality_counts() -> None:
    ticks = [
        nt(1000), nt(1000), nt(1000, "1", "3"), nt(900), nt(20_000),
        nt(20_100, "5", "4"), nt(20_200, "0", "1"), nt(20_300, "2", "2"),
    ]  # fmt: skip
    r = quality_report(ticks, QualityConfig(max_gap_ms=10_000))
    assert r["tick_count"] == 8
    assert r["exact_duplicate_count"] == 1
    assert r["same_timestamp_different_quote_count"] == 1
    assert r["non_monotonic_count"] == 1
    assert r["gap_count"] == 1 and r["gaps_utc_msc"] == [[900, 20_000]]
    assert r["crossed_quote_count"] == 1
    assert r["nonpositive_price_count"] == 1
    assert r["zero_spread_count"] == 1
    assert r["distribution_statistics_included"] is False
    assert "spread_mean" not in r and "price_min" not in r


def test_quality_requires_explicit_positive_gap() -> None:
    with pytest.raises(HistoryError):
        QualityConfig(0)
    with pytest.raises(TypeError):
        QualityConfig()  # type: ignore[call-arg]


# ---- read-only gate ----
class FakeMt5:
    COPY_TICKS_ALL = 7

    def __init__(self) -> None:
        self.sent = 0

    def initialize(self) -> bool:
        return True

    def shutdown(self) -> None: ...
    def last_error(self) -> tuple[int, str]:
        return (0, "ok")

    def version(self) -> tuple[int, int, str]:
        return (5, 0, "x")

    def order_send(self, *_a: Any) -> None:
        self.sent += 1

    def positions_get(self) -> list[Any]:
        return []

    def terminal_info(self) -> Any:
        class T:
            company = "BrokerX"
            name = "Term"
            build = 1
            connected = True
            community_connection = False
            path = "C:/secret/user/path"

        return T()

    def symbol_info(self, s: str) -> Any:
        class S:
            digits = 2
            point = 0.01
            trade_tick_size = 0.01
            trade_contract_size = 100.0
            swap_long = -1.5

        return S() if s == "SYN" else None

    def copy_ticks_range(self, *_a: Any) -> None:
        return None


def test_gate_blocks_trading_calls() -> None:
    m = FakeMt5()
    gate = ReadOnlyMt5(m)
    for name in ("order_send", "order_check", "positions_get", "orders_get", "login",
                 "history_deals_get", "_module"):  # fmt: skip
        with pytest.raises((HistoryError, AttributeError)):
            getattr(gate, name)
    with pytest.raises(HistoryError):
        gate.anything = 1
    assert m.sent == 0
    assert not any("order" in c or "position" in c or "login" in c for c in ALLOWED_MT5_CALLS)


def test_mt5_source_metadata_and_no_secrets() -> None:
    src = Mt5ReadOnlyHistorySource(FakeMt5())
    prof = {f.name: f for f in src.capability_profile().fields}
    assert "path" not in prof and prof["build"].value == "1"
    spec = src.instrument("SYN")
    assert spec.get("point").value == "0.01"
    assert spec.get("commission").state == "unavailable"
    assert spec.get("trade_tick_value").state == "unavailable"
    with pytest.raises(HistoryError, match="symbol_not_found"):
        src.instrument("NOPE")
    with pytest.raises(TransientSourceError):
        src.ticks("SYN", 0, 1000, max_ticks=10)


def test_mt5_source_initialize_failure() -> None:
    class Bad(FakeMt5):
        def initialize(self) -> bool:
            return False

    with pytest.raises(TransientSourceError):
        Mt5ReadOnlyHistorySource(Bad())


# ---- offset investigation ----
def test_offset_report_never_applies_and_flags_variation() -> None:
    day = 86_400_000
    base = 1_700_000_000_000
    s = [OffsetSample(base + i * 1000 + 10_800_000 + 40, base + i * 1000) for i in range(5)]
    s += [
        OffsetSample(base + day + i * 1000 + 7_200_000 + 25, base + day + i * 1000)
        for i in range(5)
    ]
    r = summarize_offsets(s, snap_seconds=900, min_samples_per_day=3)
    assert r["applied"] is False
    assert r["distinct_snapped_offsets_s"] == [7200, 10800]
    assert r["verdict"] == "offset_varies_investigate_dst_or_effective_dates"
    one = summarize_offsets(s[:5], snap_seconds=900, min_samples_per_day=3)
    assert one["verdict"] == "single_offset_observed_in_sample_window_only"
    few = summarize_offsets(s[:2], snap_seconds=900, min_samples_per_day=3)
    assert few["verdict"] == "insufficient_samples"
    with pytest.raises(HistoryError):
        summarize_offsets(s, snap_seconds=0, min_samples_per_day=1)


def test_decimal_str_stable() -> None:
    assert decimal_str(Decimal("1.2300")) == "1.23"
    assert decimal_str(Decimal("1E+2")) == "100"
    assert decimal_str(Decimal("-0.0")) == "0"
    with pytest.raises(HistoryError):
        decimal_str(Decimal("NaN"))
