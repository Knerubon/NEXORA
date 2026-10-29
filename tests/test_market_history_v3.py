"""Corrective wave V3: regular-file verification, machine-derived fold ranges, research dataset
identity, TRUSTED/UNTRUSTED with an independent anchor, writer ownership, symlink safety, exact
stress counts and re-sealed fuzzing."""

from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nexora.market_history.clock import ClockAttestation, check_attestation
from nexora.market_history.extract import (
    DEVELOPMENT_STATUS,
    LOCK_NAME,
    UNIT_FIELDS,
    ExtractionRequest,
    _units,
    acquire_writer_lock,
    dataset_status,
    extract,
    load_anchor,
    make_anchor,
    read_chunk,
    release_writer_lock,
    require_trusted,
    usability_blockers,
    verify_dataset,
    verify_dataset_report,
)
from nexora.market_history.models import (
    HistoryError,
    InstrumentSpec,
    MetaField,
    RawTick,
    SourceDescriptor,
    canonical_json,
    sha256_hex,
)
from nexora.market_history.quality import QualityConfig

from tests.test_market_history import FakeSource, att, conv, mk
from tests.test_market_history_corrective import TICKS, codes, go, refuses, reseal, segs

H = 3_600_000
NOW = datetime(2026, 1, 1, tzinfo=UTC)
ROOT = Path(__file__).resolve().parent.parent


def need_symlinks(tmp: Path) -> None:
    try:
        os.symlink(tmp, tmp.parent / (tmp.name + "-probe"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


# ======================= F1: only regular files are evidence =======================
def first_chunk(d: Path) -> Path:
    name: str = json.loads((d / "manifest.json").read_text())["chunks"][0]["file"]
    return d / name


def test_directory_at_chunk_path_is_a_problem_not_a_silent_skip(tmp_path: Path) -> None:
    go(tmp_path)
    c = first_chunk(tmp_path)
    c.unlink()
    c.mkdir()
    assert "chunk_not_regular_file:" + c.name in verify_dataset(tmp_path)
    assert usability_blockers(tmp_path)
    assert not dataset_status(tmp_path).trusted


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs")
def test_fifo_at_chunk_path_is_refused_without_blocking(tmp_path: Path) -> None:
    go(tmp_path)
    c = first_chunk(tmp_path)
    c.unlink()
    os.mkfifo(c)
    assert "chunk_not_regular_file:" + c.name in verify_dataset(tmp_path)


def test_symlinked_chunk_and_manifest_are_refused(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    go(tmp_path / "d")
    d = tmp_path / "d"
    c = first_chunk(d)
    data = c.read_bytes()
    c.unlink()
    (tmp_path / "elsewhere").write_bytes(data)
    os.symlink(tmp_path / "elsewhere", c)
    assert "chunk_is_symlink:" + c.name in verify_dataset(d)
    c.unlink()
    c.write_bytes(data)
    m = d / "manifest.json"
    text = m.read_bytes()
    m.unlink()
    (tmp_path / "m.json").write_bytes(text)
    os.symlink(tmp_path / "m.json", m)
    assert codes(verify_dataset_report(d)) == ["manifest_is_symlink"]
    m.unlink()
    m.mkdir()
    assert codes(verify_dataset_report(d)) == ["manifest_not_regular_file"]


def test_read_chunk_refuses_non_regular_files(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    with pytest.raises(HistoryError):
        list(read_chunk(tmp_path / "dir"))


# ======================= F5: symlink safety on the write side =======================
def test_out_dir_symlink_is_refused_before_any_write(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "out"
    os.symlink(target, link)
    with pytest.raises(HistoryError, match="out_dir_is_symlink"):
        go(link)
    assert list(target.iterdir()) == []


def test_out_dir_that_is_a_file_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(HistoryError, match="out_dir_not_a_directory"):
        go(f)


def test_dangling_symlink_at_chunk_or_manifest_name_is_a_structured_error(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    d = tmp_path / "d"
    d.mkdir()
    os.symlink(d / "nowhere", d / "chunk-1000-3000.jsonl")
    with pytest.raises(HistoryError, match="chunk_exists_not_a_regular_file"):
        go(d)
    e = tmp_path / "e"
    e.mkdir()
    os.symlink(e / "nowhere", e / "manifest.json")
    with pytest.raises(HistoryError, match="manifest_exists_dataset_is_immutable"):
        go(e)


# ======================= F2: machine-derived clock fold =======================
FOLD = segs((0, 20 * H, 10800), (20 * H, None, 7200))  # raw repeats by 1 h around 20H


def test_fold_range_is_derived_from_the_segments_alone() -> None:
    assert FOLD.derived_fold_ranges() == ((19 * H, 21 * H),)
    assert FOLD.to_json()["derived_fold_ranges"] == [[19 * H, 21 * H]]
    assert segs((0, 100, 7200), (100, None, 10800)).derived_fold_ranges() == ()  # spring forward
    two = segs((0, 10, 10800), (10, 50, 7200), (50, 90, 3600), (90, None, 7200))
    assert two.derived_fold_ranges() == (
        (10 - 3_600_000, 10 + 3_600_000),
        (50 - 3_600_000, 50 + 3_600_000),
    )


@pytest.mark.parametrize(
    ("lo", "hi", "refused"),
    [
        (10 * H, 19 * H, False),  # ends exactly where the range starts
        (10 * H, 19 * H + 1, True),
        (19 * H, 20 * H, True),  # the review's accepted case: window == repeated hour
        (19 * H + 600_000, 19 * H + 1_800_000, True),  # wholly inside
        (20 * H, 21 * H, True),
        (19 * H, 21 * H, True),
        (21 * H - 1, 22 * H, True),
        (21 * H, 22 * H, False),  # starts exactly where the range ends
    ],
)
def test_windows_touching_the_derived_range_are_refused_even_with_empty_ambiguity(
    tmp_path: Path, lo: int, hi: int, refused: bool
) -> None:
    attestation = ClockAttestation("tz-report", "ops", 0, 50 * H)  # declares NO ambiguity
    req = ExtractionRequest("SYN", lo, hi, hi - lo)
    if refused:
        refuses(
            tmp_path / "o",
            "window_touches_derived_fold_range",
            req=req,
            conversion=FOLD,
            clock_attestation=attestation,
        )
    else:
        go(tmp_path / "o", FakeSource([]), req=req, conversion=FOLD, clock_attestation=attestation)


def test_attestation_cannot_waive_a_derived_fold(tmp_path: Path) -> None:
    waiver = ClockAttestation("tz-report", "ops", 0, 50 * H, ambiguous_raw_ranges=((0, 1),))
    with pytest.raises(HistoryError, match="window_touches_derived_fold_range"):
        check_attestation(waiver, FOLD, 19 * H, 20 * H)


def test_review_interleaved_ascending_fold_is_refused_when_the_conversion_has_the_fold(
    tmp_path: Path,
) -> None:
    """The M1 scenario with the fold present in the conversion: refused before the provider is
    reached, and no attestation (even one declaring no ambiguity) can waive it."""
    refuses(
        tmp_path / "o",
        "window_touches_derived_fold_range",
        req=ExtractionRequest("SYN", 19 * H, 20 * H, H),
        conversion=FOLD,
        clock_attestation=ClockAttestation("tz-report", "ops", 0, 50 * H),
    )


def test_single_segment_attestation_is_a_claim_not_proof(tmp_path: Path) -> None:
    """Residual risk, asserted so nobody mistakes it for a guarantee: with one segment there is
    nothing to derive a fold from, so a false attestation is accepted and only RECORDED."""
    m = go(tmp_path, conversion=conv(10800))
    assert m["clock_attestation"]["evidence_ref"] and m["development_status"] == "UNTRUSTED"
    assert m["time_conversion"]["derived_fold_ranges"] == []


def test_verify_recomputes_the_derived_ranges(tmp_path: Path) -> None:
    go(tmp_path)
    reseal(tmp_path, lambda m: m["time_conversion"].update(derived_fold_ranges=[[1, 2]]))
    assert "time_conversion_not_canonical" in verify_dataset(tmp_path)


def test_verify_rejects_a_dataset_whose_recorded_window_touches_a_fold(tmp_path: Path) -> None:
    go(tmp_path)
    reseal(
        tmp_path,
        lambda m: m.update(time_conversion=segs((0, 3000, 10800), (3000, None, 7200)).to_json()),
    )
    assert "clock_attestation_invalid:window_touches_derived_fold_range" in verify_dataset(tmp_path)


# ======================= F3: content hash vs research dataset id =======================
class Inst(FakeSource):
    def __init__(self, ticks: list[RawTick], *, source_version: str = "1", **fields: str) -> None:
        super().__init__(ticks)
        self.fields = fields
        self.source_version = source_version

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor("fixture", self.source_version, "synthetic_fixture")

    def instrument(self, symbol: str) -> InstrumentSpec:
        base = {"trade_tick_size": "0.01", "trade_contract_size": "100", "digits": "2"}
        base.update(self.fields)
        return InstrumentSpec(symbol, tuple(MetaField(k, "known", v) for k, v in base.items()))


def test_unit_changes_move_dataset_id_but_never_content_sha256(tmp_path: Path) -> None:
    a = go(tmp_path / "a", Inst(TICKS))
    bodies = b"".join((tmp_path / "a" / c["file"]).read_bytes() for c in a["chunks"])
    assert a["content_sha256"] == sha256_hex(bodies)  # canonical tick bytes only
    assert verify_dataset(tmp_path / "a") == []
    for label, source in (
        ("contract size", Inst(TICKS, trade_contract_size="1")),
        ("digits", Inst(TICKS, digits="3")),
        ("tick size", Inst(TICKS, trade_tick_size="0.1")),
        ("source version", Inst(TICKS, source_version="2")),
    ):
        b = go(tmp_path / label.replace(" ", "_"), source)
        assert b["content_sha256"] == a["content_sha256"], label
        assert b["dataset_id"] != a["dataset_id"], label
        assert b["manifest_sha256"] != a["manifest_sha256"], label


def test_time_symbol_and_window_are_identity(tmp_path: Path) -> None:
    base = go(tmp_path / "a", Inst(TICKS))
    assert (
        go(tmp_path / "b", Inst(TICKS), conversion=conv(7200))["dataset_id"] != base["dataset_id"]
    )
    other = ClockAttestation("different-evidence", "ops", 0, 10**9)
    assert (
        go(tmp_path / "c", Inst(TICKS), clock_attestation=other)["dataset_id"] != base["dataset_id"]
    )


def test_trading_rules_capabilities_retries_and_layout_are_not_identity(tmp_path: Path) -> None:
    base = go(tmp_path / "a", Inst(TICKS))
    swapped = go(
        tmp_path / "b", Inst(TICKS, swap_long="-9", volume_step="0.01", trade_stops_level="5")
    )
    assert swapped["dataset_id"] == base["dataset_id"]
    assert swapped["manifest_sha256"] != base["manifest_sha256"]  # still anchored
    rechunk = go(tmp_path / "c", Inst(TICKS), req=ExtractionRequest("SYN", 1000, 6000, 700))
    assert rechunk["dataset_id"] == base["dataset_id"]


def test_missing_unit_fields_are_recorded_unknown_not_defaulted(tmp_path: Path) -> None:
    m = go(tmp_path, Inst(TICKS))
    units = m["units"]["instrument_fields"]
    assert set(units) == set(UNIT_FIELDS)
    assert units["currency_profit"] == {"state": "unknown", "value": None}
    assert units["digits"] == {"state": "known", "value": "2"}
    assert "UTC" in m["units"]["time"].upper() and "price" in m["units"]


def test_tampering_with_units_or_instrument_is_detected_even_when_resealed(tmp_path: Path) -> None:
    go(tmp_path / "a", Inst(TICKS))
    reseal(tmp_path / "a", lambda m: m["units"]["instrument_fields"]["digits"].update(value="9"))
    assert "units_binding_mismatch" in verify_dataset(tmp_path / "a")
    go(tmp_path / "b", Inst(TICKS))

    def bump(m: dict[str, Any]) -> None:
        for f in m["instrument"]["fields"]:
            if f["name"] == "trade_contract_size":
                f["value"] = "1"

    reseal(tmp_path / "b", bump)
    assert "units_binding_mismatch" in verify_dataset(tmp_path / "b")


def test_units_helper_is_pure() -> None:
    spec = InstrumentSpec("S", (MetaField("digits", "known", "2"),)).to_json()
    assert _units(spec) == _units(json.loads(json.dumps(spec)))


# ======================= F7: TRUSTED needs an independently stored anchor =======================
def anchored(d: Path, m: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return make_anchor(m, recorded_by="rin", storage_ref="PR #92 comment", recorded_at=NOW, **kw)


def test_every_dataset_is_born_untrusted_and_the_manifest_cannot_grant_trust(
    tmp_path: Path,
) -> None:
    m = go(tmp_path)
    assert m["development_status"] == DEVELOPMENT_STATUS == "UNTRUSTED"
    s = dataset_status(tmp_path)
    assert s.status == "UNTRUSTED" and "no_independent_anchor" in s.reasons and not s.trusted
    assert verify_dataset(tmp_path) == []  # development use is fine; evidence use is not
    with pytest.raises(HistoryError, match="dataset_untrusted"):
        require_trusted(tmp_path, None)


def test_matching_v3_anchor_alone_no_longer_grants_trust(tmp_path: Path) -> None:
    """V4 supersedes V3 here: a hash-only anchor is a candidate, not custody evidence. The positive
    path (store + registry + independent approver) is covered in test_market_history_v4.py."""
    m = go(tmp_path / "d")
    anchor = anchored(tmp_path / "d", m)
    (tmp_path / "anchor.json").write_text(json.dumps(anchor))
    loaded = load_anchor(tmp_path / "anchor.json", dataset_dir=tmp_path / "d")
    s = dataset_status(tmp_path / "d", loaded)
    assert s.status == "UNTRUSTED" and not s.trusted
    assert "legacy_anchor_is_not_custody_evidence" in s.reasons
    assert not any(r.startswith("integrity:") for r in s.reasons)  # the anchor itself matched
    with pytest.raises(HistoryError, match="dataset_untrusted"):
        require_trusted(tmp_path / "d", loaded)


def test_resealed_forgery_is_untrusted_with_an_anchor_and_invisible_without_one(
    tmp_path: Path,
) -> None:
    m = go(tmp_path)
    anchor = anchored(tmp_path, m)

    def forge(x: dict[str, Any]) -> None:
        x["provenance"]["extractor"] = "someone-else"

    reseal(tmp_path, forge)
    assert verify_dataset(tmp_path) == []  # internally consistent: integrity alone cannot tell
    s = dataset_status(tmp_path, anchor)
    assert not s.trusted and "integrity:manifest_anchor_mismatch" in s.reasons


def test_forged_trust_claim_is_rejected_even_with_a_matching_anchor(tmp_path: Path) -> None:
    go(tmp_path)
    new_hash = reseal(tmp_path, lambda m: m.update(development_status="TRUSTED"))
    forged = json.loads((tmp_path / "manifest.json").read_text())
    s = dataset_status(tmp_path, anchored(tmp_path, forged))
    assert forged["manifest_sha256"] == new_hash
    assert not s.trusted and "integrity:manifest_claims_trust:TRUSTED" in s.reasons


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda a: a.update(manifest_sha256="zz" * 32), "anchor_malformed:manifest_sha256"),
        (lambda a: a.update(manifest_sha256="A" * 64), "anchor_malformed:manifest_sha256"),
        (lambda a: a.update(dataset_id=None), "anchor_malformed:dataset_id"),
        (lambda a: a.update(content_sha256=5), "anchor_malformed:content_sha256"),
        (lambda a: a.update(schema="other"), "anchor_malformed:schema"),
        (lambda a: a.update(storage_ref="  "), "anchor_malformed:storage_ref"),
        (lambda a: a.update(recorded_by=None), "anchor_malformed:recorded_by"),
        (lambda a: a.update(recorded_at="2026-01-01T00:00:00"), "anchor_malformed:recorded_at"),
        (lambda a: a.update(recorded_at="yesterday"), "anchor_malformed:recorded_at"),
        (lambda a: a.pop("symbol"), "anchor_malformed:symbol"),
    ],
)
def test_malformed_anchors_never_trust_and_never_raise(
    tmp_path: Path, mutation: Any, reason: str
) -> None:
    m = go(tmp_path)
    anchor = anchored(tmp_path, m)
    mutation(anchor)
    s = dataset_status(tmp_path, anchor)
    assert not s.trusted and reason in s.reasons
    hostile: list[Any] = [[], "text", 5, {}, {"schema": "nexora.market-history-anchor/1"}]
    for bad in hostile:
        assert not dataset_status(tmp_path, bad).trusted


@pytest.mark.parametrize("field", ["dataset_id", "content_sha256", "symbol"])
def test_anchor_must_match_every_bound_field(tmp_path: Path, field: str) -> None:
    m = go(tmp_path)
    anchor = anchored(tmp_path, m)
    anchor[field] = "f" * 64 if field != "symbol" else "OTHER"
    s = dataset_status(tmp_path, anchor)
    assert not s.trusted and f"anchor_mismatch:{field}" in s.reasons


def test_hard_defects_block_trust_even_with_a_valid_anchor(tmp_path: Path) -> None:
    crossed = [mk(1000), mk(1500, "100.2", "100.1"), mk(2000)]
    m = go(tmp_path, FakeSource(crossed), req=ExtractionRequest("SYN", 1000, 2500, 1500))
    s = dataset_status(tmp_path, anchored(tmp_path, m))
    assert not s.trusted and "hard_defect:crossed_quote_count" in s.reasons


def test_anchor_must_live_outside_the_dataset_folder(tmp_path: Path) -> None:
    m = go(tmp_path / "d")
    inside = tmp_path / "d" / "anchor.json"
    inside.write_text(json.dumps(anchored(tmp_path / "d", m)))
    with pytest.raises(HistoryError, match="anchor_inside_dataset"):
        load_anchor(inside, dataset_dir=tmp_path / "d")
    assert "unexpected_entry:anchor.json" in verify_dataset(tmp_path / "d")
    outside = tmp_path / "anchor.json"
    outside.write_text("{not json")
    with pytest.raises(HistoryError, match="anchor_unreadable"):
        load_anchor(outside, dataset_dir=tmp_path / "d")
    outside.write_text(json.dumps({"schema": "x"}))
    with pytest.raises(HistoryError, match="anchor_malformed"):
        load_anchor(outside, dataset_dir=tmp_path / "d")


def test_symlinked_anchor_file_is_refused(tmp_path: Path) -> None:
    need_symlinks(tmp_path)
    m = go(tmp_path / "d")
    real = tmp_path / "real.json"
    real.write_text(json.dumps(anchored(tmp_path / "d", m)))
    link = tmp_path / "link.json"
    os.symlink(real, link)
    with pytest.raises(HistoryError, match="anchor_unreadable"):
        load_anchor(link, dataset_dir=tmp_path / "d")


def test_make_anchor_requires_attribution_and_a_timezone(tmp_path: Path) -> None:
    m = go(tmp_path)
    with pytest.raises(HistoryError):
        make_anchor(m, recorded_by="rin", storage_ref="x", recorded_at=datetime(2026, 1, 1))
    with pytest.raises(HistoryError):
        make_anchor(m, recorded_by="", storage_ref="x", recorded_at=NOW)
    with pytest.raises(HistoryError):
        make_anchor(m, recorded_by="rin", storage_ref=" ", recorded_at=NOW)


# ======================= F4: single-writer ownership =======================
def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def write_lock(d: Path, **info: Any) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / LOCK_NAME).write_text(json.dumps(info))


def test_second_writer_is_refused_and_touches_nothing(tmp_path: Path) -> None:
    token = acquire_writer_lock(tmp_path)
    foreign = tmp_path / "chunk-1000-3000.jsonl.tmp-123-ab"
    foreign.write_bytes(b"in flight")
    with pytest.raises(HistoryError, match="writer_lock_held"):
        go(tmp_path)
    assert foreign.exists() and not (tmp_path / "manifest.json").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([foreign.name, LOCK_NAME])
    release_writer_lock(tmp_path, token)
    assert not (tmp_path / LOCK_NAME).exists()


def test_release_never_removes_someone_elses_lock(tmp_path: Path) -> None:
    acquire_writer_lock(tmp_path)
    release_writer_lock(tmp_path, "not-my-token")
    assert (tmp_path / LOCK_NAME).exists()


def test_lock_is_released_after_success_and_after_failure(tmp_path: Path) -> None:
    go(tmp_path / "ok")
    assert not (tmp_path / "ok" / LOCK_NAME).exists()

    class Boom(FakeSource):
        def ticks(self, *a: Any, **k: Any) -> list[RawTick]:
            raise HistoryError("boom")

    with pytest.raises(HistoryError, match="boom"):
        go(tmp_path / "bad", Boom([]))
    assert not (tmp_path / "bad" / LOCK_NAME).exists()


def test_v4_lock_record_of_a_dead_owner_is_free_because_the_os_lock_is_gone(
    tmp_path: Path,
) -> None:
    """V3 reaped by reading pid/host and renaming by name (racy). V4 never reaps: a record is free
    only because NO process holds the OS lock on it (the kernel dropped it at the owner's death)."""
    write_lock(
        tmp_path, format="nexora.writer-lock/2", pid=dead_pid(), host=socket.gethostname(),
        token="old",
    )  # fmt: skip
    go(tmp_path)
    assert verify_dataset(tmp_path) == []
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(LOCK_NAME)]


def test_v3_style_lock_record_is_never_reaped_even_with_a_dead_pid(tmp_path: Path) -> None:
    write_lock(tmp_path, pid=dead_pid(), host=socket.gethostname(), token="old")
    with pytest.raises(HistoryError, match="writer_lock_held"):
        go(tmp_path)  # a V3 writer never took an OS lock: V4 cannot prove it is gone
    assert (tmp_path / LOCK_NAME).exists()


@pytest.mark.parametrize(
    "info",
    [
        {"pid": os.getpid(), "host": socket.gethostname(), "token": "t"},  # live owner
        {"pid": 1, "host": "some-other-host", "token": "t"},  # foreign host: cannot judge
        {"pid": "x", "host": socket.gethostname(), "token": "t"},
        {"host": socket.gethostname()},
        [],
    ],
)
def test_live_foreign_or_ambiguous_locks_are_never_reaped(tmp_path: Path, info: Any) -> None:
    if isinstance(info, dict):
        write_lock(tmp_path, **info)
    else:
        (tmp_path / LOCK_NAME).write_text(json.dumps(info))
    with pytest.raises(HistoryError, match="writer_lock_held"):
        go(tmp_path)
    (tmp_path / LOCK_NAME).write_text("{garbage")
    with pytest.raises(HistoryError, match="writer_lock_held"):
        go(tmp_path)


def test_leftover_lock_and_stray_entries_fail_verification(tmp_path: Path) -> None:
    go(tmp_path)
    (tmp_path / LOCK_NAME).write_text("{}")
    (tmp_path / "notes.txt").write_text("x")
    problems = verify_dataset(tmp_path)
    assert (
        "writer_lock_present:" + LOCK_NAME in problems and "unexpected_entry:notes.txt" in problems
    )


def test_unique_temp_names_do_not_collide_with_a_planted_temp(tmp_path: Path) -> None:
    (tmp_path / f"chunk-1000-3000.jsonl.tmp-{os.getpid()}").write_bytes(b"planted")
    go(tmp_path)  # the stale planted temp is cleaned under the lock, not mistaken for the chunk
    assert verify_dataset(tmp_path) == []


RACER = textwrap.dedent(
    """
    import sys, time
    from datetime import UTC, datetime
    from pathlib import Path
    from nexora.market_history import extract as ex
    from nexora.market_history.quality import QualityConfig
    from tests.test_market_history import FakeSource, att, conv, mk

    class Slow(FakeSource):
        def ticks(self, sym, a, b, *, max_ticks):
            time.sleep(0.6)
            return super().ticks(sym, a, b, max_ticks=max_ticks)

    T = [mk(1000 + i * 500) for i in range(10)]
    try:
        when = datetime(2026, 1, 1, tzinfo=UTC)
        req = ex.ExtractionRequest("SYN", 1000, 6000, 2000)
        m = ex.extract(
            Slow(T), req, conv(), QualityConfig(5000), Path(sys.argv[1]), retrieved_at=when,
            clock_attestation=att(), protected_utc_ranges=(), sleep=lambda s: None,
        )
        print("OK", m["dataset_id"])
    except Exception as e:
        print("ERR", type(e).__name__, getattr(e, "code", ""))
    """
)


def test_two_real_processes_racing_exactly_one_wins_cleanly(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]),
    }
    procs = []
    for _ in range(2):
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", RACER, str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=ROOT,
                env=env,
            )  # fmt: skip
        )
    outs = [p.communicate(timeout=120)[0].strip().splitlines()[-1] for p in procs]
    assert sum(o.startswith("OK") for o in outs) == 1, outs
    loser = next(o for o in outs if not o.startswith("OK"))
    assert (
        loser == "ERR HistoryError writer_lock_held"
        or "manifest_exists_dataset_is_immutable" in loser
    )
    assert verify_dataset(tmp_path) == []


# ======================= F6: exact stress counts =======================
def build_stress(seed: int, n: int) -> tuple[list[RawTick], dict[str, int]]:
    """Deterministic defect injection where no two injected defects can interact, so every count
    is known by construction (independent of the module under test)."""
    rnd = random.Random(seed)
    ticks: list[RawTick] = []
    exp = dict(dup=0, same=0, back=0, cross=0, gaps=0, nonpos=0)
    t = 1000
    kind = "normal"  # what the previous tick was
    for _ in range(n):
        r = rnd.random()
        prev = ticks[-1] if ticks else None
        clean_prev = kind in ("normal", "gap")
        if prev is not None and clean_prev and r < 0.01:
            ticks.append(RawTick(prev.time_msc, prev.bid, prev.ask))
            exp["dup"] += 1
            kind = "dup"
        elif prev is not None and clean_prev and r < 0.02:
            ticks.append(RawTick(prev.time_msc, prev.bid, prev.ask + Decimal("0.5")))
            exp["same"] += 1
            kind = "same"
        elif prev is not None and clean_prev and r < 0.03:
            ticks.append(RawTick(t - 3, Decimal("100.00"), Decimal("100.10")))
            exp["back"] += 1
            kind = "back"
        elif r < 0.04:
            t += 10
            ticks.append(RawTick(t, Decimal("100.10"), Decimal("100.00")))
            exp["cross"] += 1
            kind = "cross"
        elif prev is not None and clean_prev and r < 0.05:
            t += 100_000
            ticks.append(RawTick(t, Decimal("100.00"), Decimal("100.10")))
            exp["gaps"] += 1
            kind = "gap"
        elif r < 0.055:
            t += 10
            ticks.append(RawTick(t, Decimal("0"), Decimal("1")))
            exp["nonpos"] += 1
            kind = "nonpos"
        else:
            t += 10
            ticks.append(RawTick(t, Decimal("100.00"), Decimal("100.10")))
            kind = "normal"
    return ticks, exp


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_stress_counts_are_exact(tmp_path: Path, seed: int) -> None:
    ticks, exp = build_stress(seed, 20_000)
    end = max(t.time_msc for t in ticks) + 1
    m = extract(
        FakeSource(ticks),
        ExtractionRequest("SYN", 1000, end, end),  # one chunk: file order == provider order
        conv(0),
        QualityConfig(50_000),
        tmp_path,
        retrieved_at=NOW,
        clock_attestation=att(),
        protected_utc_ranges=(),
        sleep=lambda _s: None,
    )
    q = m["quality"]
    assert q["tick_count"] == len(ticks)
    assert q["exact_duplicate_count"] == exp["dup"]
    assert q["same_timestamp_different_quote_count"] == exp["same"]
    assert q["non_monotonic_count"] == exp["back"]
    assert q["crossed_quote_count"] == exp["cross"]
    assert q["gap_count"] == exp["gaps"]
    assert q["nonpositive_price_count"] == exp["nonpos"]
    assert q["nonfinite_quote_count"] == 0
    assert min(exp.values()) > 0, "every defect class must actually be exercised"
    assert verify_dataset(tmp_path) == []
    assert set(usability_blockers(tmp_path)) == {
        "hard_defect:non_monotonic_count",
        "hard_defect:crossed_quote_count",
        "hard_defect:nonpositive_price_count",
    }


# ======================= re-sealed fuzz =======================
def structured_mutate(obj: Any, rnd: random.Random) -> None:
    junk = [None, [], {}, "", -1, 1.5, "x", True, 10**40, [None], {"a": 1}, "0" * 64, 0]
    if isinstance(obj, dict) and obj:
        key = rnd.choice(sorted(obj))
        roll = rnd.random()
        if roll < 0.2:
            del obj[key]
        elif roll < 0.55 or not isinstance(obj[key], dict | list):
            obj[key] = rnd.choice(junk)
        else:
            structured_mutate(obj[key], rnd)
    elif isinstance(obj, list) and obj:
        i = rnd.randrange(len(obj))
        if rnd.random() < 0.4 or not isinstance(obj[i], dict | list):
            obj[i] = rnd.choice(junk)
        else:
            structured_mutate(obj[i], rnd)


def seal(d: Path, m: dict[str, Any], *, identity: bool) -> None:
    from nexora.market_history.extract import _identity

    m.pop("manifest_sha256", None)
    if identity:
        try:
            m["dataset_id"] = sha256_hex(canonical_json(_identity(m)))
        except Exception:
            pass
    try:
        m["manifest_sha256"] = sha256_hex(canonical_json(m))
    except Exception:
        m["manifest_sha256"] = "0" * 64
    (d / "manifest.json").chmod(0o644)
    (d / "manifest.json").write_text(json.dumps(m))


def test_resealed_mutants_never_raise_and_are_never_trusted_against_an_anchor(
    tmp_path: Path,
) -> None:
    original = go(tmp_path, Inst(TICKS))
    anchor = anchored(tmp_path, original)
    text = (tmp_path / "manifest.json").read_text()
    rnd = random.Random(20261020)
    base = json.loads(text)
    base.pop("manifest_sha256")
    quiet_stale: set[str] = set()  # edits that verified clean while dataset_id was left stale
    quiet_full: set[str] = set()  # ... and with every self-hash recomputed (a competent forger)
    checked = 0
    for i in range(1500):
        m = json.loads(text)
        structured_mutate(m, rnd)
        m.pop("manifest_sha256", None)  # re-sealing restores it: that mutant is the original
        if m == base:
            continue
        full = i % 2 == 0
        seal(tmp_path, m, identity=full)
        if m["manifest_sha256"] == original["manifest_sha256"]:
            continue  # re-sealing repaired the only edit (e.g. a mutated dataset_id)
        checked += 1
        report = verify_dataset_report(tmp_path)
        assert all(p.code != "verify_internal_error" for p in report), (m, report)
        assert not dataset_status(tmp_path, anchor).trusted, m  # the anchor catches everything
        assert not dataset_status(tmp_path, None).trusted
        if not report:
            changed = {k for k in {*m, *base} if k != "manifest_sha256" and m.get(k) != base.get(k)}
            (quiet_full if full else quiet_stale).update(changed)
    assert checked > 1000
    # Stale dataset_id: only operational facts (outside identity, covered by manifest_sha256 and
    # the anchor) may be edited without an internal inconsistency being reported.
    assert quiet_stale <= {
        "provenance",
        "chunks",
        "chunk_ms",
        "max_chunk_ticks",
        "capability_profile",
        "instrument",
        "filesystem_capabilities",
        # Round 3: the quality report gained keys, so the seeded RNG now also reaches
        # quality.config.max_gap_ms. A threshold edit that changes no gap verdict is internally
        # consistent (the recompute uses the recorded threshold); it is not identity, and the
        # manifest hash bound into the signed approval exposes it (pre-existing, not new).
        "quality",
    }, quiet_stale
    # A forger who recomputes every self-hash defeats integrity checks by construction: that is
    # exactly why TRUSTED requires the anchor, which the assertions above prove.
    assert quiet_full, "expected at least one fully re-sealed edit to pass internal checks"


def test_resealed_edits_to_identity_fields_always_break_internal_consistency(
    tmp_path: Path,
) -> None:
    """With dataset_id NOT recomputed, every identity-field edit is detected without any anchor."""
    go(tmp_path, Inst(TICKS))
    text = (tmp_path / "manifest.json").read_text()
    rnd = random.Random(7)
    identity_keys = ["symbol", "window_raw_msc", "time_conversion", "clock_attestation",
                     "content_sha256", "tick_count", "source", "units"]  # fmt: skip
    for _ in range(400):
        m = json.loads(text)
        key = rnd.choice(identity_keys)
        sub = {key: m[key]}
        structured_mutate(sub, rnd)
        if sub.get(key, "<deleted>") == m[key]:
            continue
        if key in sub:
            m[key] = sub[key]
        else:
            del m[key]
        seal(tmp_path, m, identity=False)
        assert verify_dataset_report(tmp_path), key


# ======================= CLI: anchor and status =======================
def _cli(name: str) -> Any:
    import importlib.util

    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extract_cli_validates_anchor_arguments_before_attaching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_market_history_corrective import _cli_files

    cli = _cli("extract_market_history.py")

    class Boom:
        def __init__(self) -> None:
            raise AssertionError("terminal attached before validation")

    monkeypatch.setattr(cli, "Mt5ReadOnlyHistorySource", Boom)
    argv = _cli_files(tmp_path)
    monkeypatch.setattr(sys, "argv", [*argv, "--anchor-out", str(tmp_path / "a.json")])
    with pytest.raises(SystemExit):  # all three go together
        cli.main()
    inside = [*argv, "--anchor-out", str(tmp_path / "out" / "a.json"),
              "--anchor-recorded-by", "rin", "--anchor-storage-ref", "PR"]  # fmt: skip
    monkeypatch.setattr(sys, "argv", inside)
    with pytest.raises(HistoryError, match="anchor_inside_dataset"):
        cli.main()
    (tmp_path / "exists.json").write_text("{}")
    taken = [*argv, "--anchor-out", str(tmp_path / "exists.json"),
             "--anchor-recorded-by", "rin", "--anchor-storage-ref", "PR"]  # fmt: skip
    monkeypatch.setattr(sys, "argv", taken)
    with pytest.raises(HistoryError, match="anchor_file_exists"):
        cli.main()


def test_verify_cli_reports_untrusted_without_custody_and_for_a_hash_only_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _cli("verify_market_history.py")
    m = go(tmp_path / "d")

    def run(*extra: str) -> tuple[int, dict[str, Any]]:
        monkeypatch.setattr(sys, "argv", ["v", "--dataset", str(tmp_path / "d"), *extra])
        code = cli.main()
        return code, json.loads(capsys.readouterr().out)

    code, out = run()
    # N-03: a cleanly verified but UNTRUSTED dataset is exit 1, never 0 (0 means TRUSTED only)
    assert (code, out["status"], out["trusted"]) == (1, "UNTRUSTED", False)
    assert "no_independent_anchor" in out["status_reasons"]
    assert run("--require-trusted")[0] == 1
    good = tmp_path / "anchor.json"
    good.write_text(json.dumps(anchored(tmp_path / "d", m)))
    code, out = run("--anchor", str(good), "--require-trusted")
    assert code == 1 and out["status"] == "UNTRUSTED"  # a hash-only anchor is not custody
    assert "legacy_anchor_is_not_custody_evidence" in out["status_reasons"]
    wrong = anchored(tmp_path / "d", m)
    wrong["manifest_sha256"] = "0" * 64
    good.write_text(json.dumps(wrong))
    code, out = run("--anchor", str(good))
    assert code == 1 and "integrity:manifest_anchor_mismatch" in out["status_reasons"]
    code, out = run("--anchor", str(tmp_path / "d" / "manifest.json"))
    assert code == 2 and out["status_reasons"] == ["anchor_inside_dataset"]


def test_sample_manifest_is_the_current_schema_and_not_stale(tmp_path: Path) -> None:
    sample = json.loads(
        (ROOT / "docs/research/market-history/sample-manifest.synthetic.json").read_text()
    )
    assert sample.pop("LABEL") == "SYNTHETIC_FIXTURE_NOT_REAL_MARKET_DATA"
    assert sample == go(tmp_path / "x")
