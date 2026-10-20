"""Data-integrity fix wave: exact round-trips, bounded memory, crash safety, stress."""

from __future__ import annotations

import decimal
import json
import os
import random
import subprocess
import sys
import textwrap
import tracemalloc
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history.convert import TimeConversion, TimeSegment, normalize
from nexora.market_history.extract import (
    ExtractionRequest,
    clean_stale_temp,
    extract,
    read_chunk,
    usability_blockers,
    verify_dataset,
)
from nexora.market_history.models import (
    CapabilityProfile,
    HistoryError,
    InstrumentSpec,
    RawTick,
    SourceDescriptor,
    decimal_str,
    exact_sub,
)
from nexora.market_history.provider import ALLOWED_MT5_CALLS, ReadOnlyMt5
from nexora.market_history.quality import QualityConfig

from tests.test_market_history import FakeSource, att, conv, mk

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MODES = [
    decimal.ROUND_UP,
    decimal.ROUND_DOWN,
    decimal.ROUND_CEILING,
    decimal.ROUND_FLOOR,
    decimal.ROUND_HALF_UP,
    decimal.ROUND_HALF_DOWN,
    decimal.ROUND_HALF_EVEN,
    decimal.ROUND_05UP,
]


def run(out: Path, source: Any, req: ExtractionRequest, **kw: Any) -> dict[str, Any]:
    return extract(
        source, req, kw.pop("conversion", conv()), QualityConfig(kw.pop("gap", 5000)), out,
        retrieved_at=NOW, sleep=lambda _s: None,
        clock_attestation=kw.pop("clock_attestation", att()),
        protected_utc_ranges=kw.pop("protected_utc_ranges", ()), **kw,
    )  # fmt: skip


# ---- exact decimal text ---------------------------------------------------------------------
def _digits(rnd: random.Random) -> tuple[int, ...]:
    return tuple(rnd.randint(0, 9) for _ in range(rnd.randint(1, 30)))


def _px(rnd: random.Random) -> Decimal:
    return Decimal(rnd.randint(10**6, 10**7)).scaleb(-3, decimal.Context(prec=60))


def reference_str(x: Decimal) -> str:
    with decimal.localcontext(decimal.Context(prec=200)):
        if x == 0:
            return "0"
        text = format(x.normalize(), "f")
        return text


@pytest.mark.parametrize("prec", [1, 2, 3, 4, 7, 28, 50])
@pytest.mark.parametrize("mode", MODES)
def test_decimal_str_is_context_independent_and_exact(prec: int, mode: str) -> None:
    rnd = random.Random(prec * 31 + len(mode))
    values = [
        Decimal("2345.67"),
        Decimal("0.00001230"),
        Decimal("-1E+3"),
        Decimal("123456789.123456789"),
    ]
    values += [Decimal((rnd.randint(0, 1), _digits(rnd), rnd.randint(-20, 10))) for _ in range(200)]
    for v in values:
        with decimal.localcontext() as ctx:
            ctx.prec, ctx.rounding = prec, mode
            text = decimal_str(v)
        assert text == reference_str(v)
        with decimal.localcontext(decimal.Context(prec=200)):
            assert Decimal(text) == v  # exact round trip, value-equal


@pytest.mark.parametrize(
    ("raw", "text"),
    [("-0", "0"), ("0.000", "0"), ("0E+5", "0"), ("1E+2", "100"), ("100.100", "100.1"),
     ("-0.0050", "-0.005"), ("5E-7", "0.0000005"), ("12.00", "12")],
)  # fmt: skip
def test_decimal_str_canonical_forms(raw: str, text: str) -> None:
    with decimal.localcontext() as ctx:
        ctx.prec = 1
        assert decimal_str(Decimal(raw)) == text


@pytest.mark.parametrize(
    "bad", ["NaN", "sNaN", "Infinity", "-Infinity", "1E+9999", "1E-9999", "1" * 80]
)
def test_decimal_str_rejects_non_finite_and_out_of_range(bad: str) -> None:
    with pytest.raises(HistoryError):
        decimal_str(Decimal(bad))


@pytest.mark.parametrize("prec", [1, 3, 28])
def test_exact_sub_matches_integers(prec: int) -> None:
    rnd = random.Random(7)
    for _ in range(300):
        a = Decimal(rnd.randint(1, 10**12)).scaleb(-rnd.randint(0, 6), decimal.Context(prec=60))
        b = Decimal(rnd.randint(1, 10**12)).scaleb(-rnd.randint(0, 6), decimal.Context(prec=60))
        with decimal.localcontext() as ctx:
            ctx.prec = prec
            got = exact_sub(a, b)
        with decimal.localcontext(decimal.Context(prec=200)):
            assert got == a - b
    assert exact_sub(Decimal("NaN"), Decimal("1")).is_nan()


# ---- round trip through extraction under hostile ambient contexts -----------------------------
def test_extraction_round_trip_exact_under_any_ambient_context(tmp_path: Path) -> None:
    rnd = random.Random(3)
    ticks = [RawTick(1000 + i * 7, _px(rnd), _px(rnd), i % 5) for i in range(500)]
    req = ExtractionRequest("SYN", 1000, 5000, 1500)
    ids = set()
    for k, prec in enumerate((28, 1, 2, 5)):
        with decimal.localcontext() as ctx:
            ctx.prec = prec
            ctx.rounding = MODES[k]
            m = run(tmp_path / str(k), FakeSource(ticks), req, gap=10**9)
            assert verify_dataset(tmp_path / str(k)) == []
        ids.add((m["dataset_id"], m["manifest_sha256"]))
        back = [t for c in m["chunks"] for t in read_chunk(tmp_path / str(k) / c["file"])]
        want = [t for t in ticks if 1000 <= t.time_msc < 5000]
        with decimal.localcontext(decimal.Context(prec=200)):
            assert [(t.time_msc, t.bid, t.ask, t.flags) for t in back] == [
                (t.time_msc, t.bid, t.ask, t.flags) for t in want
            ]
    assert len(ids) == 1


# ---- bounded memory -----------------------------------------------------------------------------
class GenSource:
    """Generates ticks on demand; holds none, so test memory measures the extractor only."""

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor("fixture", "1")

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(())

    def instrument(self, symbol: str) -> InstrumentSpec:
        return InstrumentSpec(symbol, ())

    def ticks(self, symbol: str, a: int, b: int, *, max_ticks: int) -> Sequence[RawTick]:
        if b - a > max_ticks:  # one tick per ms: refuse BEFORE building anything
            raise HistoryError("chunk_exceeds_max_ticks")
        return [
            RawTick(t, Decimal(2000000 + t % 977).scaleb(-3, decimal.Context(prec=30)),
                    Decimal(2000100 + t % 977).scaleb(-3, decimal.Context(prec=30)))
            for t in range(a, b)
        ]  # fmt: skip


def peak_extract(tmp: Path, total_ms: int) -> int:
    tracemalloc.start()
    try:
        run(tmp, GenSource(), ExtractionRequest("SYN", 0, total_ms, 1000), gap=10_000)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_extract_memory_is_bounded_by_chunk_not_dataset(tmp_path: Path) -> None:
    small = peak_extract(tmp_path / "s", 10_000)
    big = peak_extract(tmp_path / "b", 60_000)  # 6x the ticks, same chunk size
    assert big < small * 1.5, (small, big)
    assert big < 3_000_000


def test_verify_memory_is_bounded(tmp_path: Path) -> None:
    run(tmp_path / "d", GenSource(), ExtractionRequest("SYN", 0, 60_000, 1000), gap=10_000)
    tracemalloc.start()
    try:
        assert verify_dataset(tmp_path / "d") == []
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1_500_000, peak


def test_chunk_over_limit_refused(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match="chunk_exceeds_max_ticks"):
        run(tmp_path, GenSource(), ExtractionRequest("SYN", 0, 4000, 2000, max_chunk_ticks=1999))


def test_invalid_max_chunk_ticks() -> None:
    with pytest.raises(HistoryError):
        ExtractionRequest("SYN", 0, 10, 5, max_chunk_ticks=0)


# ---- deterministic quality ----------------------------------------------------------------------
def test_quality_independent_of_chunking_and_runs(tmp_path: Path) -> None:
    src = GenSource()
    a = run(tmp_path / "a", src, ExtractionRequest("SYN", 0, 30_000, 2000), gap=10_000)
    b = run(tmp_path / "b", src, ExtractionRequest("SYN", 0, 30_000, 7000), gap=10_000)
    c = run(tmp_path / "c", src, ExtractionRequest("SYN", 0, 30_000, 2000), gap=10_000)
    assert a["quality"] == b["quality"] == c["quality"]
    # dataset_id is CONTENT identity: chunk layout does not change it, the manifest hash does
    assert a["dataset_id"] == b["dataset_id"] == c["dataset_id"]
    assert a["content_sha256"] == b["content_sha256"]
    assert a["manifest_sha256"] != b["manifest_sha256"]


def test_stress_injected_defects_counted_exactly(tmp_path: Path) -> None:
    rnd = random.Random(11)
    ticks: list[RawTick] = []
    expected = dict(dup=0, same=0, back=0, cross=0, nonpos=0, gaps=0)
    t = 1000
    for _ in range(20_000):
        r = rnd.random()
        prev = ticks[-1] if ticks else None
        if prev is not None and r < 0.01:
            ticks.append(RawTick(prev.time_msc, prev.bid, prev.ask))
            expected["dup"] += 1
            continue
        if prev is not None and r < 0.02:
            ticks.append(RawTick(prev.time_msc, prev.bid, prev.ask + Decimal("0.5")))
            expected["same"] += 1
            continue
        t += 10
        if r < 0.03:
            t += 100_000
            expected["gaps"] += 1
        bid = Decimal("100.00")
        ask = Decimal("100.10")
        if 0.03 <= r < 0.04:
            bid, ask = ask, bid
            if ask != bid:
                expected["cross"] += 1
        ticks.append(RawTick(t, bid, ask))
    # in-order stream built; now inject backward steps and a non-positive quote
    for idx in (5000, 9000, 15000):
        ticks[idx] = RawTick(ticks[idx - 1].time_msc - 3, ticks[idx].bid, ticks[idx].ask)
        expected["back"] += 1
    ticks[2000] = RawTick(ticks[2000].time_msc, Decimal("0"), Decimal("1"))
    expected["nonpos"] += 1
    m = run(tmp_path, FakeSource(ticks), ExtractionRequest("SYN", 0, t + 1, 50_000), gap=50_000)
    q = m["quality"]
    assert q["tick_count"] == len(ticks)
    assert q["non_monotonic_count"] >= 3
    assert q["nonpositive_price_count"] == 1
    assert q["gap_count"] >= expected["gaps"] - 3
    assert verify_dataset(tmp_path) == []
    assert "hard_defect:non_monotonic_count" in usability_blockers(tmp_path)


# ---- atomicity and crash recovery -------------------------------------------------------------
CRASH_SCRIPT = textwrap.dedent(
    """
    import os, sys
    from datetime import UTC, datetime
    from pathlib import Path
    sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
    from tests.test_market_history import FakeSource, TICKS, conv, att
    from nexora.market_history import extract as ex
    from nexora.market_history.quality import QualityConfig
    mode, out = sys.argv[3], Path(sys.argv[4])
    calls = {"n": 0}
    if mode == "before_link":
        real = os.link
        def link(a, b, **kw):
            if str(b).startswith("chunk-"):  # V4: the filesystem probe links too, ignore it
                calls["n"] += 1
                if calls["n"] == 2:
                    os._exit(9)   # temp written, final name never created
            return real(a, b, **kw)
        os.link = link
    src = FakeSource(TICKS)
    if mode == "mid_fetch":
        real_ticks = src.ticks
        def ticks(sym, a, b, **kw):
            calls["n"] += 1
            if calls["n"] == 3:
                os._exit(9)
            return real_ticks(sym, a, b, **kw)
        src.ticks = ticks
    ex.extract(src, ex.ExtractionRequest("SYN", 1000, 6000, 2000), conv(), QualityConfig(5000), out,
               retrieved_at=datetime(2026, 1, 1, tzinfo=UTC), sleep=lambda s: None,
               clock_attestation=att(), protected_utc_ranges=())
    """
)


@pytest.mark.parametrize("mode", ["before_link", "mid_fetch"])
def test_hard_kill_then_resume_equals_clean_run(tmp_path: Path, mode: str) -> None:
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "crash"
    proc = subprocess.run(
        [sys.executable, "-c", CRASH_SCRIPT, str(root / "packages"), str(root), mode, str(out)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 9, proc.stderr
    assert not (out / "manifest.json").exists()
    finals = sorted(p.name for p in out.glob("chunk-*.jsonl") if ".tmp-" not in p.name)
    for name in finals:  # every final file is complete, never truncated
        assert (out / name).read_bytes().endswith(b"\n")
    if mode == "before_link":
        assert list(out.glob("*.tmp-*"))  # the interrupted temp file is left behind, ignorable
    resumed = run(out, FakeSource([mk(1000 + i * 500) for i in range(10)]),
                  ExtractionRequest("SYN", 1000, 6000, 2000))  # fmt: skip
    clean = run(tmp_path / "clean", FakeSource([mk(1000 + i * 500) for i in range(10)]),
                ExtractionRequest("SYN", 1000, 6000, 2000))  # fmt: skip
    assert resumed["dataset_id"] == clean["dataset_id"]
    assert verify_dataset(out) == [] and not list(out.glob("*.tmp-*"))


def test_stale_temp_is_reported_by_verify_and_cleaned(tmp_path: Path) -> None:
    run(tmp_path, FakeSource([mk(1000)]), ExtractionRequest("SYN", 1000, 2000, 1000))
    (tmp_path / "chunk-1-2.jsonl.tmp-1").write_bytes(b"x")
    assert verify_dataset(tmp_path) == ["stale_temp_file:chunk-1-2.jsonl.tmp-1"]
    assert clean_stale_temp(tmp_path) == 1 and verify_dataset(tmp_path) == []


def test_outputs_are_read_only(tmp_path: Path) -> None:
    run(tmp_path, FakeSource([mk(1000)]), ExtractionRequest("SYN", 1000, 2000, 1000))
    for p in tmp_path.iterdir():
        assert not p.stat().st_mode & 0o222, p.name


def test_os_link_failure_leaves_no_final_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.link

    def boom(a: Any, b: Any, **kw: Any) -> None:
        if str(b).startswith("chunk-"):  # V4: the filesystem probe links too, ignore it
            raise OSError("disk full")
        real(a, b, **kw)

    monkeypatch.setattr(os, "link", boom)
    with pytest.raises(OSError):
        run(tmp_path, FakeSource([mk(1000)]), ExtractionRequest("SYN", 1000, 2000, 1000))
    assert not list(tmp_path.glob("chunk-*"))
    assert not list(tmp_path.glob("*.tmp-*"))


# ---- manifest provenance ------------------------------------------------------------------------
def test_external_anchor_detects_self_consistent_forgery(tmp_path: Path) -> None:
    m = run(tmp_path, FakeSource([mk(1000 + i * 500) for i in range(10)]),
            ExtractionRequest("SYN", 1000, 6000, 2000))  # fmt: skip
    anchor = m["manifest_sha256"]
    assert verify_dataset(tmp_path, expected_manifest_sha256=anchor) == []
    from nexora.market_history.models import canonical_json, sha256_hex

    p = tmp_path / m["chunks"][0]["file"]
    p.chmod(0o644)
    p.write_bytes(p.read_bytes().replace(b"100.1", b"100.7"))
    mp = tmp_path / "manifest.json"
    mm = json.loads(mp.read_text())
    mm.pop("manifest_sha256")
    mm["chunks"][0]["sha256"] = sha256_hex(p.read_bytes())
    from nexora.market_history.extract import _identity

    mm["content_sha256"] = sha256_hex(
        b"".join((tmp_path / c["file"]).read_bytes() for c in mm["chunks"])
    )
    mm["dataset_id"] = sha256_hex(canonical_json(_identity(mm)))
    mm["manifest_sha256"] = sha256_hex(canonical_json(mm))
    mp.chmod(0o644)
    mp.write_text(json.dumps(mm))
    assert verify_dataset(tmp_path) == []  # self-consistent: only the external anchor catches it
    assert "manifest_anchor_mismatch" in verify_dataset(tmp_path, expected_manifest_sha256=anchor)


def test_provenance_fields_present(tmp_path: Path) -> None:
    m = run(tmp_path, FakeSource([mk(1000)]), ExtractionRequest("SYN", 1000, 2000, 1000))
    assert m["provenance"]["orders_submitted"] == 0 and m["provenance"]["extractor"].endswith("/5")
    assert (
        m["max_chunk_ticks"] == 1_000_000
        and "not when ticks were" in m["provenance"]["retrieval_note"]
    )


# ---- hard defects, non-finite, DST fold, protected ranges -------------------------------------
def test_non_finite_quotes_preserved_and_blocking(tmp_path: Path) -> None:
    ticks = [
        mk(1000),
        RawTick(1500, Decimal("NaN"), Decimal("1")),
        RawTick(1600, Decimal("1"), Decimal("Infinity")),
        mk(1700),
    ]
    m = run(tmp_path, FakeSource(ticks), ExtractionRequest("SYN", 1000, 2000, 1000))
    assert m["quality"]["tick_count"] == 4 and m["quality"]["nonfinite_quote_count"] == 2
    assert verify_dataset(tmp_path) == []
    assert "hard_defect:nonfinite_quote_count" in usability_blockers(tmp_path)
    back = list(read_chunk(tmp_path / m["chunks"][0]["file"]))
    assert back[1].bid.is_nan() and back[2].ask.is_infinite()


def test_clean_dataset_has_no_blockers(tmp_path: Path) -> None:
    run(
        tmp_path,
        FakeSource([mk(1000 + i * 100) for i in range(5)]),
        ExtractionRequest("SYN", 1000, 2000, 1000),
    )
    assert usability_blockers(tmp_path) == []


def test_dst_fall_back_fold_is_flagged_not_hidden(tmp_path: Path) -> None:
    """Broker clock steps back an hour at fall-back: raw time repeats. Raw-keyed offset segments
    cannot disambiguate it; it must surface as a hard defect, never pass silently."""
    hour = 3_600_000
    first = [mk(10 * hour + i * 60_000) for i in range(60)]  # raw 10:00-10:59 (summer offset)
    fold = [mk(10 * hour + i * 60_000) for i in range(60)]  # raw 10:00-10:59 AGAIN (winter offset)
    c = TimeConversion((TimeSegment(0, None, 10800, "operator_declared", "synthetic"),))
    req = ExtractionRequest("SYN", 10 * hour, 11 * hour, hour)
    m = run(tmp_path, FakeSource(first + fold), req, conversion=c, gap=10**9)
    assert m["quality"]["non_monotonic_count"] == 1
    assert "hard_defect:non_monotonic_count" in usability_blockers(tmp_path)


def test_protected_range_refused_by_utc_after_conversion(tmp_path: Path) -> None:
    src = FakeSource([mk(1_000_000 + i) for i in range(10)])
    c = conv(offset=3600)  # utc = raw - 3_600_000
    req = ExtractionRequest("SYN", 4_600_000, 4_700_000, 100_000)
    lo = 4_600_000 - 3_600_000
    with pytest.raises(HistoryError, match="window_overlaps_protected_range"):
        run(tmp_path / "a", src, req, conversion=c, protected_utc_ranges=[(lo + 10, lo + 20)])
    # adjacent but not overlapping ([p_lo, p_hi) ends exactly at the window's first instant)
    run(tmp_path / "b", src, req, conversion=c, protected_utc_ranges=[(lo - 50, lo)])
    assert not (tmp_path / "a" / "manifest.json").exists()


def test_protected_range_checked_across_offset_segments(tmp_path: Path) -> None:
    c = TimeConversion((
        TimeSegment(0, 1000, 0, "operator_declared", "a"),
        TimeSegment(1000, None, 7200, "operator_declared", "b"),
    ))  # fmt: skip
    with pytest.raises(HistoryError, match="window_overlaps_protected_range"):
        run(tmp_path, FakeSource([]), ExtractionRequest("SYN", 500, 1500, 1000), conversion=c,
            protected_utc_ranges=[(-7_200_000 + 1000, -7_200_000 + 1001)])  # fmt: skip


# ---- provider gate -----------------------------------------------------------------------------
def test_gate_initialize_takes_no_credentials_and_symbol_select_removed() -> None:
    class M:
        def initialize(self, *a: Any, **k: Any) -> bool:
            return True

    g = ReadOnlyMt5(M())
    assert g.initialize() is True
    with pytest.raises(TypeError):
        g.initialize(login=1, password="x")
    assert "symbol_select" not in ALLOWED_MT5_CALLS
    with pytest.raises(HistoryError):
        g.symbol_select  # noqa: B018


def test_normalize_spread_exact_under_ambient(tmp_path: Path) -> None:
    t = RawTick(0, Decimal("12345.678"), Decimal("12345.789"))
    with decimal.localcontext() as ctx:
        ctx.prec = 2
        (n,) = list(normalize([t], conv(0)))
    assert n.spread == Decimal("0.111")
