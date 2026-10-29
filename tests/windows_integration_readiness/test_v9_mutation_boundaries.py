"""Wave V9: boundary tests from the mutation sweep (each pins a condition a mutant survived).

Every test states which comparison or clause it pins; the sweep itself is reported in the PR."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.windows_integration_readiness import custody_gate as cg
from tests.windows_integration_readiness import execution_contract as ec
from tests.windows_integration_readiness.test_evidence_validator import (
    check,
    real_shaped_pass,
    synthetic,
)
from tests.windows_integration_readiness.test_execution_contract import (
    SHA,
    _code,
    argv,
    plan,
)
from tests.windows_integration_readiness.test_r4_execution_contract import _fake_repo, needs_dir_fd
from tests.windows_integration_readiness.test_v4_custody_compat import (
    ENTRY_SHA,
    HEAD_SHA,
    _approved_baseline,
    good,
)
from tests.windows_integration_readiness.vectors import TICKS_SYNTH


def reasons(**changes: Any) -> tuple[str, ...]:
    return cg.trust_gate(good(**changes)).reasons


# ---- approval_seq against the pinned head: the boundary is seq, not seq + 1 ----------------------


def test_approval_at_the_pinned_head_is_inside_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    d = cg.trust_gate(
        good(approval_seq=7, candidate_approval_seqs=(7,), approval_entry_sha256=HEAD_SHA)
    )
    assert d.outcome == cg.RECORDABLE and d.reasons == ()  # positive control for the boundary


def test_approval_one_past_the_pinned_head_is_outside_the_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_baseline(monkeypatch)
    r = reasons(approval_seq=8, candidate_approval_seqs=(8,), approval_entry_sha256=ENTRY_SHA)
    assert "approval_outside_pinned_chain" in r  # a `> seq + 1` mutant would miss exactly this
    assert "approval_not_covered_by_pin" in r


@pytest.mark.parametrize("beyond", [8, 9, 100])
def test_approval_beyond_the_pin_is_never_recordable(
    monkeypatch: pytest.MonkeyPatch, beyond: int
) -> None:
    _approved_baseline(monkeypatch)
    d = cg.trust_gate(good(approval_seq=beyond, candidate_approval_seqs=(beyond,)))
    assert d.outcome != cg.RECORDABLE


def test_approval_at_the_head_must_be_the_head_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    r = reasons(approval_seq=7, candidate_approval_seqs=(7,), approval_entry_sha256=ENTRY_SHA)
    assert "approval_entry_differs_from_pinned_head" in r
    assert "approval_entry_differs_from_signed_pin" in r


# ---- extractor sha clause ------------------------------------------------------------------------


def test_empty_extractor_sha_stops_even_when_checklist_and_run_agree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_baseline(monkeypatch)
    d = cg.trust_gate(good(checklist_extractor_sha="", run_extractor_sha=""))
    assert d.outcome == cg.STOP
    assert (
        "extractor_sha_changed_since_checklist" in d.reasons
    )  # the `not run_extractor_sha` clause


def test_extractor_sha_difference_alone_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    d = cg.trust_gate(good(run_extractor_sha="f" * 40))
    assert d.outcome == cg.STOP and "extractor_sha_changed_since_checklist" in d.reasons


# ---- manifest schema -----------------------------------------------------------------------------


@pytest.mark.parametrize("schema", ["nexora.market-history/3", "nexora.market-history/5", "", "x"])
def test_only_the_candidate_manifest_schema_is_accepted(
    monkeypatch: pytest.MonkeyPatch, schema: str
) -> None:
    _approved_baseline(monkeypatch)
    assert f"legacy_or_unknown_manifest_schema:{schema}" in reasons(manifest_schema=schema)
    assert not any(r.startswith("legacy_or_unknown") for r in reasons())


# ---- head_equals_sha contradiction ---------------------------------------------------------------


def _real(**checkout: Any) -> dict[str, Any]:
    r = real_shaped_pass()
    r["checkout"].update(checkout)
    return r


def test_head_equals_sha_true_with_a_different_head_is_a_contradiction() -> None:
    r = _real(head="f" * 40, head_equals_sha=True)
    assert "head_equals_sha_contradicts_recorded_head" in check(r).errors


def test_head_equals_sha_false_with_the_same_head_is_a_contradiction() -> None:
    r = synthetic()
    r["checkout"]["head_equals_sha"] = False
    r["checkout"]["head"] = r["identity"]["extractor_sha"]
    assert "head_equals_sha_contradicts_recorded_head" in check(r).errors


def test_head_equals_sha_consistent_values_have_no_contradiction() -> None:
    r = synthetic()
    same = r["identity"]["extractor_sha"]
    r["checkout"].update(head=same, head_equals_sha=True)
    assert "head_equals_sha_contradicts_recorded_head" not in check(r).errors
    r["checkout"].update(head="f" * 40, head_equals_sha=False)
    assert "head_equals_sha_contradicts_recorded_head" not in check(r).errors


# ---- candidate_approval_seqs: no default, no empty list ------------------------------------------


def test_candidate_approval_seqs_has_no_permissive_default() -> None:
    assert cg.CustodyBundle.__dataclass_fields__["candidate_approval_seqs"].default is None


def test_omitted_candidate_approval_seqs_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    assert "candidate_approval_seqs_not_recorded" in reasons(candidate_approval_seqs=None)


def test_empty_candidate_approval_seqs_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    d = cg.trust_gate(good(candidate_approval_seqs=()))
    assert d.outcome != cg.RECORDABLE
    assert "candidate_approval_seqs_not_recorded" in d.reasons  # empty counts as omitted


def test_validator_rejects_an_empty_candidate_approval_seqs_list() -> None:
    r = synthetic()
    r["custody"]["candidate_approval_seqs"] = []
    assert check(r).errors  # seq 5 is not among no approvals, and the schema wants a recorded list


# ---- window and chunk bounds ---------------------------------------------------------------------


def _bounds(tmp_path: Path, lo: int, hi: int, chunk: int) -> Any:
    return plan(
        tmp_path / "out",
        argv(
            tmp_path / "out",
            **{"--from-raw-msc": str(lo), "--to-raw-msc": str(hi), "--chunk-ms": str(chunk)},
        ),
    )


@pytest.mark.parametrize(("lo", "hi"), [(1000, 1000), (1000, 999), (1000, 0)])
def test_empty_or_reversed_window_is_refused(tmp_path: Path, lo: int, hi: int) -> None:
    assert _code(lambda: _bounds(tmp_path, lo, hi, 1000)) == "invalid_window_or_chunk"


@pytest.mark.parametrize("chunk", [0, -1, -1000])
def test_non_positive_chunk_is_refused(tmp_path: Path, chunk: int) -> None:
    assert _code(lambda: _bounds(tmp_path, 1000, 5000, chunk)) == "invalid_window_or_chunk"


@pytest.mark.parametrize(("lo", "hi", "chunk"), [(1000, 1001, 1), (1000, 1001, 1000), (0, 5, 5)])
def test_smallest_valid_window_and_chunk_are_accepted(
    tmp_path: Path, lo: int, hi: int, chunk: int
) -> None:
    p = _bounds(tmp_path, lo, hi, chunk)
    assert p.window_raw_msc == (lo, hi) and p.chunk_ms == chunk


# ---- descriptor ancestry: an ancestor moved into the checkout ------------------------------------


@needs_dir_fd
def test_ancestor_moved_into_the_checkout_with_a_symlink_left_behind(tmp_path: Path) -> None:
    """Planned parent is two levels below a staging dir outside the checkout. The staging dir (an
    ANCESTOR, not the parent) is renamed into the checkout and a symlink is left at its old place.
    The parent's own inode is unchanged, so only the descriptor ancestry walk can refuse; a
    `_fd_inside` that always answers False writes into the checkout."""
    repo = _fake_repo(tmp_path)
    staging = tmp_path / "staging"
    (staging / "nested").mkdir(parents=True)
    p = plan(staging / "nested" / "out", repo_root=repo)
    staging.rename(repo / "data" / "staging")
    staging.symlink_to(repo / "data" / "staging")
    code = _code(lambda: ec.run_mock(p, TICKS_SYNTH, extractor_sha=SHA, operator="op"))
    assert code == "output_dir_inside_checkout"
    assert not (repo / "data" / "staging" / "nested" / "out").exists()


@needs_dir_fd
def test_fd_inside_is_true_for_a_descendant_and_false_for_an_outsider(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    rid = (os.stat(repo).st_dev, os.stat(repo).st_ino)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    inner, outer = os.open(repo / "data", flags), os.open(other, flags)
    try:
        assert ec._fd_inside(inner, rid) is True
        assert ec._fd_inside(outer, rid) is False
    finally:
        os.close(inner)
        os.close(outer)


# ---- second sweep: reasons only one clause emits ---------------------------------------------


def test_verifier_without_require_trusted_has_its_own_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_baseline(monkeypatch)
    no_flag = tuple(a for a in good().verify_argv if a != "--require-trusted")
    assert "verifier_run_without_require_trusted" in reasons(verify_argv=no_flag)
    assert "verifier_run_without_require_trusted" not in reasons()


def test_trusted_status_with_a_nonzero_exit_is_a_contradiction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_baseline(monkeypatch)
    r = reasons(verifier_exit_code=1)
    assert "verifier_trusted_contradicts_recorded_evidence" in r
    assert "verifier_did_not_report_trusted" in r
    assert "verifier_trusted_contradicts_recorded_evidence" not in reasons()


def test_pin_ahead_of_the_store_is_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_baseline(monkeypatch)
    r = reasons(pin_seq=8, pinned_head_seq=8, store_head_seq_at_verification=7)
    assert "pin_ahead_of_store_truncated" in r
    assert "pin_ahead_of_store_truncated" not in reasons(pin_seq=7)


@pytest.mark.parametrize("present", [("--anchor-out",), ("--anchor-out", "--anchor-recorded-by")])
def test_partial_anchor_trio_is_refused_with_its_own_code(
    tmp_path: Path, present: tuple[str, ...]
) -> None:
    extra = {
        "--anchor-out": str(tmp_path / "anchors" / "a.json"),
        "--anchor-recorded-by": "synthetic-operator",
        "--anchor-storage-ref": "synthetic-ref",
    }
    args = argv(tmp_path / "out", **{k: extra[k] for k in present})
    assert _code(lambda: plan(tmp_path / "out", args)) == "anchor_arguments_incomplete"


@pytest.mark.parametrize("flag", ["--operator-key", "--candidate-out", "--anchor-out"])
def test_custody_files_inside_the_checkout_are_refused(tmp_path: Path, flag: str) -> None:
    repo = _fake_repo(tmp_path)
    over: dict[str, str | None] = {
        "--operator": "synthetic-operator",
        "--operator-key": str(tmp_path / "keys" / "op.key"),
        "--candidate-out": str(tmp_path / "cand" / "c.json"),
    }
    over[flag] = str(repo / "data" / "inside.file")
    if flag == "--anchor-out":
        over["--anchor-recorded-by"] = "synthetic-operator"
        over["--anchor-storage-ref"] = "synthetic-ref"
    args = argv(tmp_path / "out", **over)
    code = _code(lambda: plan(tmp_path / "out", args, repo_root=repo))
    assert code == f"{flag}_inside_checkout"


def test_validator_names_an_approval_missing_from_the_candidate_approvals() -> None:
    r = synthetic()
    r["custody"]["candidate_approval_seqs"] = [r["custody"]["approval_seq"] + 1]
    assert "approval_seq_not_among_candidate_approval_seqs" in check(r).errors


def test_smallest_pin_still_covers_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """pin_seq 1 is the lowest valid pin: an approval beyond it is still outside the pin."""
    _approved_baseline(monkeypatch)
    r = reasons(
        pin_seq=1,
        pinned_head_seq=1,
        store_head_seq_at_verification=1,
        approval_seq=2,
        candidate_approval_seqs=(2,),
    )
    assert "approval_not_covered_by_pin" in r
