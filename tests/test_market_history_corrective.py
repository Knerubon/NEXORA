"""Corrective wave on PR #91: clock attestation, strict ranges, pre-materialisation bound,
content identity, structured verification. SYNTHETIC fixtures only."""

from __future__ import annotations

import json
import os
import random
import sys
import tracemalloc
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history.clock import CLAIM, ClockAttestation, check_attestation
from nexora.market_history.convert import TimeConversion, TimeSegment
from nexora.market_history.extract import (
    ExtractionRequest,
    Problem,
    _identity,
    extract,
    usability_blockers,
    verify_dataset,
    verify_dataset_report,
)
from nexora.market_history.guards import validate_margin, validate_protected_ranges
from nexora.market_history.models import (
    CapabilityProfile,
    HistoryError,
    InstrumentSpec,
    MetaField,
    RawTick,
    SourceDescriptor,
    canonical_json,
    sha256_hex,
)
from nexora.market_history.provider import Mt5ReadOnlyHistorySource
from nexora.market_history.quality import QualityConfig

from tests.test_market_history import FakeMt5, FakeSource, att, conv, mk

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TICKS = [mk(1000 + i * 500) for i in range(10)]
REQ = ExtractionRequest("SYN", 1000, 6000, 2000)


class DenySource:
    """Any provider access is a test failure: proves refusals happen before the provider."""

    calls = 0

    def __getattr__(self, name: str) -> Any:
        DenySource.calls += 1
        raise AssertionError(f"provider accessed: {name}")


def go(out: Path, source: Any = None, req: ExtractionRequest = REQ, **kw: Any) -> dict[str, Any]:
    return extract(
        source if source is not None else FakeSource(TICKS),
        req,
        kw.pop("conversion", conv()),
        QualityConfig(5000),
        out,
        retrieved_at=NOW,
        sleep=lambda _s: None,
        clock_attestation=kw.pop("clock_attestation", att()),
        protected_utc_ranges=kw.pop("protected_utc_ranges", ()),
        **kw,
    )


def refuses(out: Path, code: str, **kw: Any) -> None:
    DenySource.calls = 0
    with pytest.raises(HistoryError, match=code):
        go(out, DenySource(), **kw)
    assert DenySource.calls == 0, "provider was reached before the refusal"
    assert not out.exists() or not list(out.iterdir()), "files written before refusal"


# ======================= M1: clock attestation (fail closed) =======================
def test_attestation_is_required_and_checked_before_provider(tmp_path: Path) -> None:
    refuses(tmp_path / "a", "clock_attestation_required", clock_attestation=None)


def test_required_keyword_cannot_be_omitted(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        extract(FakeSource(TICKS), REQ, conv(), QualityConfig(5000), tmp_path, retrieved_at=NOW)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        (dict(clock_attestation=att(1001, 10**9)), "attestation_does_not_cover_window"),
        (dict(clock_attestation=att(0, 5999)), "attestation_does_not_cover_window"),
        (
            dict(clock_attestation=ClockAttestation("e", "me", 0, 10**9, ((3000, 3500),))),
            "window_touches_ambiguous_raw_range",
        ),
        (
            dict(clock_attestation=ClockAttestation("e", "me", 0, 10**9, ((0, 1001),))),
            "window_touches_ambiguous_raw_range",
        ),
    ],
)
def test_attestation_refusals(tmp_path: Path, kwargs: dict[str, Any], code: str) -> None:
    refuses(tmp_path / "o", code, **kwargs)


def test_ambiguous_range_adjacent_to_window_is_allowed(tmp_path: Path) -> None:
    a = ClockAttestation("e", "me", 0, 10**9, ((0, 1000), (6000, 7000)))
    assert go(tmp_path, clock_attestation=a)["tick_count"] == 10


@pytest.mark.parametrize(
    "build",
    [
        lambda: ClockAttestation(" ", "me", 0, 10),
        lambda: ClockAttestation("e", "", 0, 10),
        lambda: ClockAttestation("e", "me", 0, 10, claim="something else"),
        lambda: ClockAttestation("e", "me", 10, 10),
        lambda: ClockAttestation("e", "me", 0.5, 10),  # type: ignore[arg-type]
        lambda: ClockAttestation("e", "me", 0, 10, ((5, 5),)),
        lambda: ClockAttestation("e", "me", 0, 10, ((1, 2, 3),)),  # type: ignore[arg-type]
        lambda: ClockAttestation(None, "me", 0, 10),  # type: ignore[arg-type]
    ],
)
def test_malformed_attestation_objects_rejected(build: Callable[[], Any]) -> None:
    with pytest.raises(HistoryError):
        build()


def segs(*items: tuple[int, int | None, int]) -> TimeConversion:
    return TimeConversion(
        tuple(TimeSegment(a, b, off, "operator_declared", "synthetic") for a, b, off in items)
    )


def test_fall_back_boundary_inside_window_refused(tmp_path: Path) -> None:
    # offset falls 10800 -> 7200 at raw 3000: raw time repeats there
    refuses(
        tmp_path / "o",
        "window_touches_derived_fold_range",
        conversion=segs((0, 3000, 10800), (3000, None, 7200)),
    )


def test_spring_forward_and_outside_boundaries_allowed(tmp_path: Path) -> None:
    # offset RISES: raw skips ahead, no ambiguity
    go(tmp_path / "a", conversion=segs((0, 3000, 7200), (3000, None, 10800)))
    # fall-back boundary exists but its derived ambiguous range ([500-3.6M, 500+3.6M)) is far
    # from the extracted window only when the drop is small: a 1 s drop at raw 100 ends at 1100
    far = segs((0, 100, 10801), (100, None, 10800))
    go(tmp_path / "b", req=ExtractionRequest("SYN", 2000, 6000, 2000), conversion=far)
    with pytest.raises(HistoryError, match="window_touches_derived_fold_range"):
        check_attestation(att(), segs((0, 3000, 10800), (3000, None, 7200)), 2000, 4000)


def test_review_scenario_ascending_interleaved_fold_now_requires_attestation(
    tmp_path: Path,
) -> None:
    """The M1 reproduction: ascending-sorted provider hides a repeated hour from every counter.
    The control is no longer the data: extraction is impossible without an attestation, and an
    attestation naming the ambiguous hour (or a fall-back boundary) refuses the window."""
    H = 3_600_000
    a = [mk(10 * H + i * 60_000 + 123, "100.0", "100.1") for i in range(60)]
    b = [mk(10 * H + i * 60_000 + 456, "101.0", "101.1") for i in range(60)]
    merged = sorted(a + b, key=lambda t: t.time_msc)
    req = ExtractionRequest("SYN", 10 * H, 11 * H, H)
    c = conv(10800)
    refuses(
        tmp_path / "none",
        "clock_attestation_required",
        req=req,
        conversion=c,
        clock_attestation=None,
    )
    amb = ClockAttestation("tz-report-1", "ops", 0, 20 * H, ((10 * H, 11 * H),))
    refuses(
        tmp_path / "amb",
        "window_touches_ambiguous_raw_range",
        req=req,
        conversion=c,
        clock_attestation=amb,
    )
    ok = ClockAttestation("tz-report-1", "ops", 10 * H, 11 * H)
    m = go(tmp_path / "ok", FakeSource(merged), req, conversion=c, clock_attestation=ok)
    # residual, documented: a FALSE attestation is not detectable from the data
    assert (
        m["clock_attestation"]["evidence_ref"] == "tz-report-1"
        and m["quality"]["non_monotonic_count"] == 0
    )


def test_attestation_is_part_of_identity_and_manifest(tmp_path: Path) -> None:
    a = go(tmp_path / "a", clock_attestation=ClockAttestation("e1", "me", 0, 10**9))
    b = go(tmp_path / "b", clock_attestation=ClockAttestation("e2", "me", 0, 10**9))
    assert a["clock_attestation"]["claim"] == CLAIM
    assert a["dataset_id"] != b["dataset_id"] and a["content_sha256"] == b["content_sha256"]


def reseal(d: Path, mutate: Callable[[dict[str, Any]], None]) -> str:
    """Edit the manifest and recompute every self-hash (a competent forger)."""
    mp = d / "manifest.json"
    m = json.loads(mp.read_text())
    m.pop("manifest_sha256")
    mutate(m)
    m["dataset_id"] = sha256_hex(canonical_json(_identity(m)))
    m["manifest_sha256"] = sha256_hex(canonical_json(m))
    mp.chmod(0o644)
    mp.write_text(json.dumps(m))
    return str(m["manifest_sha256"])


def test_verify_rechecks_attestation_and_conversion(tmp_path: Path) -> None:
    go(tmp_path / "a")
    reseal(tmp_path / "a", lambda m: m["clock_attestation"].update(window_to_raw_msc=5999))
    assert "clock_attestation_invalid:attestation_does_not_cover_window" in verify_dataset(
        tmp_path / "a"
    )
    go(tmp_path / "b")
    reseal(
        tmp_path / "b",
        lambda m: m.update(time_conversion=segs((0, 3000, 10800), (3000, None, 7200)).to_json()),
    )
    assert "clock_attestation_invalid:window_touches_derived_fold_range" in verify_dataset(
        tmp_path / "b"
    )


# ======================= M2: strict protected ranges =======================
BAD_RANGES: list[Any] = [
    None,
    "ab",
    b"ab",
    {1: 2},
    (x for x in [(1, 2)]),
    [(1.5, 2.5)],
    [(True, 5)],
    [(5, 1)],
    [(5, 5)],
    [(1, 2, 3)],
    [[1]],
    ["ab"],
    [(1, "2")],
    [(1, None)],
    [(2**70, 2**71)],
    [(1, 2), (9, 3)],
    [5],
    42,
]


@pytest.mark.parametrize("bad", range(len(BAD_RANGES)))
def test_malformed_protected_ranges_refuse_before_any_provider_access(
    tmp_path: Path, bad: int
) -> None:
    refuses(tmp_path / "o", "invalid_protected_range", protected_utc_ranges=BAD_RANGES[bad])


@pytest.mark.parametrize("margin", [-1, 1.5, True, None, "5", 2**70])
def test_invalid_margin_refused_before_provider(tmp_path: Path, margin: Any) -> None:
    refuses(tmp_path / "o", "invalid_protected_margin", protected_margin_ms=margin)


def test_valid_ranges_normalised_and_empty_means_none() -> None:
    assert validate_protected_ranges(((9, 12), [1, 2])) == ((1, 2), (9, 12))
    assert validate_protected_ranges(()) == () and validate_protected_ranges([]) == ()
    assert validate_margin(0) == 0 and validate_margin(7) == 7


def test_protected_overlap_margin_and_recording(tmp_path: Path) -> None:
    c = conv(0)  # utc == raw; window utc [1000, 5999]
    refuses(
        tmp_path / "a",
        "window_overlaps_protected_range",
        conversion=c,
        protected_utc_ranges=[(5999, 7000)],
    )
    go(tmp_path / "b", conversion=c, protected_utc_ranges=[(6000, 7000)])  # adjacent, no margin
    refuses(tmp_path / "c", "window_overlaps_protected_range", conversion=c,
            protected_utc_ranges=[(6000, 7000)], protected_margin_ms=1)  # fmt: skip
    prov = json.loads((tmp_path / "b" / "manifest.json").read_text())["provenance"]
    assert prov["protected_utc_ranges"] == [[6000, 7000]] and prov["protected_margin_ms"] == 0


def test_verify_rechecks_recorded_protection(tmp_path: Path) -> None:
    go(tmp_path, conversion=conv(0), protected_utc_ranges=[(6000, 7000)])
    reseal(tmp_path, lambda m: m["provenance"].update(protected_utc_ranges=[[2000, 3000]]))
    assert "window_overlaps_recorded_protected_range" in verify_dataset(tmp_path)
    reseal(tmp_path, lambda m: m["provenance"].update(protected_utc_ranges=[[5, 1]]))
    assert "protected_ranges_invalid:invalid_protected_range" in verify_dataset(tmp_path)


# ======================= memory bound before materialisation =======================
class SpyKw(FakeSource):
    seen: list[int]

    def ticks(self, symbol: str, a: int, b: int, *, max_ticks: int) -> list[RawTick]:
        self.seen = getattr(self, "seen", []) + [max_ticks]
        return super().ticks(symbol, a, b, max_ticks=max_ticks)


def test_extract_passes_the_bound_to_the_provider(tmp_path: Path) -> None:
    src = SpyKw(TICKS)
    go(tmp_path, src, ExtractionRequest("SYN", 1000, 6000, 2000, max_chunk_ticks=77))
    assert set(src.seen) == {77}


class Exploding:
    """Behaves like a huge terminal array: has a length, refuses any element access."""

    def __len__(self) -> int:
        return 5_000_000

    def __iter__(self) -> Any:
        raise AssertionError("rows were iterated/converted despite exceeding max_ticks")

    def __getitem__(self, i: Any) -> Any:
        raise AssertionError("rows were indexed despite exceeding max_ticks")


class BigTerminal(FakeMt5):
    def __init__(self, rows: Any) -> None:
        super().__init__()
        self.rows = rows

    def copy_ticks_range(self, *_a: Any) -> Any:
        return self.rows


def test_mt5_source_refuses_before_converting_rows() -> None:
    src = Mt5ReadOnlyHistorySource(BigTerminal(Exploding()))
    with pytest.raises(HistoryError, match="chunk_exceeds_max_ticks"):
        src.ticks("SYN", 0, 1000, max_ticks=1000)


def test_mt5_source_converts_when_within_bound() -> None:
    class Row(dict):  # type: ignore[type-arg]
        class dtype:  # noqa: N801
            names = ("time_msc", "bid", "ask", "flags")

    rows = [Row(time_msc=1500, bid=2345.67, ask=2345.89, flags=6)]
    out = Mt5ReadOnlyHistorySource(BigTerminal(rows)).ticks("SYN", 0, 2000, max_ticks=1)
    assert out == [RawTick(1500, Decimal("2345.67"), Decimal("2345.89"), 6)]


class LazyGuard:
    """Refuses before building anything when the window could exceed the bound."""

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor("fixture", "1")

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(())

    def instrument(self, symbol: str) -> InstrumentSpec:
        return InstrumentSpec(symbol, ())

    def ticks(self, symbol: str, a: int, b: int, *, max_ticks: int) -> Sequence[RawTick]:
        if b - a > max_ticks:
            raise HistoryError("chunk_exceeds_max_ticks")
        return [RawTick(t, Decimal(1), Decimal(2)) for t in range(a, b)]


def test_oversized_chunk_peak_memory_stays_small(tmp_path: Path) -> None:
    tracemalloc.start()
    try:
        with pytest.raises(HistoryError, match="chunk_exceeds_max_ticks"):
            go(
                tmp_path,
                LazyGuard(),
                ExtractionRequest("SYN", 0, 400_000, 400_000, max_chunk_ticks=1000),
            )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000, peak  # was 141 MB when the check ran after materialisation


# ======================= content identity =======================
def test_retries_and_layout_do_not_change_dataset_id(tmp_path: Path) -> None:
    clean = go(tmp_path / "c")
    retried = go(tmp_path / "r", FakeSource(TICKS, fail_first=2))
    assert retried["chunks"][0]["attempts"] == 3 and clean["chunks"][0]["attempts"] == 1
    assert retried["dataset_id"] == clean["dataset_id"]
    assert retried["content_sha256"] == clean["content_sha256"]
    assert retried["manifest_sha256"] != clean["manifest_sha256"]  # operational facts stay anchored
    rechunk = go(
        tmp_path / "x", req=ExtractionRequest("SYN", 1000, 6000, 700, max_chunk_ticks=5000)
    )
    assert rechunk["dataset_id"] == clean["dataset_id"]


def test_volatile_metadata_excluded_but_content_and_mapping_included(tmp_path: Path) -> None:
    class Meta(FakeSource):
        def capability_profile(self) -> CapabilityProfile:
            return CapabilityProfile((MetaField("connected", "known", "False"),))

        def instrument(self, symbol: str) -> InstrumentSpec:
            return InstrumentSpec(
                symbol,
                (
                    MetaField("trade_tick_size", "known", "0.01"),
                    MetaField("commission", "unavailable"),
                    MetaField("swap_long", "known", "-9"),
                ),
            )

    base = go(tmp_path / "a")
    # capability profile and trading-rule metadata (swap_long) are not identity
    assert go(tmp_path / "b", Meta(TICKS))["dataset_id"] == base["dataset_id"]
    other_ticks = [*TICKS[:-1], mk(9999 - 5000 + 1000 + 4500, "100.5", "100.6")]
    assert go(tmp_path / "c", FakeSource(other_ticks))["dataset_id"] != base["dataset_id"]
    assert go(tmp_path / "d", conversion=conv(7200))["dataset_id"] != base["dataset_id"]


def test_verify_detects_content_and_count_tampering(tmp_path: Path) -> None:
    go(tmp_path)
    reseal(tmp_path, lambda m: m.update(content_sha256="0" * 64))
    assert "content_sha256_mismatch" in verify_dataset(tmp_path)
    go(tmp_path / "t")
    reseal(tmp_path / "t", lambda m: m.update(tick_count=99))
    assert "tick_count_mismatch" in verify_dataset(tmp_path / "t")


# ======================= structured verification of malformed datasets =======================
def fresh(tmp: Path) -> Path:
    go(tmp)
    return tmp


def write_manifest(d: Path, text: str | bytes) -> None:
    mp = d / "manifest.json"
    mp.chmod(0o644)
    mp.write_bytes(text if isinstance(text, bytes) else text.encode())


def codes(report: list[Problem]) -> list[str]:
    return [p.code for p in report]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"", "manifest_unreadable"),
        (b"{not json", "manifest_unreadable"),
        (b"\xff\xfe\x00bad", "manifest_unreadable"),
        (b"[1,2,3]", "manifest_malformed"),
        (b'"text"', "manifest_malformed"),
        (b"{}", "manifest_malformed"),
        (b'{"schema":"nexora.market-history/1"}', "manifest_malformed"),
    ],
)
def test_garbage_manifests_return_structured_problems(
    tmp_path: Path, payload: bytes, code: str
) -> None:
    write_manifest(fresh(tmp_path), payload)
    report = verify_dataset_report(tmp_path)
    assert report and all(isinstance(p, Problem) for p in report)
    assert code in codes(report)
    assert usability_blockers(tmp_path)  # blockers also never raise


def test_old_schema_is_unsupported_not_a_crash(tmp_path: Path) -> None:
    fresh(tmp_path)
    reseal(tmp_path, lambda m: m.update(schema="nexora.market-history/1"))
    assert codes(verify_dataset_report(tmp_path)) == ["schema_unsupported"]


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda m: m.update(chunks="nope"), "manifest_malformed"),
        (lambda m: m.update(chunks=[]), "manifest_malformed"),
        (lambda m: m.update(window_raw_msc=["a", "b"]), "manifest_malformed"),
        (lambda m: m.update(tick_count="3"), "manifest_malformed"),
        (lambda m: m.update(time_conversion=[]), "manifest_malformed"),
        (lambda m: m["chunks"][0].pop("sha256"), "chunk_entry_malformed"),
        (lambda m: m["chunks"][0].update(tick_count="x"), "chunk_entry_malformed"),
        (lambda m: m["chunks"][0].update(file="../outside.jsonl"), "chunk_name_invalid"),
        (lambda m: m["chunks"][0].update(file="chunk-1-2.jsonl"), "chunk_name_invalid"),
        (lambda m: m.update(source={"nokind": 1}), "manifest_malformed"),
    ],
)
def test_malformed_manifest_shapes_do_not_raise(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], code: str
) -> None:
    fresh(tmp_path)
    mp = tmp_path / "manifest.json"
    m = json.loads(mp.read_text())
    mutate(m)
    write_manifest(tmp_path, json.dumps(m))
    assert code in codes(verify_dataset_report(tmp_path))


def forge_chunk(d: Path, data: bytes) -> None:
    first = json.loads((d / "manifest.json").read_text())["chunks"][0]["file"]
    p = d / first
    p.chmod(0o644)
    p.write_bytes(data)

    def mutate(m: dict[str, Any]) -> None:
        m["chunks"][0]["sha256"] = sha256_hex(data)
        m["content_sha256"] = sha256_hex(
            b"".join((d / c["file"]).read_bytes() for c in m["chunks"])
        )

    reseal(d, mutate)


@pytest.mark.parametrize(
    "data",
    [
        b"not json\n",
        b'{"t":1}\n',
        b"[1,2]\n",
        b'{"t":"1000","b":"1","a":"2"}\n',
        b'{"t":1000,"b":"x","a":"2"}\n',
        b'{"t":1000,"b":"1","a":"2","f":"6"}\n',
        b"\xff\xfe\n",
    ],
)
def test_hash_consistent_but_malformed_chunks_are_structured_failures(
    tmp_path: Path, data: bytes
) -> None:
    fresh(tmp_path)
    forge_chunk(tmp_path, data)
    report = verify_dataset_report(tmp_path)
    assert "chunk_malformed" in codes(report), report
    assert "verify_internal_error" not in codes(report)


@pytest.mark.skipif(sys.platform == "win32", reason="symlink privilege")
def test_symlinked_chunk_is_a_structured_failure(tmp_path: Path) -> None:
    fresh(tmp_path)
    first = json.loads((tmp_path / "manifest.json").read_text())["chunks"][0]["file"]
    target = tmp_path / "elsewhere.dat"
    target.write_bytes((tmp_path / first).read_bytes())
    (tmp_path / first).chmod(0o644)
    (tmp_path / first).unlink()
    os.symlink(target, tmp_path / first)
    assert "chunk_is_symlink" in codes(verify_dataset_report(tmp_path))


def test_missing_directory_and_random_byte_corruption_never_raise(tmp_path: Path) -> None:
    assert codes(verify_dataset_report(tmp_path / "nope")) == ["manifest_missing"]
    fresh(tmp_path / "d")
    raw = (tmp_path / "d" / "manifest.json").read_bytes()
    rnd = random.Random(5)
    for _ in range(300):
        b = bytearray(raw)
        for _ in range(rnd.randint(1, 6)):
            b[rnd.randrange(len(b))] = rnd.randrange(256)
        write_manifest(tmp_path / "d", bytes(b))
        report = verify_dataset_report(tmp_path / "d")
        assert isinstance(report, list) and all(isinstance(p, Problem) for p in report)
        assert "verify_internal_error" not in codes(report), report
    write_manifest(tmp_path / "d", raw)
    assert verify_dataset(tmp_path / "d") == []


def test_problem_text_form_is_stable() -> None:
    assert str(Problem("chunk_missing", "a.jsonl")) == "chunk_missing:a.jsonl"
    assert str(Problem("manifest_missing")) == "manifest_missing"


# ===== CLI validates everything before attaching to the terminal =====
def _load_cli() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "extract_market_history.py"
    spec = importlib.util.spec_from_file_location("extract_cli_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cli_files(
    tmp: Path, *, ranges: Any = None, attestation: dict[str, Any] | None = None
) -> list[str]:
    (tmp / "conv.json").write_text(json.dumps(conv(0).to_json()))
    (tmp / "att.json").write_text(json.dumps(attestation or att().to_json()))
    base = [
        "x", "--symbol", "SYN", "--from-raw-msc", "1000", "--to-raw-msc", "6000",
        "--chunk-ms", "2000", "--max-gap-ms", "5000", "--time-conversion", str(tmp / "conv.json"),
        "--clock-attestation", str(tmp / "att.json"), "--out", str(tmp / "out"),
    ]  # fmt: skip
    if ranges is None:
        return [*base, "--no-protected-ranges"]
    (tmp / "ranges.json").write_text(json.dumps(ranges))
    return [*base, "--protected-utc-ranges", str(tmp / "ranges.json")]


@pytest.mark.parametrize(
    ("ranges", "code"),
    [([[5, 1]], "invalid_protected_range"), ([[1.5, 2.5]], "invalid_protected_range"),
     ([[2000, 3000]], "window_overlaps_protected_range")],
)  # fmt: skip
def test_cli_never_attaches_to_terminal_when_inputs_are_bad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ranges: Any, code: str
) -> None:
    cli = _load_cli()
    attached: list[int] = []

    class Boom:
        def __init__(self) -> None:
            attached.append(1)
            raise AssertionError("terminal attached before validation")

    monkeypatch.setattr(cli, "Mt5ReadOnlyHistorySource", Boom)
    monkeypatch.setattr(sys, "argv", _cli_files(tmp_path, ranges=ranges))
    with pytest.raises(HistoryError, match=code):
        cli.main()
    assert attached == []


def test_cli_requires_an_explicit_protection_choice_and_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load_cli()
    argv = _cli_files(tmp_path)
    monkeypatch.setattr(sys, "argv", [a for a in argv if a != "--no-protected-ranges"])
    with pytest.raises(SystemExit):
        cli.main()
    monkeypatch.setattr(
        sys, "argv", argv[: argv.index("--clock-attestation")] + argv[argv.index("--out") :]
    )
    with pytest.raises(SystemExit):
        cli.main()
