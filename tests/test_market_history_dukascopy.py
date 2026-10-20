"""SYNTHETIC fixtures only: the bytes below are generated here, not market data."""

from __future__ import annotations

import decimal
import json
import lzma
import stat
import struct
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history.dukascopy import (
    DeclaredSemantics,
    decode_hour,
    exact_decimal,
    ingest,
    rel_path,
    scale_of,
    slots,
    verify,
)
from nexora.market_history.models import HistoryError
from nexora.market_history.quality import QualityConfig

NOW = datetime(2026, 1, 1, tzinfo=UTC)
START = date(2026, 9, 7)
SEM = DeclaredSemantics(0, "synthetic-test", 1000, 0, "synthetic-test")


def bi5(ticks: list[tuple[int, int, int]]) -> bytes:
    return lzma.compress(b"".join(struct.pack(">IIIff", o, a, b, 1.0, 1.0) for o, a, b in ticks))


def build(
    root: Path, files: dict[int, bytes], sem: DeclaredSemantics = SEM, days: int = 2
) -> tuple[Path, Path]:
    src = root / "src"
    entries = []
    sl = slots(START, days)
    for hour, data in files.items():
        rel = rel_path("XAUUSD", sl[hour], sem.month_index_base)
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
        entries.append(
            {"file": rel, "url": f"https://example.invalid/{rel}", "retrieved_at": NOW.isoformat()}
        )
    log = root / "log.json"
    log.write_text(json.dumps({"files": entries}))
    return src, log


def run(root: Path, files: dict[int, bytes], **kw: Any) -> dict[str, Any]:
    sem = kw.pop("sem", SEM)
    src, log = build(root, files, sem, kw.get("days", 2))
    return ingest(
        src,
        root / "out",
        symbol="XAUUSD",
        start_day=START,
        days=kw.get("days", 2),
        semantics=sem,
        retrieval_log=log,
        selection_ref="PILOT-PLAN.md@d78c04d",
        quality=QualityConfig(600_000),
        ingested_at=NOW,
    )


GOOD = bi5([(0, 2345670, 2345450), (1000, 2345680, 2345460), (2000, 2345690, 2345470)])
FILES = {10: GOOD, 11: bi5([(500, 2345700, 2345480)]), 12: b""}


# ---- decoding ----
def test_decode_roundtrip_and_empty() -> None:
    slot = slots(START, 1)[10]
    ticks = decode_hour(GOOD, slot)
    assert [t.label_msc - int(slot.timestamp()) * 1000 for t in ticks] == [0, 1000, 2000]
    assert decode_hour(b"", slot) == []


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"not lzma at all", "lzma_decode_failed"),
        (lzma.compress(b"x" * 19), "record_size_mismatch"),
        (bi5([(3_600_000, 1, 1)]), "record_offset_outside_hour"),
    ],
)
def test_decode_errors_have_codes(data: bytes, code: str) -> None:
    with pytest.raises(HistoryError, match=code):
        decode_hour(data, slots(START, 1)[0])


def test_exact_decimal_ignores_ambient_context() -> None:
    with decimal.localcontext() as c:
        c.prec = 2
        assert str(exact_decimal(2345670, 3)) == "2345.670"
        assert str(exact_decimal(-5, 3)) == "-0.005"
        assert exact_decimal(0, 3) == 0


@pytest.mark.parametrize("bad", [0, -10, 1500, 7, True, 1.0])
def test_bad_divisors(bad: Any) -> None:
    with pytest.raises(HistoryError):
        scale_of(bad)


def test_month_index_base_in_path() -> None:
    sep = datetime(2026, 9, 7, 5, tzinfo=UTC)
    assert rel_path("XAUUSD", sep, 0) == "XAUUSD/2026/08/07/05h_ticks.bi5"
    assert rel_path("XAUUSD", sep, 1) == "XAUUSD/2026/09/07/05h_ticks.bi5"


# ---- ingest / manifest ----
def test_ingest_preserves_bytes_and_reports_coverage(tmp_path: Path) -> None:
    m = run(tmp_path, FILES)
    out = tmp_path / "out" / "raw"
    for hour, data in FILES.items():
        rel = rel_path("XAUUSD", slots(START, 2)[hour], 0)
        assert (out / rel).read_bytes() == data  # byte-identical
        assert not (out / rel).stat().st_mode & stat.S_IWUSR
    states = m["coverage"]["state_counts"]
    assert states == {"absent_not_supplied": 45, "present_nonempty": 2, "present_empty": 1}
    assert m["coverage"]["absent_or_empty_is_a_defect"] is False
    assert m["coverage"]["per_day"]["2026-09-07"][10:13] == "##o"
    assert m["quality"]["tick_count"] == 4
    assert m["provenance"]["network_access"] is False
    assert all(r["source_url"].startswith("https://") for r in m["slots"] if "source_url" in r)
    assert verify(tmp_path / "out") == []


def test_no_price_distributions_in_manifest(tmp_path: Path) -> None:
    text = json.dumps(run(tmp_path, FILES))
    assert "spread_mean" not in text and "2345" not in text


def test_deterministic_across_ambient_precision(tmp_path: Path) -> None:
    a = run(tmp_path / "a", FILES)
    with decimal.localcontext() as c:
        c.prec = 3
        b = run(tmp_path / "b", FILES)
    assert a["dataset_id"] == b["dataset_id"]
    assert a["normalized_stream_sha256"] == b["normalized_stream_sha256"]
    with decimal.localcontext() as c:
        c.prec = 3
        assert verify(tmp_path / "b" / "out") == []


def test_clock_offset_shifts_utc_but_not_label(tmp_path: Path) -> None:
    a = run(tmp_path / "a", FILES)
    b = run(tmp_path / "b", FILES, sem=DeclaredSemantics(7200, "doc", 1000, 0, "x"))
    assert a["quality"]["first_utc_msc"] - 7_200_000 == b["quality"]["first_utc_msc"]
    assert a["dataset_id"] != b["dataset_id"]


def test_quality_counts_duplicates_disorder_crossed(tmp_path: Path) -> None:
    bad = bi5(
        [(0, 100, 90), (0, 100, 90), (0, 101, 90), (5000, 100, 90), (1000, 100, 90), (6000, 80, 90)]
    )
    q = run(tmp_path, {10: bad})["quality"]
    assert q["exact_duplicate_count"] == 1
    assert q["same_timestamp_different_quote_count"] == 1
    assert q["non_monotonic_count"] == 1
    assert q["crossed_quote_count"] == 1


def test_undecodable_file_is_recorded_not_fatal(tmp_path: Path) -> None:
    m = run(tmp_path, {10: GOOD, 11: b"garbage"})
    row = [r for r in m["slots"] if r["file"].endswith("11h_ticks.bi5")][0]
    assert row["decode_error"] == "lzma_decode_failed" and m["decode_errors"] == [row["file"]]
    assert m["quality"]["tick_count"] == 3
    assert verify(tmp_path / "out") == []


# ---- refusals ----
def test_requires_retrieval_record_per_present_file(tmp_path: Path) -> None:
    src, log = build(tmp_path, FILES)
    data = json.loads(log.read_text())
    data["files"].pop()
    log.write_text(json.dumps(data))
    with pytest.raises(HistoryError, match="retrieval_record_missing"):
        ingest(
            src,
            tmp_path / "out",
            symbol="XAUUSD",
            start_day=START,
            days=2,
            semantics=SEM,
            retrieval_log=log,
            selection_ref="x",
            quality=QualityConfig(1),
            ingested_at=NOW,
        )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda e: e[0].update(retrieved_at="2026-01-01T00:00:00"),
            "retrieved_at_must_be_tz_aware",
        ),
        (lambda e: e[0].update(url=" "), "source_url_required"),
        (lambda e: e.append(dict(e[0])), "duplicate_retrieval_record"),
        (
            lambda e: e.append({**e[0], "file": "XAUUSD/2026/08/09/00h_ticks.bi5"}),
            "retrieval_record_for_unplanned_file",
        ),
    ],
)
def test_bad_retrieval_logs(tmp_path: Path, mutate: Any, code: str) -> None:
    src, log = build(tmp_path, FILES)
    data = json.loads(log.read_text())
    mutate(data["files"])
    log.write_text(json.dumps(data))
    with pytest.raises(HistoryError, match=code):
        ingest(
            src,
            tmp_path / "out",
            symbol="XAUUSD",
            start_day=START,
            days=2,
            semantics=SEM,
            retrieval_log=log,
            selection_ref="x",
            quality=QualityConfig(1),
            ingested_at=NOW,
        )


def test_declared_semantics_are_mandatory() -> None:
    for args in [
        (0, " ", 1000, 0, "x"),
        (0, "d", 1000, 0, ""),
        (0, "d", 1000, 2, "x"),
        (0, "d", 3, 0, "x"),
    ]:
        with pytest.raises(HistoryError):
            DeclaredSemantics(*args)


def test_selection_ref_and_tz_required(tmp_path: Path) -> None:
    src, log = build(tmp_path, FILES)
    kw: dict[str, Any] = dict(
        symbol="XAUUSD",
        start_day=START,
        days=2,
        semantics=SEM,
        retrieval_log=log,
        quality=QualityConfig(1),
    )
    with pytest.raises(HistoryError, match="selection_ref_required"):
        ingest(src, tmp_path / "o1", selection_ref=" ", ingested_at=NOW, **kw)
    with pytest.raises(HistoryError, match="ingested_at_must_be_tz_aware"):
        ingest(src, tmp_path / "o2", selection_ref="x", ingested_at=datetime(2026, 1, 1), **kw)


def test_manifest_write_once_and_raw_conflict(tmp_path: Path) -> None:
    run(tmp_path, FILES)
    src, log = tmp_path / "src", tmp_path / "log.json"
    kw: dict[str, Any] = dict(
        symbol="XAUUSD",
        start_day=START,
        days=2,
        semantics=SEM,
        retrieval_log=log,
        selection_ref="x",
        quality=QualityConfig(1),
        ingested_at=NOW,
    )
    with pytest.raises(HistoryError, match="manifest_exists_dataset_is_immutable"):
        ingest(src, tmp_path / "out", **kw)
    (tmp_path / "out" / "manifest.json").unlink()
    rel = rel_path("XAUUSD", slots(START, 2)[10], 0)
    (src / rel).write_bytes(bi5([(0, 1, 1)]))
    with pytest.raises(HistoryError, match="raw_exists_with_different_content"):
        ingest(src, tmp_path / "out", **kw)


# ---- independent verification detects tampering ----
def _tamper(tmp: Path, fn: Any) -> list[str]:
    run(tmp, FILES)
    fn(tmp / "out")
    return verify(tmp / "out")


def test_verify_detects_raw_edit(tmp_path: Path) -> None:
    def edit(d: Path) -> None:
        p = next((d / "raw").rglob("10h_ticks.bi5"))
        p.chmod(0o644)
        p.write_bytes(bi5([(0, 1, 1)]))

    assert any(x.startswith("raw_hash_mismatch") for x in _tamper(tmp_path, edit))


def test_verify_detects_missing_extra_and_manifest_edit(tmp_path: Path) -> None:
    def edit(d: Path) -> None:
        p = next((d / "raw").rglob("11h_ticks.bi5"))
        p.chmod(0o644)
        p.unlink()
        extra = d / "raw" / "XAUUSD" / "extra.bi5"
        extra.write_bytes(b"")

    probs = _tamper(tmp_path, edit)
    assert any(x.startswith("raw_missing") for x in probs)
    assert any(x.startswith("raw_unlisted") for x in probs)

    def edit2(d: Path) -> None:
        m = json.loads((d / "manifest.json").read_text())
        m["symbol"] = "OTHER"
        (d / "manifest.json").write_text(json.dumps(m))

    assert "manifest_hash_mismatch" in _tamper(tmp_path / "second", edit2)


def test_verify_missing_manifest(tmp_path: Path) -> None:
    assert verify(tmp_path) == ["manifest_missing"]
