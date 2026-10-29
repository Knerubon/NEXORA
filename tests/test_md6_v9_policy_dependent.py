"""Wave V9 remediation tests that DEPEND ON UNRESOLVED QUANT / GOVERNANCE POLICY.

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
models; not runtime enforcement. Production runtime enforcement: NOT VERIFIED.

READ THIS BEFORE TRUSTING ANY TEST HERE. Unlike ``tests/test_md6_v9_structural.py`` (behaviour that
follows from rules already in the proposal), the expected values below encode a choice between
alternatives that only a human Quant / governance owner may make. Each section names the open
decision. A passing test in this file is NOT evidence that the policy is right:

* QE-9  (F-1)  may an issued outcome be revised by later events? Default model = B (monotone
               non-improving, whole-ledger fail-closed). Alternative A (frozen prefix) is isolated
               in ``tests/md6_exposure_frozen_prefix_proposal.py``, BLOCKED pending that decision;
* QE-7  (F-5)  selection across disjoint footprints, adjacent windows and correlated instruments is
               ungoverned: the tests CHARACTERIZE today's behaviour, they do not endorse it;
* QE-1  (F-6)  anchors carry no time; no time authority exists;
* QE-8  (F-7)  authentication of ledger appenders; fail-closed denial-of-evidence side effects;
* QE-10 (F-4)  instrument equivalence and time/price mappings are undefined: ``xfail(strict=True)``
               marks the evasions that remain, so a future fix flips them loudly.

All identities, windows, ticks and timestamps are SYNTHETIC. No threshold appears anywhere.
"""

from __future__ import annotations

import random
from dataclasses import fields, replace

import pytest

from tests import md6_cross_track_reference as X
from tests import md6_quarantine_reference as C
from tests.md6_exposure_frozen_prefix_proposal import classify_evaluation_prefix_frozen
from tests.md6_exposure_reference import (
    Assessment,
    ContentIdentity,
    DatasetLineage,
    LedgerAnchor,
    LineageRegistry,
    Outcome,
    classify_multiplicity_set,
    verify_chain,
)
from tests.test_md6_exposure_lineage import INST_A, INST_B, LINEAGE, ROOT_B, Book, tgt
from tests.test_md6_exposure_n1_remediation import (
    BASE,
    SYN_ALIAS,
    SYN_INST,
    _book_for,
    _target,
    _ticks,
)
from tests.test_md6_v9_structural import (
    INVALID_APPENDS,
    SEEDS,
    _evaluate_disjoint,
    _independent_first_run,
    _random_valid_book,
    _register_disjoint,
    classify,
    declare,
)

# ================================================================================================
# QE-9 / F-1: alternative A (frozen prefix), side by side with the default
# ================================================================================================


def frozen(book: Book, eval_id: str, lineage: LineageRegistry = LINEAGE) -> Assessment:
    return classify_evaluation_prefix_frozen(
        (book.ledger,), book.anchors(), lineage, eval_id
    ).assessment


@pytest.mark.parametrize("name", sorted(INVALID_APPENDS))
def test_qe9_alternative_a_later_invalid_append_does_not_change_an_earlier_outcome(
    name: str,
) -> None:
    book = _independent_first_run()
    INVALID_APPENDS[name](book)
    assert verify_chain(book.ledger) != ()
    result = classify_evaluation_prefix_frozen((book.ledger,), book.anchors(), LINEAGE, "E1")
    assert result.assessment.outcome is Outcome.INDEPENDENT
    assert result.ledger_problems, "the integrity alarm must still be raised"
    # Default semantics B, same input: voided.
    assert classify(book, "E1").outcome is Outcome.UNVERIFIED


@pytest.mark.parametrize("name", sorted(INVALID_APPENDS))
def test_qe9_alternative_a_never_grants_eligibility_to_the_invalid_entry_itself(name: str) -> None:
    """A later invalid append is never promoted: evaluations at/after the defect are UNVERIFIED."""
    book = _independent_first_run()
    INVALID_APPENDS[name](book)
    book.register("P-after", (tgt(800, 850),), family="FAM-after")
    book.evaluate("E-after", "P-after", (tgt(800, 850),), family="FAM-after")
    result = classify_evaluation_prefix_frozen((book.ledger,), book.anchors(), LINEAGE, "E-after")
    assert result.assessment.outcome is Outcome.UNVERIFIED
    assert result.ledger_problems


def test_qe9_alternative_a_invalid_entry_inside_the_prefix_is_still_unverified() -> None:
    book = Book()
    book.register("P1", (tgt(0, 100),))
    book.register("P1", (tgt(0, 100),))  # duplicate id inside the prefix
    book.evaluate("E1", "P1", (tgt(0, 100),))
    assert frozen(book, "E1").outcome is Outcome.UNVERIFIED


def test_qe9_alternative_a_tampered_prefix_is_unverified() -> None:
    book = _independent_first_run()
    tampered = (replace(book.ledger[0], entry_hash="0" * 64), *book.ledger[1:])
    result = classify_evaluation_prefix_frozen((tampered,), book.anchors(), LINEAGE, "E1")
    assert result.assessment.outcome is Outcome.UNVERIFIED


def test_qe9_alternative_a_missing_or_wrong_anchor_is_unverified() -> None:
    book = _independent_first_run()
    assert (
        classify_evaluation_prefix_frozen((book.ledger,), (), LINEAGE, "E1").assessment.outcome
        is Outcome.UNVERIFIED
    )
    wrong = (LedgerAnchor(book.ledger[-1].position, "f" * 64),)
    assert (
        classify_evaluation_prefix_frozen((book.ledger,), wrong, LINEAGE, "E1").assessment.outcome
        is Outcome.UNVERIFIED
    )
    assert (
        classify_evaluation_prefix_frozen((), book.anchors(), LINEAGE, "E1").assessment.outcome
        is Outcome.UNVERIFIED
    )


def test_qe9_alternative_a_a_bad_anchor_at_or_before_the_evaluation_is_unverified() -> None:
    book = _independent_first_run()
    good = book.anchors()[0]
    assert (
        classify_evaluation_prefix_frozen((book.ledger,), (good,), LINEAGE, "E1").assessment.outcome
        is Outcome.INDEPENDENT
    )
    bad_early = LedgerAnchor(0, "f" * 64)
    result = classify_evaluation_prefix_frozen((book.ledger,), (good, bad_early), LINEAGE, "E1")
    assert result.assessment.outcome is Outcome.UNVERIFIED


def test_qe9_alternative_a_retrospective_registration_is_still_rejected() -> None:
    book = Book()
    book.evaluate("E1", "P1", (tgt(0, 100),), registration_ts=1)
    book.register("P1", (tgt(0, 100),))
    assert frozen(book, "E1").outcome is Outcome.REJECTED_RETROSPECTIVE


def test_qe9_alternative_a_lineage_captured_at_the_evaluation_position() -> None:
    inst = SYN_INST
    fresh = _ticks(8, start=10_000)
    book = _book_for(_target(inst, BASE, "DS-1"), _target(SYN_ALIAS, fresh, "DS-F"))
    before = X.lineage_registry([X.KnownDataset(inst, BASE), X.KnownDataset(SYN_ALIAS, fresh)])
    partial = BASE[4:] + _ticks(4, start=50_000)
    after = X.lineage_registry(
        [
            X.KnownDataset(inst, BASE),
            X.KnownDataset(SYN_ALIAS, fresh),
            X.KnownDataset("SYN.PARTIAL", partial),
        ]
    )
    # Default B: the later dataset voids the earlier result.
    assert classify(book, "E1", after).outcome is Outcome.UNVERIFIED
    # Alternative A with lineage as captured when the evaluation was recorded.
    captured = classify_evaluation_prefix_frozen(
        (book.ledger,), book.anchors(), after, "E1", lineage_at=lambda _position: before
    )
    assert captured.assessment.outcome is Outcome.INDEPENDENT
    # Without a capture A cannot freeze lineage: it falls back to the live registry (fail closed).
    live = classify_evaluation_prefix_frozen((book.ledger,), book.anchors(), after, "E1")
    assert live.assessment.outcome is Outcome.UNVERIFIED


def test_qe9_alternative_a_late_declaration_does_not_downgrade_a_standalone_member() -> None:
    book = Book()
    _register_disjoint(book)
    _evaluate_disjoint(book, "a")
    declare(book, "D-1", ("P-a", "P-b"))
    _evaluate_disjoint(book, "b")
    assert frozen(book, "E-a").outcome is Outcome.INDEPENDENT  # A: frozen, declaration is later
    assert classify(book, "E-a").outcome is Outcome.EXPLORATORY  # B (default): M5 applies
    # The set itself is EXPLORATORY under both: the declaration came after a member evaluation.
    last = book.ledger[-1]
    set_result = classify_multiplicity_set(
        (book.ledger,),
        (LedgerAnchor(last.position, last.entry_hash),),
        LINEAGE,
        "D-1",
        authority_verifier=lambda _a, _c: True,
    )
    assert set_result.outcome is Outcome.EXPLORATORY


@pytest.mark.parametrize("seed", SEEDS)
def test_qe9_alternative_a_agrees_with_the_default_when_no_later_event_intervenes(
    seed: int,
) -> None:
    rng = random.Random(seed)
    for trial in range(25):
        book = _random_valid_book(rng, 14, allow_declarations=False)
        names = [e.record.evaluation_id for e in book.ledger if hasattr(e.record, "evaluation_id")]
        for name in names:
            assert frozen(book, name).outcome is classify(book, name).outcome, (seed, trial, name)


# ================================================================================================
# QE-7 / F-5: selection across footprints is ungoverned (CHARACTERIZATION, not endorsement)
# ================================================================================================


def test_qe7_characterization_five_disjoint_windows_are_five_independent_results() -> None:
    """UNGOVERNED today: nothing counts or corrects for choosing among disjoint footprints."""
    book = Book()
    windows = [(i * 100, i * 100 + 100) for i in range(5)]
    for i, w in enumerate(windows):
        book.register(f"P{i}", (tgt(*w),), family=f"FAM-{i}")
    for i, w in enumerate(windows):
        book.evaluate(f"E{i}", f"P{i}", (tgt(*w),), family=f"FAM-{i}")
    assert [classify(book, f"E{i}").outcome for i in range(5)] == [Outcome.INDEPENDENT] * 5


def test_qe7_characterization_adjacent_windows_do_not_contend() -> None:
    """Half-open adjacency [0,100) and [100,200) is disjoint: both INDEPENDENT (no serial-
    dependence or boundary-effect consideration exists)."""
    book = Book()
    book.register("P1", (tgt(0, 100),), family="F1")
    book.register("P2", (tgt(100, 200),), family="F2")
    book.evaluate("E1", "P1", (tgt(0, 100),), family="F1")
    book.evaluate("E2", "P2", (tgt(100, 200),), family="F2")
    assert {classify(book, "E1").outcome, classify(book, "E2").outcome} == {Outcome.INDEPENDENT}


def test_qe7_characterization_correlated_instruments_over_one_period_are_independent() -> None:
    """Different instruments over the same period do not contend: statistical independence between
    instruments is not modelled, and no independence threshold may be invented here (QE-7)."""
    a = (tgt(0, 100),)
    b = (tgt(0, 100, instrument=INST_B, content=ROOT_B),)
    book = Book()
    book.register("P1", a, family="F1")
    book.register("P2", b, family="F2")
    book.evaluate("E1", "P1", a, family="F1")
    book.evaluate("E2", "P2", b, family="F2")
    assert {classify(book, "E1").outcome, classify(book, "E2").outcome} == {Outcome.INDEPENDENT}
    assert INST_A != INST_B


# ================================================================================================
# QE-1 / F-6 and QE-8 / F-7: anchors without time, unauthenticated appenders, side effects
# ================================================================================================


def test_qe1_characterization_ledger_anchors_carry_no_time() -> None:
    assert [f.name for f in fields(LedgerAnchor)] == ["position", "entry_hash"]


def test_qe8_characterization_any_appender_can_release_contention_by_superseding() -> None:
    """No appender is authenticated: a registration that supersedes a contender releases it (G-i).
    Whether withdrawal needs an authority is a Quant / Architect decision (QE-8, QE-1)."""
    book = Book()
    book.register("P1", (tgt(0, 100),), family="F1")
    book.register("P2", (tgt(0, 100),), family="F2")
    book.register("P-rogue", (tgt(900, 950),), family="F-rogue", supersedes=("P2",))
    book.evaluate("E1", "P1", (tgt(0, 100),), family="F1")
    assert classify(book, "E1").outcome is Outcome.INDEPENDENT


def test_qe8_characterization_one_unresolvable_registration_makes_later_runs_unverified() -> None:
    """Denial of evidence by design (fail closed): a registration whose lineage is unknown makes
    every later evaluation that could contend with it UNVERIFIED."""
    mystery = ContentIdentity("syn-unknown", "canon/SYNTHETIC-0")
    lineage = {**LINEAGE, mystery: DatasetLineage(mystery, INST_B, 0, 1_000, "UNKNOWN")}
    book = Book()
    book.register("P-bad", (tgt(0, 50, instrument=INST_B, content=mystery),), family="F-bad")
    book.register("P1", (tgt(0, 100),), family="F1")
    book.evaluate("E1", "P1", (tgt(0, 100),), family="F1")
    assert classify(book, "E1", lineage).outcome is Outcome.UNVERIFIED


def test_qe8_characterization_other_canonicalization_version_makes_later_runs_unverified() -> None:
    other = ContentIdentity("syn-content-other", "canon/OTHER-1")
    lineage = {**LINEAGE, other: DatasetLineage(other, INST_A, 0, 1_000, "ROOT")}
    book = Book()
    book.register("P-other", (tgt(900, 950, content=other),), family="F-other")
    book.register("P1", (tgt(0, 100),), family="F1")
    book.evaluate("E1", "P1", (tgt(0, 100),), family="F1")
    result = classify(book, "E1", lineage)
    assert result.outcome is Outcome.UNVERIFIED
    assert any("CANONICALIZATION_CONFLICT" in r for r in result.reasons)


# ================================================================================================
# QE-10 / F-4: evasions that REMAIN because instrument / time / price equivalence is undefined
# ================================================================================================


@pytest.mark.xfail(
    strict=True,
    reason="BLOCKED QE-10: no approved contract maps a shifted raw time (offset, MD-2) to the same "
    "market data, so a time-shifted relabelled copy is not detected",
)
def test_qe10_time_shifted_relabelled_copy_is_not_independent() -> None:
    shifted = tuple(replace(r, raw_msc=r.raw_msc + 10_000) for r in BASE)
    registry = X.lineage_registry(
        [X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_ALIAS, shifted)]
    )
    book = _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_ALIAS, shifted, "DS-2"))
    assert classify(book, "E2", registry).outcome is not Outcome.INDEPENDENT


@pytest.mark.xfail(
    strict=True,
    reason="BLOCKED QE-10: instrument equivalence (price scaling, contract roll, alias symbols) is "
    "not defined by any approved contract (ADR-025 fixes canonical ids only)",
)
def test_qe10_price_scaled_relabelled_copy_is_not_independent() -> None:
    scaled = tuple(C.TickRecord(r.raw_msc, "9" + r.bid, "9" + r.ask, r.flags) for r in BASE)
    registry = X.lineage_registry(
        [X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_ALIAS, scaled)]
    )
    book = _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_ALIAS, scaled, "DS-2"))
    assert classify(book, "E2", registry).outcome is not Outcome.INDEPENDENT
