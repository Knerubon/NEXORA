"""Wave V9 remediation tests: STRUCTURAL behaviour of the MD-6 exposure and content models.

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
models; not runtime enforcement. Production runtime enforcement: NOT VERIFIED.

SCOPE OF THIS FILE: structural behaviour only, that is behaviour that follows from rules already
written in the proposal (M5, G-h, G-i, the fail-closed table, the content-relation rules) and
from no new Quant policy. Tests whose expected value depends on an unresolved Quant decision live in
``tests/test_md6_v9_policy_dependent.py`` (every test there names its QE decision).

Findings reproduced here (Wave V8 independent review of PR #94 head 930af2ae):

* F-1  ledger-wide fail-closed: a later invalid append voids earlier outcomes (default semantics are
  pinned here as "monotone non-improving"; the alternative lives in the policy-dependent file);
* F-2  free text must never become verified Quant authority;
* F-3  a declared multiplicity member must not be INDEPENDENT on its own (M5), whatever the order;
* F-4  trivial representation changes (flags dropped, trailing zeros) must not evade exposure
  when an instrument is relabelled.

All identities, windows, ticks, seeds and timestamps are SYNTHETIC. No MD-6 threshold, outcome value
or real data appears anywhere.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import replace
from itertools import permutations

import pytest

from tests import md6_cross_track_reference as X
from tests import md6_quarantine_reference as C
from tests.md6_exposure_reference import (
    Assessment,
    LedgerAnchor,
    LineageRegistry,
    MultiplicityDeclaration,
    Outcome,
    Target,
    append,
    check_family_disclosure,
    classify_evaluation,
    classify_multiplicity_set,
    family_outcome,
    reference_problem,
    request_relabel,
    verify_chain,
)
from tests.test_md6_exposure_lineage import LINEAGE, Book, tgt
from tests.test_md6_exposure_n1_remediation import (
    BASE,
    SYN_ALIAS,
    SYN_INST,
    VARIANTS,
    W,
    _book_for,
    _target,
    _ticks,
)

SEEDS = (11, 2026, 94096)  # SYNTHETIC; recorded in every failure message and in the PR evidence


def syn_verifier(authority_ref: str, correction_ref: str) -> bool:
    """SYNTHETIC test double. It is NOT an authority registry and encodes no Quant approval.

    It exists only to prove that the model has a path to INDEPENDENT for a declared set once some
    verifier (whose real definition is OPEN, QE-7) says yes.
    """
    return (authority_ref, correction_ref) == ("QA-SYN", "CORR-SYN")


def classify(book: Book, eval_id: str, lineage: LineageRegistry = LINEAGE) -> Assessment:
    return classify_evaluation((book.ledger,), book.anchors(), lineage, eval_id)


def classify_set(
    book: Book,
    decl_id: str,
    verifier: Callable[[str, str], bool] | None = None,
) -> Assessment:
    return classify_multiplicity_set(
        (book.ledger,), book.anchors(), LINEAGE, decl_id, authority_verifier=verifier
    )


def declare(
    book: Book,
    decl_id: str,
    members: tuple[str, ...],
    *,
    quant: str = "QA-SYN",
    correction: str = "CORR-SYN",
) -> None:
    ts = book._tick()
    book.ledger = append(
        book.ledger, MultiplicityDeclaration(decl_id, members, quant, correction, ts), ts
    )


# Three DISJOINT SYNTHETIC windows on one instrument (each its own exposure unit).
DISJOINT = {"a": (0, 100), "b": (100, 200), "c": (200, 300)}


def _w(name: str) -> tuple[Target, ...]:
    return (tgt(*DISJOINT[name]),)


def _register_disjoint(book: Book) -> None:
    for v in VARIANTS:
        book.register(f"P-{v}", _w(v), policy=f"POL-{v}", family=f"FAM-{v}")


def _evaluate_disjoint(book: Book, v: str) -> None:
    book.evaluate(f"E-{v}", f"P-{v}", _w(v), policy=f"POL-{v}", family=f"FAM-{v}")


# ================================================================================================
# F-3: declared members are set-dependent (M5), in every order
# ================================================================================================


def test_f3_control_undeclared_disjoint_registrations_are_each_independent() -> None:
    book = Book()
    _register_disjoint(book)
    for v in VARIANTS:
        _evaluate_disjoint(book, v)
    assert {classify(book, f"E-{v}").outcome for v in VARIANTS} == {Outcome.INDEPENDENT}


def test_f3_declared_member_with_disjoint_footprint_is_not_independent_alone() -> None:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b", "P-c"))
    for v in VARIANTS:
        _evaluate_disjoint(book, v)
    for v in VARIANTS:
        result = classify(book, f"E-{v}")
        assert result.outcome is Outcome.EXPLORATORY, v
        assert not result.outcome.gate_eligible
        assert result.declaration_id == "D-1"
        assert any("declared multiplicity set D-1" in r for r in result.reasons)


@pytest.mark.parametrize("declaration_slot", [0, 1, 2, 3])
def test_f3_every_evaluation_order_and_declaration_position_keeps_members_non_independent(
    declaration_slot: int,
) -> None:
    """All 6 evaluation orders x declaration before / between / after the evaluations."""
    set_outcomes: set[Outcome] = set()
    verified_set_outcomes: set[Outcome] = set()
    for order in permutations(VARIANTS):
        book = Book()
        _register_disjoint(book)
        for slot, v in enumerate(order):
            if slot == declaration_slot:
                declare(book, "D-1", ("P-a", "P-b", "P-c"))
            _evaluate_disjoint(book, v)
        if declaration_slot == 3:
            declare(book, "D-1", ("P-a", "P-b", "P-c"))
        assert verify_chain(book.ledger) == (), order
        for v in VARIANTS:
            assert classify(book, f"E-{v}").outcome is not Outcome.INDEPENDENT, (order, v)
        set_outcomes.add(classify_set(book, "D-1").outcome)
        verified_set_outcomes.add(classify_set(book, "D-1", syn_verifier).outcome)
    # The set outcome does not depend on the order in which members were evaluated.
    assert len(set_outcomes) == 1 and len(verified_set_outcomes) == 1
    if declaration_slot == 0:
        assert set_outcomes == {Outcome.PENDING_QUANT_AUTHORITY}
        assert verified_set_outcomes == {Outcome.INDEPENDENT}
    else:
        assert set_outcomes == verified_set_outcomes == {Outcome.EXPLORATORY}


def test_f3_non_member_evaluation_beside_a_declared_set_stays_independent() -> None:
    book = Book()
    _register_disjoint(book)
    book.register("P-d", (tgt(300, 400),), policy="POL-d", family="FAM-d")
    declare(book, "D-1", ("P-a", "P-b", "P-c"))
    for v in VARIANTS:
        _evaluate_disjoint(book, v)
    book.evaluate("E-d", "P-d", (tgt(300, 400),), policy="POL-d", family="FAM-d")
    assert classify(book, "E-d").outcome is Outcome.INDEPENDENT
    assert classify(book, "E-d").declaration_id is None


def test_f3_member_is_not_independent_even_when_the_set_is_verified() -> None:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b", "P-c"))
    for v in VARIANTS:
        _evaluate_disjoint(book, v)
    assert classify_set(book, "D-1", syn_verifier).outcome is Outcome.INDEPENDENT
    assert {classify(book, f"E-{v}").outcome for v in VARIANTS} == {Outcome.EXPLORATORY}


def test_f3_declaration_with_malformed_references_still_blocks_standalone_members() -> None:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b", "P-c"), quant=" ", correction="")
    for v in VARIANTS:
        _evaluate_disjoint(book, v)
    assert classify_set(book, "D-1").outcome is Outcome.UNVERIFIED
    assert {classify(book, f"E-{v}").outcome for v in VARIANTS} == {Outcome.EXPLORATORY}


def test_f3_member_downgrade_never_hides_a_worse_outcome() -> None:
    """Precedence is unchanged: a member that is also UNVERIFIED stays UNVERIFIED."""
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b", "P-c"))
    book.evaluate("E-a", "P-a", _w("a"), policy="POL-a", family="FAM-a", registration_ts=999)
    assert classify(book, "E-a").outcome is Outcome.UNVERIFIED


# ================================================================================================
# F-2: free text is never verified Quant authority
# ================================================================================================

MALFORMED_REFS = [
    "",
    " ",
    "\t",
    "\n",
    "  QA-SYN  ",
    "QA SYN",
    "QA\tSYN",
    "QA-SYN\n",
    " ",
    "​",
    "QAé",
    "\x00",
    "!!!",
    "---",
    "NONE",
    "none",
    "N/A",
    "null",
    "TBD",
    "pending",
    "unknown",
]


@pytest.mark.parametrize("bad", MALFORMED_REFS)
def test_f2_malformed_authority_or_correction_reference_is_unverified(bad: str) -> None:
    assert reference_problem(bad) is not None
    for quant, correction in ((bad, "CORR-SYN"), ("QA-SYN", bad)):
        book = Book()
        _register_disjoint(book)
        declare(book, "D-1", ("P-a", "P-b"), quant=quant, correction=correction)
        for v in ("a", "b"):
            _evaluate_disjoint(book, v)
        # Even a verifier that approves everything cannot rescue a malformed reference.
        for verifier in (None, lambda _a, _c: True):
            result = classify_set(book, "D-1", verifier)
            assert result.outcome is Outcome.UNVERIFIED, repr(bad)
            assert not result.outcome.gate_eligible


@pytest.mark.parametrize("not_text", [None, 7, b"QA-SYN", ("QA-SYN",)])
def test_f2_non_string_reference_is_malformed(not_text: object) -> None:
    assert reference_problem(not_text) is not None


@pytest.mark.parametrize("ok", ["QA-SYN", "x", "QA.SYN/2026#1", "ref:QA_SYN"])
def test_f2_well_formed_free_text_is_pending_never_independent(ok: str) -> None:
    """Structural validity says nothing about truth: no verified authority, no INDEPENDENT."""
    assert reference_problem(ok) is None
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b"), quant=ok, correction=ok)
    for v in ("a", "b"):
        _evaluate_disjoint(book, v)
    result = classify_set(book, "D-1")
    assert result.outcome is Outcome.PENDING_QUANT_AUTHORITY
    assert not result.outcome.gate_eligible
    assert any("NOT VERIFIED" in r for r in result.reasons)


def test_f2_default_has_no_verified_authority_and_a_rejecting_verifier_changes_nothing() -> None:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b", "P-c"))
    for v in VARIANTS:
        _evaluate_disjoint(book, v)
    assert classify_set(book, "D-1").outcome is Outcome.PENDING_QUANT_AUTHORITY
    assert classify_set(book, "D-1", lambda _a, _c: False).outcome is (
        Outcome.PENDING_QUANT_AUTHORITY
    )


def test_f2_a_crashing_verifier_fails_closed() -> None:
    def boom(_a: str, _c: str) -> bool:
        raise RuntimeError("synthetic verifier failure")

    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b"))
    for v in ("a", "b"):
        _evaluate_disjoint(book, v)
    assert classify_set(book, "D-1", boom).outcome is Outcome.UNVERIFIED


def test_f2_verifier_is_consulted_only_after_every_exposure_check_passes() -> None:
    """A verified authority does not excuse a peek (M4) or a late declaration (M2)."""
    peeked = Book()
    _register_disjoint(peeked)
    declare(peeked, "D-1", ("P-a", "P-b"))
    peeked.evaluate("E-peek", None, _w("a"))
    for v in ("a", "b"):
        _evaluate_disjoint(peeked, v)
    assert classify_set(peeked, "D-1", syn_verifier).outcome is Outcome.EXPLORATORY
    late = Book()
    _register_disjoint(late)
    _evaluate_disjoint(late, "a")
    declare(late, "D-1", ("P-a", "P-b"))
    _evaluate_disjoint(late, "b")
    assert classify_set(late, "D-1", syn_verifier).outcome is Outcome.EXPLORATORY


def test_f2_pending_outcome_is_not_eligible_not_relabellable_and_never_a_family_pass() -> None:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b"))
    for v in ("a", "b"):
        _evaluate_disjoint(book, v)
    pending = classify_set(book, "D-1")
    assert pending.outcome is Outcome.PENDING_QUANT_AUTHORITY
    assert not request_relabel(pending, Outcome.INDEPENDENT).accepted
    assert family_outcome([pending, classify(book, "E-a")], ()) is not Outcome.INDEPENDENT
    assert family_outcome([pending], ()) is Outcome.PENDING_QUANT_AUTHORITY


def test_f2_only_independent_is_gate_eligible() -> None:
    assert [o for o in Outcome if o.gate_eligible] == [Outcome.INDEPENDENT]


def test_f2_declaration_ref_malformation_does_not_poison_the_whole_ledger() -> None:
    """A malformed reference voids the set, not unrelated evaluations (no ledger-wide DoS)."""
    book = Book()
    _register_disjoint(book)
    book.register("P-z", (tgt(500, 600),), policy="POL-z", family="FAM-z")
    book.evaluate("E-z", "P-z", (tgt(500, 600),), policy="POL-z", family="FAM-z")
    declare(book, "D-1", ("P-a", "P-b"), quant="  ", correction="")
    assert verify_chain(book.ledger) == ()
    assert classify(book, "E-z").outcome is Outcome.INDEPENDENT


# ================================================================================================
# F-4: trivial representation changes cannot bypass exposure under a relabelled instrument
# ================================================================================================


def _drop_flags(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return tuple(replace(r, flags=None) for r in rs)


def _rewrite_flags(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return tuple(replace(r, flags="2") for r in rs)


def _pad_zeros(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return tuple(replace(r, bid=r.bid + "00", ask=r.ask + "00") for r in rs)


def _strip_zeros(token: str) -> str:
    return token.rstrip("0").rstrip(".") if "." in token else token


def _strip_zeros_all(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return tuple(replace(r, bid=_strip_zeros(r.bid), ask=_strip_zeros(r.ask)) for r in rs)


def _reverse(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return tuple(reversed(rs))


def _all_at_once(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return _reverse(_pad_zeros(_drop_flags(rs)))


def _subset_drifted(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return _drop_flags(rs[2:6])


def _half_drifted(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return rs[:4] + _drop_flags(rs[4:])


def _drifted_plus_exact_overlap(rs: tuple[C.TickRecord, ...]) -> tuple[C.TickRecord, ...]:
    return rs[:1] + _drop_flags(rs[1:]) + _ticks(2, start=40_000)


TRIVIAL_CHANGES: dict[str, Callable[[tuple[C.TickRecord, ...]], tuple[C.TickRecord, ...]]] = {
    "drop_flags": _drop_flags,
    "rewrite_flags": _rewrite_flags,
    "pad_trailing_zeros": _pad_zeros,
    "strip_trailing_zeros": _strip_zeros_all,
    "reorder": _reverse,
    "all_at_once": _all_at_once,
    "subset_drifted": _subset_drifted,
    "half_drifted": _half_drifted,
    "drifted_plus_unrelated": _drifted_plus_exact_overlap,
}


def _relabelled_second_run(
    copy: tuple[C.TickRecord, ...], *, second_first: bool = False, registry_reversed: bool = False
) -> tuple[Book, LineageRegistry]:
    known = [X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_ALIAS, copy)]
    if registry_reversed:
        known.reverse()
    lineage = X.lineage_registry(known)
    first, second = _target(SYN_INST, BASE, "DS-1"), _target(SYN_ALIAS, copy, "DS-2")
    if second_first:
        first, second = second, first
    return _book_for(first, second), lineage


def test_f4_control_unmodified_relabelled_instrument_with_disjoint_content_is_independent() -> None:
    fresh = _ticks(8, start=10_000)
    book, lineage = _relabelled_second_run(fresh)
    assert classify(book, "E2", lineage).outcome is Outcome.INDEPENDENT


def test_f4_control_same_time_grid_different_prices_on_another_instrument_is_independent() -> None:
    other = tuple(C.TickRecord(r.raw_msc, "2" + r.bid[1:], "2" + r.ask[1:], r.flags) for r in BASE)
    assert X.content_relation(other, BASE) == "NONE"
    book, lineage = _relabelled_second_run(other)
    assert classify(book, "E2", lineage).outcome is Outcome.INDEPENDENT


@pytest.mark.parametrize("name", sorted(TRIVIAL_CHANGES))
def test_f4_trivial_change_under_relabelled_instrument_is_never_independent(name: str) -> None:
    copy = TRIVIAL_CHANGES[name](BASE)
    for second_first in (False, True):
        for registry_reversed in (False, True):
            book, lineage = _relabelled_second_run(
                copy, second_first=second_first, registry_reversed=registry_reversed
            )
            for evaluation in ("E1", "E2"):
                result = classify(book, evaluation, lineage)
                assert result.outcome is Outcome.UNVERIFIED, (
                    name,
                    second_first,
                    registry_reversed,
                    evaluation,
                    result.reasons,
                )


def test_f4_content_relation_reports_representation_drift() -> None:
    assert X.content_relation(_drop_flags(BASE), BASE) == "REPRESENTATION_DRIFT"
    assert X.content_relation(_pad_zeros(BASE), BASE) == "REPRESENTATION_DRIFT"
    assert X.content_relation(_rewrite_flags(BASE), BASE) == "REPRESENTATION_DRIFT"
    assert X.content_relation(_half_drifted(BASE), BASE) == "REPRESENTATION_DRIFT"
    assert X.content_relation(BASE, BASE) == "EXACT_ORDERED"
    assert X.content_relation(_reverse(BASE), BASE) == "MULTISET"
    assert X.content_relation(BASE[2:6], BASE) == "SUBSET"


def test_f4_drift_lineage_is_unknown_for_both_sides_and_same_instrument_stays_exposed() -> None:
    copy = _drop_flags(BASE)
    registry = X.lineage_registry([X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_ALIAS, copy)])
    assert {n.status for n in registry.values()} == {"UNKNOWN"}
    same_instrument, lineage = (
        _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_INST, copy, "DS-2")),
        X.lineage_registry([X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_INST, copy)]),
    )
    # Same instrument label: the instrument key already carries the exposure (EXPLORATORY).
    assert classify(same_instrument, "E2", lineage).outcome is Outcome.EXPLORATORY


def test_f4_metamorphic_outcome_class_is_invariant_under_the_change_and_its_composition() -> None:
    outcomes = set()
    for name, change in TRIVIAL_CHANGES.items():
        if name == "drifted_plus_unrelated":
            continue
        book, lineage = _relabelled_second_run(change(BASE))
        outcomes.add(classify(book, "E2", lineage).outcome)
    assert outcomes == {Outcome.UNVERIFIED}


def test_f4_cross_track_and_quarantine_paths_agree_on_every_trivial_change() -> None:
    """Parity: whatever the quarantine register treats as a re-expression, so does the bridge."""
    q = C.record_quarantine(
        b"",
        C.anchor_for(()),
        C.QuarantineRequest(
            C.CandidateDataset("ds-A", BASE),
            "DATASET",
            None,
            SYN_INST,
            "hard:crossed",
            "CRIT-SYN",
            ("EV-1",),
            "sub-1",
            ("author-1",),
            (),
            C.Authority("reviewer-1", "INDEPENDENT_DATA_REVIEWER"),
            10,
            False,
        ),
    )
    assert isinstance(q, C.RegisterUpdated)
    for name, change in TRIVIAL_CHANGES.items():
        copy = change(BASE)
        reentry = C.check_reentry(C.CandidateDataset(f"ds-{name}", copy), q.register, q.anchor)
        relation = X.content_relation(copy, BASE)
        if name == "drifted_plus_unrelated":
            # A drifted copy padded with unrelated ticks is still a re-expression.
            assert relation == "REPRESENTATION_DRIFT", name
        assert reentry.outcome != "NOT_KNOWN", name
        assert not reentry.may_proceed_to_part_a, name
        assert relation != "NONE", name


# ================================================================================================
# F-1 (default semantics) and append order
# ================================================================================================


def _independent_first_run() -> Book:
    book = Book()
    book.register("P1", W)
    book.evaluate("E1", "P1", W)
    assert classify(book, "E1").outcome is Outcome.INDEPENDENT
    return book


def _append_dup_registration(book: Book) -> None:
    book.register("P1", (tgt(500, 600),))


def _append_overlapping_declarations(book: Book) -> None:
    for name in ("x", "y", "z"):
        book.register(f"P-{name}", (tgt(600, 700),), family=f"FAM-{name}")
    declare(book, "D-1", ("P-x", "P-y"))
    declare(book, "D-2", ("P-y", "P-z"))


def _append_abandon_unknown(book: Book) -> None:
    book.abandon("P-never-registered")


def _append_supersede_unknown(book: Book) -> None:
    book.register("P9", (tgt(500, 600),), supersedes=("P-never-registered",))


def _append_dup_evaluation(book: Book) -> None:
    book.evaluate("E1", None, (tgt(500, 600),))


INVALID_APPENDS: dict[str, Callable[[Book], None]] = {
    "duplicate_preregistration_id": _append_dup_registration,
    "overlapping_declarations": _append_overlapping_declarations,
    "abandon_unknown_id": _append_abandon_unknown,
    "supersede_unknown_id": _append_supersede_unknown,
    "duplicate_evaluation_id": _append_dup_evaluation,
}


@pytest.mark.parametrize("name", sorted(INVALID_APPENDS))
def test_f1_default_semantics_a_later_invalid_append_voids_every_earlier_outcome(
    name: str,
) -> None:
    """PINS the DEFAULT semantics: whole-ledger fail-closed (alternative A is BLOCKED, see the
    policy-dependent tests). The direction is always toward UNVERIFIED, never toward eligibility."""
    book = _independent_first_run()
    INVALID_APPENDS[name](book)
    assert verify_chain(book.ledger) != ()
    assert classify(book, "E1").outcome is Outcome.UNVERIFIED


def test_f1_default_semantics_a_later_partially_overlapping_dataset_voids_earlier_lineage() -> None:
    """PINS the DEFAULT semantics: the lineage registry is one ledger-external input, not frozen
    per entry. A later dataset that partially overlaps an earlier one makes both UNKNOWN."""
    inst = SYN_INST
    book = _book_for(_target(inst, BASE, "DS-1"), _target(inst, _ticks(8, start=10_000), "DS-F"))
    registry_before = X.lineage_registry(
        [X.KnownDataset(inst, BASE), X.KnownDataset(SYN_ALIAS, _ticks(8, start=10_000))]
    )
    assert classify(book, "E1", registry_before).outcome is Outcome.INDEPENDENT
    partial = BASE[4:] + _ticks(4, start=50_000)
    registry_after = X.lineage_registry(
        [
            X.KnownDataset(inst, BASE),
            X.KnownDataset(SYN_ALIAS, _ticks(8, start=10_000)),
            X.KnownDataset("SYN.PARTIAL", partial),
        ]
    )
    assert classify(book, "E1", registry_after).outcome is Outcome.UNVERIFIED


def test_f1_valid_non_declaration_appends_never_change_an_earlier_outcome() -> None:
    """R2 holds exactly for appends that keep the ledger valid and are not declarations."""
    book = _independent_first_run()
    before = classify(book, "E1")
    book.register("P2", (tgt(500, 600),), family="FAM-2")
    book.evaluate("E2", "P2", (tgt(500, 600),), family="FAM-2")
    book.view_protected("V1", (tgt(700, 800),))
    book.register("P3", (tgt(900, 950),), supersedes=("P2",))
    book.abandon("P3")
    book.evaluate("E3-unregistered", None, (tgt(0, 50),))
    assert verify_chain(book.ledger) == ()
    assert classify(book, "E1") == before


def _random_valid_book(rng: random.Random, steps: int, *, allow_declarations: bool) -> Book:
    windows = [(0, 50), (50, 100), (100, 150), (0, 100), (150, 200), (25, 175)]
    book = Book()
    pending: list[tuple[str, tuple[int, int]]] = []
    registered: list[str] = []
    counter = 0
    for _ in range(steps):
        choice = rng.choice(["reg", "reg", "eval", "eval", "peek", "view", "abandon", "decl"])
        counter += 1
        if choice == "reg":
            window = rng.choice(windows)
            sup: tuple[str, ...] = ()
            if registered and rng.random() < 0.25:
                sup = (rng.choice(registered),)
            book.register(f"P{counter}", (tgt(*window),), family=f"F{counter}", supersedes=sup)
            registered.append(f"P{counter}")
            pending.append((f"P{counter}", window))
        elif choice == "eval" and pending:
            prereg, window = pending.pop(rng.randrange(len(pending)))
            book.evaluate(f"E{counter}", prereg, (tgt(*window),), family=f"F{prereg[1:]}")
        elif choice == "peek":
            book.evaluate(f"E{counter}", None, (tgt(*rng.choice(windows)),))
        elif choice == "view":
            book.view_protected(f"V{counter}", (tgt(*rng.choice(windows)),))
        elif choice == "abandon" and registered:
            book.abandon(rng.choice(registered))
        elif choice == "decl" and allow_declarations and len(pending) >= 2:
            members = tuple(p for p, _ in pending[:2])
            if not any(
                isinstance(e.record, MultiplicityDeclaration)
                and set(members) & set(e.record.member_preregistration_ids)
                for e in book.ledger
            ):
                declare(book, f"D{counter}", members)
    return book


def _replay_outcomes(book: Book, steps: list[int]) -> dict[int, dict[str, Outcome]]:
    """Outcomes of every evaluation present at each ledger prefix length in ``steps``."""
    snapshots: dict[int, dict[str, Outcome]] = {}
    for length in steps:
        prefix = book.ledger[:length]
        anchors = (LedgerAnchor(prefix[-1].position, prefix[-1].entry_hash),)
        names = [e.record.evaluation_id for e in prefix if hasattr(e.record, "evaluation_id")]
        snapshots[length] = {
            n: classify_evaluation((prefix,), anchors, LINEAGE, n).outcome for n in names
        }
    return snapshots


@pytest.mark.parametrize("seed", SEEDS)
def test_append_order_valid_non_declaration_appends_never_change_earlier_outcomes(
    seed: int,
) -> None:
    rng = random.Random(seed)
    for trial in range(30):
        book = _random_valid_book(rng, 14, allow_declarations=False)
        assert verify_chain(book.ledger) == (), (seed, trial)
        lengths = list(range(1, len(book.ledger) + 1))
        snaps = _replay_outcomes(book, lengths)
        for later in lengths:
            for name, outcome in snaps[later].items():
                first = next(n for n in lengths if name in snaps[n])
                assert snaps[first][name] == outcome, (seed, trial, name, first, later)


@pytest.mark.parametrize("seed", SEEDS)
def test_append_order_no_append_ever_grants_eligibility_to_an_earlier_evaluation(
    seed: int,
) -> None:
    """Monotone non-improving, including late multiplicity declarations (which only downgrade)."""
    rng = random.Random(seed)
    for trial in range(30):
        book = _random_valid_book(rng, 14, allow_declarations=True)
        assert verify_chain(book.ledger) == (), (seed, trial)
        lengths = list(range(1, len(book.ledger) + 1))
        snaps = _replay_outcomes(book, lengths)
        for later in lengths:
            for name, outcome in snaps[later].items():
                first = next(n for n in lengths if name in snaps[n])
                if snaps[first][name] is not Outcome.INDEPENDENT:
                    assert outcome is not Outcome.INDEPENDENT, (seed, trial, name, first, later)


# ================================================================================================
# Regression: three overlapping variants, all permutations (N1 / C1 / C6)
# ================================================================================================


def _variants_book(reg_order: tuple[str, ...], eval_order: tuple[str, ...]) -> Book:
    book = Book()
    for v in reg_order:
        book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    for v in eval_order:
        book.evaluate(f"E-{v}", f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    return book


def test_three_overlapping_variants_six_evaluation_orders_have_no_false_independent() -> None:
    results = []
    for order in permutations(VARIANTS):
        book = _variants_book(VARIANTS, order)
        results.append({f"E-{v}": classify(book, f"E-{v}").outcome for v in VARIANTS})
    assert len(results) == 6
    assert all(r == results[0] for r in results)
    assert all(o is Outcome.EXPLORATORY for r in results for o in r.values())


def test_three_overlapping_variants_all_36_registration_and_evaluation_orders() -> None:
    seen: set[tuple[tuple[str, Outcome], ...]] = set()
    for reg_order in permutations(VARIANTS):
        for eval_order in permutations(VARIANTS):
            book = _variants_book(reg_order, eval_order)
            outcomes = tuple(sorted((f"E-{v}", classify(book, f"E-{v}").outcome) for v in VARIANTS))
            assert all(o is not Outcome.INDEPENDENT for _, o in outcomes), (reg_order, eval_order)
            seen.add(outcomes)
    assert len(seen) == 1  # identical per-identity outcomes in every order


@pytest.mark.parametrize("seed", SEEDS)
def test_overlapping_variants_random_permutation_trials_are_order_independent(seed: int) -> None:
    rng = random.Random(seed)
    for trial in range(40):
        count = rng.choice([3, 4, 5])
        names = [chr(ord("a") + i) for i in range(count)]
        windows = {n: (rng.randrange(0, 30), rng.randrange(70, 120)) for n in names}
        reference: dict[str, Outcome] | None = None
        for _ in range(6):
            reg_order, eval_order = names[:], names[:]
            rng.shuffle(reg_order)
            rng.shuffle(eval_order)
            book = Book()
            for n in reg_order:
                book.register(f"P-{n}", (tgt(*windows[n]),), family=f"FAM-{n}")
            for n in eval_order:
                book.evaluate(f"E-{n}", f"P-{n}", (tgt(*windows[n]),), family=f"FAM-{n}")
            outcomes = {f"E-{n}": classify(book, f"E-{n}").outcome for n in names}
            assert Outcome.INDEPENDENT not in outcomes.values(), (seed, trial)
            if reference is None:
                reference = outcomes
            assert outcomes == reference, (seed, trial)


def test_interleaved_registration_gives_exactly_one_independent_the_first_mover() -> None:
    for order in permutations(VARIANTS):
        book = Book()
        for v in order:
            book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
            book.evaluate(f"E-{v}", f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
        outcomes = {v: classify(book, f"E-{v}").outcome for v in VARIANTS}
        assert [v for v, o in outcomes.items() if o is Outcome.INDEPENDENT] == [order[0]]


# ================================================================================================
# Adversarial: subset / relabel / lineage / multiplicity composition
# ================================================================================================


def test_adversarial_subset_of_a_drifted_relabelled_copy_cannot_launder_exposure() -> None:
    copy = _subset_drifted(BASE)
    registry = X.lineage_registry(
        [X.KnownDataset(SYN_INST, BASE), X.KnownDataset("SYN.THIRD", copy)]
    )
    book = _book_for(_target(SYN_INST, BASE, "DS-1"), _target("SYN.THIRD", copy, "DS-3"))
    assert classify(book, "E2", registry).outcome is Outcome.UNVERIFIED


def test_adversarial_disclosure_still_lists_every_variant_after_the_change() -> None:
    book = Book()
    for v in VARIANTS:
        book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    assert check_family_disclosure(book.ledger, W, LINEAGE, ["P-a", "P-b"], 2) != ()
    assert check_family_disclosure(book.ledger, W, LINEAGE, ["P-a", "P-b", "P-c"], 3) == ()


def test_adversarial_declared_set_member_cannot_be_cited_through_a_relabelled_family() -> None:
    """Family aggregation cannot turn a member's standalone result back into eligibility."""
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b"))
    for v in ("a", "b"):
        _evaluate_disjoint(book, v)
    members = [classify(book, "E-a"), classify(book, "E-b")]
    assert family_outcome(members, ()) is Outcome.EXPLORATORY


def test_adversarial_unregistered_run_on_a_declared_footprint_blocks_the_set() -> None:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b"))
    book.evaluate("E-peek", None, _w("b"))
    for v in ("a", "b"):
        _evaluate_disjoint(book, v)
    assert classify_set(book, "D-1", syn_verifier).outcome is Outcome.EXPLORATORY


def test_adversarial_every_assessment_still_names_a_known_outcome_enum_member() -> None:
    assert {o.value for o in Outcome} == {
        "INDEPENDENT",
        "PENDING_QUANT_AUTHORITY",
        "EXPLORATORY",
        "UNVERIFIED",
        "REJECTED_RETROSPECTIVE",
    }


# ================================================================================================
# Mutation-driven additions: guards that outcome-level tests alone did not pin
# ================================================================================================


def test_reference_problem_messages_distinguish_each_structural_defect() -> None:
    assert reference_problem(" ") == "reference is blank"
    assert reference_problem("") == "reference is blank"
    assert reference_problem(" QA-SYN") == "reference has leading or trailing whitespace"
    assert reference_problem("QA SYN") == (
        "reference contains whitespace, control or non-ASCII characters"
    )
    assert reference_problem("---") == "reference has no alphanumeric character"
    assert reference_problem("NONE") == "reference is a no-reference literal"
    assert reference_problem(3) == "reference is not text"
    assert reference_problem("QA-SYN") is None


def test_family_mixing_a_pending_set_with_an_exploratory_member_is_exploratory() -> None:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", ("P-a", "P-b"))
    for v in ("a", "b"):
        _evaluate_disjoint(book, v)
    pending = classify_set(book, "D-1")
    member = classify(book, "E-a")
    assert pending.outcome is Outcome.PENDING_QUANT_AUTHORITY
    assert member.outcome is Outcome.EXPLORATORY
    assert family_outcome([pending, member], ()) is Outcome.EXPLORATORY
    assert family_outcome([pending, pending], ()) is Outcome.PENDING_QUANT_AUTHORITY


def test_regression_window_outside_the_dataset_coverage_is_unverified() -> None:
    """Fail-closed table: a target window that leaves its dataset's recorded coverage."""
    outside = (tgt(900, 1_100),)  # SYNTHETIC: ROOT_A covers [0, 1000)
    book = Book()
    book.register("P1", outside)
    book.evaluate("E1", "P1", outside)
    result = classify(book, "E1")
    assert result.outcome is Outcome.UNVERIFIED
    assert any("outside the dataset's recorded coverage" in r for r in result.reasons)


@pytest.mark.parametrize(
    ("members", "expected"),
    [(("P-a",), "needs 2+ distinct members"), (("P-a", "P-a"), "needs 2+ distinct members")],
)
def test_regression_degenerate_declarations_break_the_ledger(
    members: tuple[str, ...], expected: str
) -> None:
    book = Book()
    _register_disjoint(book)
    declare(book, "D-1", members)
    assert any(expected in p for p in verify_chain(book.ledger))


def test_regression_reasons_name_the_exposure_and_consumption_causes() -> None:
    late = Book()
    late.evaluate("E0", None, W)
    late.register("P1", W)
    late.evaluate("E1", "P1", W)
    assert "registered windows/content already exposed before registration" in (
        classify(late, "E1").reasons
    )
    repeat = Book()
    repeat.register("P1", W)
    repeat.evaluate("E1", "P1", W)
    repeat.evaluate("E1b", "P1", W)
    assert (
        "registration already consumed by an earlier evaluation" in classify(repeat, "E1b").reasons
    )


# ================================================================================================
# Quarantine and lineage adversarial additions
# ================================================================================================


def _quarantined(
    records: tuple[C.TickRecord, ...], *, window: str | None = None
) -> C.RegisterUpdated:
    result = C.record_quarantine(
        b"",
        C.anchor_for(()),
        C.QuarantineRequest(
            C.CandidateDataset("ds-A", records),
            "WINDOW" if window else "DATASET",
            window,
            SYN_INST,
            "hard:crossed",
            "CRIT-SYN",
            ("EV-1",),
            "sub-1",
            ("author-1",),
            (),
            C.Authority("reviewer-1", "INDEPENDENT_DATA_REVIEWER"),
            10,
            False,
        ),
    )
    assert isinstance(result, C.RegisterUpdated)
    return result


@pytest.mark.parametrize("name", sorted(TRIVIAL_CHANGES))
def test_quarantine_window_scope_is_not_bypassed_by_a_trivial_change_of_the_whole_dataset(
    name: str,
) -> None:
    window = BASE[2:6]
    q = _quarantined(window, window="W2")
    candidate = TRIVIAL_CHANGES[name](BASE)
    result = C.check_reentry(C.CandidateDataset(f"ds-{name}", candidate), q.register, q.anchor)
    assert result.outcome != "NOT_KNOWN", (name, result.relation)
    assert not result.may_proceed_to_part_a


def test_quarantine_sibling_window_with_a_trivial_change_stays_eligible_for_part_a() -> None:
    q = _quarantined(BASE[2:6], window="W2")
    sibling = _drop_flags(BASE[6:])
    result = C.check_reentry(C.CandidateDataset("ds-sibling", sibling), q.register, q.anchor)
    assert result.outcome == "NOT_KNOWN"


def test_quarantine_drifted_copy_of_a_requalified_subset_is_never_requalified() -> None:
    q = _quarantined(BASE)
    subset = BASE[:4]
    requalified = C.requalify(
        q.register,
        q.anchor,
        C.RequalificationRequest(
            q.entry.entry_hash,
            C.content_identity(subset),
            ("EV-new",),
            C.Authority("quant-1", "QUANT_APPROVER"),
            ("author-1",),
            "SYN rationale",
            20,
            False,
            subset,
        ),
    )
    assert isinstance(requalified, C.RegisterUpdated)
    exact = C.check_reentry(
        C.CandidateDataset("ds-sub", subset), requalified.register, requalified.anchor
    )
    assert exact.outcome == "REQUALIFIED"
    drifted = C.check_reentry(
        C.CandidateDataset("ds-sub-drift", _drop_flags(subset)),
        requalified.register,
        requalified.anchor,
    )
    assert drifted.outcome not in ("REQUALIFIED", "NOT_KNOWN")
    assert not drifted.may_proceed_to_part_a


def test_adversarial_three_label_chain_of_drifted_copies_is_unverified_everywhere() -> None:
    second, third = _drop_flags(BASE), _pad_zeros(BASE)
    registry = X.lineage_registry(
        [
            X.KnownDataset(SYN_INST, BASE),
            X.KnownDataset(SYN_ALIAS, second),
            X.KnownDataset("SYN.THIRD", third),
        ]
    )
    assert {n.status for n in registry.values()} == {"UNKNOWN"}
    book = _book_for(_target(SYN_ALIAS, second, "DS-2"), _target("SYN.THIRD", third, "DS-3"))
    assert classify(book, "E2", registry).outcome is Outcome.UNVERIFIED


def test_adversarial_registry_listing_order_never_changes_any_lineage_node() -> None:
    datasets = [
        X.KnownDataset(SYN_INST, BASE),
        X.KnownDataset(SYN_ALIAS, _drop_flags(BASE)),
        X.KnownDataset("SYN.THIRD", _ticks(8, start=20_000)),
        X.KnownDataset("SYN.FOURTH", BASE[2:5]),
    ]
    reference = X.lineage_registry(datasets)
    for order in permutations(datasets):
        assert X.lineage_registry(list(order)) == reference
