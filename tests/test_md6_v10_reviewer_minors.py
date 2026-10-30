"""Regression tests for the three MINORs of the independent PR #97 review (Reviewer C, a892ac7).

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
models; not runtime enforcement. Production runtime enforcement: NOT VERIFIED.

* m-1  the Quant authority verifier's answer was coerced with ``bool()``: only the exact bool True
  may approve a declared set;
* m-2  (R3-m2) an exposure entry (view or unregistered run) with empty ``targets`` or other missing
  identity fields was silently non-exposing: it must fail closed (UNVERIFIED);
* m-3  (R3-m4) a WINDOW quarantine entry's ``dataset_id`` label decided whether a sibling window
  that honestly declares its containing dataset as parent was blocked.

Every test here fails on a892ac7 and passes on the fix, except these, which pass on both: the
tests named ``*_control_*``, ``[blank-instrument]`` (already UNVERIFIED through the lineage
instrument check) and the ``ds-B-w2`` label case of m-3 (a window-specific label, as in the round-2
N4 test). All identities, windows, ticks and timestamps are SYNTHETIC. No MD-6 threshold, Quant
policy or real data appears anywhere.
"""

from __future__ import annotations

import pytest

from tests import md6_quarantine_reference as C
from tests.md6_exposure_reference import Outcome, ProtectedStatisticsView, Target, append
from tests.test_md6_exposure_lineage import Book, tgt
from tests.test_md6_exposure_n1_remediation import _quarantine, _ticks
from tests.test_md6_v9_structural import (
    _evaluate_disjoint,
    _register_disjoint,
    classify,
    classify_set,
    declare,
    syn_verifier,
)

# ---- m-1: verifier answer must be exactly True -------------------------------------------------


def _verified_set_book() -> Book:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b"))
    for v in ("a", "b"):
        _evaluate_disjoint(book, v)
    return book


@pytest.mark.parametrize(
    "answer",
    ["False", "denied", {"approved": False}, 1, "True", [True], None, 0.0],
    ids=["str-False", "str-denied", "record", "int-1", "str-True", "list", "None", "float"],
)
def test_m1_non_bool_verifier_answer_is_never_an_approval(answer: object) -> None:
    def verifier(_a: str, _c: str) -> bool:
        return answer  # type: ignore[return-value]  # deliberately violates the contract

    result = classify_set(_verified_set_book(), "D-1", verifier)
    assert result.outcome is Outcome.UNVERIFIED
    assert not result.outcome.gate_eligible
    assert any("not bool" in r for r in result.reasons)


def test_m1_control_exact_true_qualifies_and_false_stays_pending() -> None:
    book = _verified_set_book()
    assert classify_set(book, "D-1", syn_verifier).outcome is Outcome.INDEPENDENT
    assert classify_set(book, "D-1", lambda _a, _c: False).outcome is (
        Outcome.PENDING_QUANT_AUTHORITY
    )


# ---- m-2 (R3-m2): corrupt exposure entries fail closed -----------------------------------------

W = (tgt(0, 100),)  # SYNTHETIC window [0, 100)


def _book_with_view(view_id: str, targets: tuple[Target, ...]) -> Book:
    book = Book()
    book.register("P1", W)
    ts = book._tick()
    book.ledger = append(
        book.ledger,
        ProtectedStatisticsView(view_id, targets, "VIEWER-SYN", ts),
        ts,
    )
    book.evaluate("E1", "P1", W)
    return book


def test_m2_view_with_empty_targets_makes_later_evaluation_unverified() -> None:
    result = classify(_book_with_view("V-1", ()), "E1")
    assert result.outcome is Outcome.UNVERIFIED
    assert any("missing targets" in r for r in result.reasons)


def test_m2_unregistered_run_with_empty_targets_makes_later_evaluation_unverified() -> None:
    book = Book()
    book.register("P1", W)
    book.evaluate("E-peek", None, ())
    book.evaluate("E1", "P1", W)
    result = classify(book, "E1")
    assert result.outcome is Outcome.UNVERIFIED
    assert any("missing targets" in r for r in result.reasons)


def test_m2_empty_targets_view_before_registration_also_fails_closed() -> None:
    book = Book()
    ts = book._tick()
    book.ledger = append(book.ledger, ProtectedStatisticsView("V-1", (), "VIEWER-SYN", ts), ts)
    book.register("P1", W)
    book.evaluate("E1", "P1", W)
    assert classify(book, "E1").outcome is Outcome.UNVERIFIED


@pytest.mark.parametrize(
    "bad",
    [
        tgt(500, 600, instrument=""),
        tgt(500, 600, dataset_id=""),
    ],
    ids=["blank-instrument", "blank-dataset"],
)
def test_m2_view_with_missing_target_ids_fails_closed(bad: Target) -> None:
    assert classify(_book_with_view("V-1", (bad,)), "E1").outcome is Outcome.UNVERIFIED


def test_m2_view_with_blank_id_fails_closed() -> None:
    disjoint = (tgt(500, 600),)
    assert classify(_book_with_view("", disjoint), "E1").outcome is Outcome.UNVERIFIED


def test_m2_control_well_formed_disjoint_view_keeps_independent() -> None:
    assert classify(_book_with_view("V-1", (tgt(500, 600),)), "E1").outcome is Outcome.INDEPENDENT


def test_m2_control_corrupt_entry_after_the_evaluation_is_not_consulted() -> None:
    book = Book()
    book.register("P1", W)
    book.evaluate("E1", "P1", W)
    ts = book._tick()
    book.ledger = append(book.ledger, ProtectedStatisticsView("V-1", (), "VIEWER-SYN", ts), ts)
    assert classify(book, "E1").outcome is Outcome.INDEPENDENT


# ---- m-3 (R3-m4): sibling eligibility must not depend on a WINDOW entry's label ----------------

RECORDS = _ticks(12)
W1, W2, W3 = RECORDS[:4], RECORDS[4:8], RECORDS[8:]
DS_B = C.LineageRef("ds-B", C.content_identity(RECORDS).ordered_content_hash)


@pytest.mark.parametrize("label", ["ds-B", "ds-B-w2"])
@pytest.mark.parametrize("name,part", [("W1", W1), ("W3", W3)])
def test_m3_sibling_declaring_its_containing_dataset_is_not_blocked_by_window_label(
    label: str, name: str, part: tuple[C.TickRecord, ...]
) -> None:
    q = _quarantine(W2, dataset_id=label, scope="WINDOW", window_id="W2")
    honest = C.check_reentry(
        C.CandidateDataset(f"ds-B-{name}", part, declared_parents=(DS_B,)), q.register, q.anchor
    )
    silent = C.check_reentry(C.CandidateDataset(f"ds-B-{name}", part), q.register, q.anchor)
    assert honest.outcome == silent.outcome == "NOT_KNOWN"
    assert honest.may_proceed_to_part_a


def test_m3_control_window_content_still_blocks_by_content() -> None:
    q = _quarantine(W2, dataset_id="ds-B", scope="WINDOW", window_id="W2")
    whole = C.check_reentry(C.CandidateDataset("ds-B", RECORDS), q.register, q.anchor)
    assert (whole.outcome, whole.relation) == ("DERIVED_FROM_QUARANTINED", "SUPERSET")
    exact = C.check_reentry(C.CandidateDataset("relabelled", W2), q.register, q.anchor)
    assert exact.outcome == "QUARANTINED_MATCH"
    # A candidate that declares the quarantined window's own content as parent is still blocked.
    w2_ref = C.LineageRef("anything", C.content_identity(W2).ordered_content_hash)
    child = C.check_reentry(
        C.CandidateDataset("derived", _ticks(4, start=90_000), declared_parents=(w2_ref,)),
        q.register,
        q.anchor,
    )
    assert (child.outcome, child.relation) == ("DERIVED_FROM_QUARANTINED", "DECLARED_LINEAGE")


def test_m3_control_dataset_scope_label_lineage_unchanged() -> None:
    q = _quarantine(W2, dataset_id="ds-B", scope="DATASET")
    result = C.check_reentry(
        C.CandidateDataset("ds-B-W1", W1, declared_parents=(DS_B,)), q.register, q.anchor
    )
    assert (result.outcome, result.relation) == ("DERIVED_FROM_QUARANTINED", "DECLARED_LINEAGE")
