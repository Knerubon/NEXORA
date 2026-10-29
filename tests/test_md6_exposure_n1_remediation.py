"""Regression tests for the PR #94 round-2 review findings N1 (MAJOR) and N2 to N6 (MINOR).

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
models; not runtime enforcement.

C1 to C12 are the mandatory Track C regression cases. All identities, windows, ticks and timestamps
are SYNTHETIC; no MD-6 threshold, outcome value or real data appears anywhere.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import permutations

from tests import md6_cross_track_reference as X
from tests import md6_gap_partition_reference as A
from tests import md6_quarantine_reference as C
from tests.md6_exposure_reference import (
    Assessment,
    ContentIdentity,
    DatasetLineage,
    LedgerAnchor,
    LineageRegistry,
    MultiplicityDeclaration,
    Outcome,
    Target,
    append,
    check_evaluation_disclosure,
    check_family_disclosure,
    classify_evaluation,
    classify_multiplicity_set,
    family_outcome,
    request_relabel,
    verify_chain,
)
from tests.test_md6_exposure_lineage import (
    CANON,
    CHILD_LATE,
    CHILD_MID,
    INST_A,
    INST_B,
    LINEAGE,
    ROOT_B,
    Book,
    tgt,
)

W = (tgt(0, 100),)  # SYNTHETIC window [0, 100) on INST_A
VARIANTS = ("a", "b", "c")  # SYNTHETIC policy variants, e.g. three OPEN max_gap_ms choices


def classify(book: Book, eval_id: str, lineage: LineageRegistry = LINEAGE) -> Assessment:
    last = book.ledger[-1]
    anchors = (LedgerAnchor(last.position, last.entry_hash),)
    return classify_evaluation((book.ledger,), anchors, lineage, eval_id)


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


def classify_set(book: Book, decl_id: str) -> Assessment:
    last = book.ledger[-1]
    anchors = (LedgerAnchor(last.position, last.entry_hash),)
    return classify_multiplicity_set((book.ledger,), anchors, LINEAGE, decl_id)


def _variants_upfront(order: tuple[str, ...] = VARIANTS, *, same_family: bool = False) -> Book:
    book = Book()
    for v in VARIANTS:
        book.register(f"P-{v}", W, policy=f"POL-{v}", family="FAM-1" if same_family else f"FAM-{v}")
    for v in order:
        book.evaluate(
            f"E-{v}", f"P-{v}", W, policy=f"POL-{v}", family="FAM-1" if same_family else f"FAM-{v}"
        )
    return book


def _outcomes(
    book: Book, ids: tuple[str, ...], lineage: LineageRegistry = LINEAGE
) -> dict[str, Outcome]:
    return {i: classify(book, i, lineage).outcome for i in ids}


# ---- C1: the original three-variant reproduction (R-B1) -----------------------------------------


def test_c1_three_preregistered_variants_evaluated_sequentially_yield_no_independent() -> None:
    book = _variants_upfront(same_family=True)
    results = {f"E-{v}": classify(book, f"E-{v}") for v in VARIANTS}
    assert all(r.outcome is Outcome.EXPLORATORY for r in results.values())
    assert results["E-a"].contending_preregistrations == ("P-b", "P-c")
    # Complete disclosure (count 3) no longer turns the set into independent evidence.
    disclosure = check_family_disclosure(book.ledger, W, LINEAGE, ["P-a", "P-b", "P-c"], 3)
    assert disclosure == ()
    assert family_outcome(list(results.values()), disclosure) is Outcome.EXPLORATORY


# ---- C2: same window, three evaluation-family ids ----------------------------------------------


def test_c2_same_window_three_family_ids_upfront_yield_no_independent() -> None:
    book = _variants_upfront()
    assert set(_outcomes(book, ("E-a", "E-b", "E-c")).values()) == {Outcome.EXPLORATORY}


def test_c2_same_window_three_family_ids_interleaved_only_first_is_independent() -> None:
    book = Book()
    for v in VARIANTS:
        book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
        book.evaluate(f"E-{v}", f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    assert _outcomes(book, ("E-a", "E-b", "E-c")) == {
        "E-a": Outcome.INDEPENDENT,
        "E-b": Outcome.EXPLORATORY,
        "E-c": Outcome.EXPLORATORY,
    }


# ---- C3: same dataset under different ids ------------------------------------------------------


def test_c3_same_content_under_three_dataset_ids_upfront_yield_no_independent() -> None:
    book = Book()
    for v in VARIANTS:
        book.register(f"P-{v}", (tgt(0, 100, dataset_id=f"DS-{v}"),), family=f"FAM-{v}")
    for v in VARIANTS:
        book.evaluate(f"E-{v}", f"P-{v}", (tgt(0, 100, dataset_id=f"DS-{v}"),), family=f"FAM-{v}")
    assert set(_outcomes(book, ("E-a", "E-b", "E-c")).values()) == {Outcome.EXPLORATORY}


# ---- C4: partially overlapping windows ---------------------------------------------------------


def test_c4_partially_overlapping_windows_contend_and_disjoint_window_does_not() -> None:
    book = Book()
    book.register("P-a", (tgt(0, 100),), family="FAM-a")
    book.register("P-b", (tgt(50, 150),), family="FAM-b")
    book.register("P-c", (tgt(150, 250),), family="FAM-c")
    book.evaluate("E-a", "P-a", (tgt(0, 100),), family="FAM-a")
    book.evaluate("E-b", "P-b", (tgt(50, 150),), family="FAM-b")
    book.evaluate("E-c", "P-c", (tgt(150, 250),), family="FAM-c")
    assert _outcomes(book, ("E-a", "E-b", "E-c")) == {
        "E-a": Outcome.EXPLORATORY,
        "E-b": Outcome.EXPLORATORY,
        "E-c": Outcome.INDEPENDENT,
    }
    assert classify(book, "E-a").contending_preregistrations == ("P-b",)


# ---- C5: derived datasets sharing source lineage -----------------------------------------------


def test_c5_derived_datasets_sharing_a_source_interval_contend() -> None:
    mid = (tgt(50, 150, dataset_id="DS-MID", content=CHILD_MID),)
    late = (tgt(100, 200, dataset_id="DS-LATE", content=CHILD_LATE),)  # shares ROOT_A [100, 150)
    book = Book()
    book.register("P-mid", mid, family="FAM-mid")
    book.register("P-late", late, family="FAM-late")
    book.evaluate("E-late", "P-late", late, family="FAM-late")
    book.evaluate("E-mid", "P-mid", mid, family="FAM-mid")
    assert set(_outcomes(book, ("E-mid", "E-late")).values()) == {Outcome.EXPLORATORY}


def test_c5_child_and_its_root_contend_through_lineage() -> None:
    late = (tgt(100, 200, dataset_id="DS-LATE", content=CHILD_LATE),)
    root = (tgt(150, 300),)
    book = Book()
    book.register("P-root", root, family="FAM-root")
    book.register("P-late", late, family="FAM-late")
    book.evaluate("E-root", "P-root", root, family="FAM-root")
    book.evaluate("E-late", "P-late", late, family="FAM-late")
    assert set(_outcomes(book, ("E-root", "E-late")).values()) == {Outcome.EXPLORATORY}


def test_c5_derived_datasets_on_disjoint_source_intervals_stay_independent() -> None:
    mid = (tgt(50, 100, dataset_id="DS-MID", content=CHILD_MID),)
    late = (tgt(150, 200, dataset_id="DS-LATE", content=CHILD_LATE),)
    book = Book()
    book.register("P-mid", mid, family="FAM-mid")
    book.register("P-late", late, family="FAM-late")
    book.evaluate("E-mid", "P-mid", mid, family="FAM-mid")
    book.evaluate("E-late", "P-late", late, family="FAM-late")
    assert set(_outcomes(book, ("E-mid", "E-late")).values()) == {Outcome.INDEPENDENT}


# ---- C6: evaluation order permutation ----------------------------------------------------------


def test_c6_three_variants_every_evaluation_order_gives_identical_outcomes() -> None:
    seen = set()
    for order in permutations(VARIANTS):
        book = _variants_upfront(order)
        seen.add(tuple(sorted(_outcomes(book, ("E-a", "E-b", "E-c")).items())))
    assert seen == {
        (("E-a", Outcome.EXPLORATORY), ("E-b", Outcome.EXPLORATORY), ("E-c", Outcome.EXPLORATORY))
    }


def test_c6_overlap_and_disjoint_mix_is_order_independent() -> None:
    """Earlier exposure of [0, 100), then P2 [50, 150) and P3 [100, 150) both registered: the
    review-era model made E3's outcome depend on whether E3 or E2 ran first."""
    seen = set()
    for first, second in (("E2", "E3"), ("E3", "E2")):
        book = Book()
        book.register("P1", W)
        book.evaluate("E1", "P1", W)
        book.register("P2", (tgt(50, 150),), family="FAM-2")
        book.register("P3", (tgt(100, 150),), family="FAM-3")
        runs = {"E2": ("P2", (tgt(50, 150),), "FAM-2"), "E3": ("P3", (tgt(100, 150),), "FAM-3")}
        for eid in (first, second):
            pid, targets, fam = runs[eid]
            book.evaluate(eid, pid, targets, family=fam)
        seen.add(tuple(sorted(_outcomes(book, ("E1", "E2", "E3")).items())))
    assert len(seen) == 1
    assert dict(next(iter(seen))) == {
        "E1": Outcome.INDEPENDENT,
        "E2": Outcome.EXPLORATORY,
        "E3": Outcome.EXPLORATORY,
    }


# ---- C7: re-registration after exposure --------------------------------------------------------


def test_c7_reregistration_after_exposure_is_exploratory_and_earlier_result_is_frozen() -> None:
    book = Book()
    book.register("P1", W)
    book.evaluate("E1", "P1", W)
    book.register("P1-r", W, policy="POL-revised", supersedes=("P1",))
    book.evaluate("E1-r", "P1-r", W, policy="POL-revised")
    assert classify(book, "E1-r").outcome is Outcome.EXPLORATORY
    assert classify(book, "E1").outcome is Outcome.INDEPENDENT  # N2: frozen at its position
    assert check_evaluation_disclosure(book.ledger, W, LINEAGE, ["E1", "E1-r"]) == ()
    assert "undisclosed evaluation E1" in check_evaluation_disclosure(
        book.ledger, W, LINEAGE, ["E1-r"]
    )


# ---- C8: missing exposure registry -------------------------------------------------------------


def test_c8_missing_exposure_registry_is_unverified() -> None:
    book = _variants_upfront()
    assert classify_evaluation((), (), LINEAGE, "E-a").outcome is Outcome.UNVERIFIED
    assert classify_evaluation(((),), (), LINEAGE, "E-a").outcome is Outcome.UNVERIFIED
    assert classify_evaluation((book.ledger,), (), LINEAGE, "E-a").outcome is Outcome.UNVERIFIED
    declare(book, "D-1", ("P-a", "P-b", "P-c"))
    assert classify_multiplicity_set((), (), LINEAGE, "D-1").outcome is Outcome.UNVERIFIED


# ---- C9: valid independent evaluation on a genuinely unexposed window --------------------------


def test_c9_single_registration_on_unexposed_window_is_independent() -> None:
    book = Book()
    book.register("P1", (tgt(300, 400),))
    book.register(
        "P-other",
        (tgt(0, 100, instrument=INST_B, dataset_id="DS-B", content=ROOT_B),),
        family="FAM-B",
    )
    book.evaluate("E1", "P1", (tgt(300, 400),))
    result = classify(book, "E1")
    assert result.outcome is Outcome.INDEPENDENT
    assert result.exposed_parts == ()
    assert result.contending_preregistrations == ()


def test_c9_withdrawal_before_any_exposure_releases_contention() -> None:
    book = Book()
    book.register("P-a", W, policy="POL-a")
    book.register("P-b", W, policy="POL-b", family="FAM-b")
    book.abandon("P-b")  # before any exposure of W
    book.evaluate("E-a", "P-a", W, policy="POL-a")
    assert classify(book, "E-a").outcome is Outcome.INDEPENDENT


def test_c9_withdrawal_after_the_evaluation_does_not_release_it() -> None:
    book = Book()
    book.register("P-a", W, policy="POL-a")
    book.register("P-b", W, policy="POL-b", family="FAM-b")
    book.evaluate("E-a", "P-a", W, policy="POL-a")
    book.abandon("P-b")
    assert classify(book, "E-a").outcome is Outcome.EXPLORATORY


# ---- C10: closure-gap partition regression (M1) ------------------------------------------------


def test_c10_weekend_straddle_partition_still_counts_only_verified_open_time() -> None:
    minute, hour, day = 60_000, 3_600_000, 86_400_000

    def ident(name: str) -> A.ArtifactIdentity:
        return A.ArtifactIdentity(f"SYN-{name}", f"sha-{name}", "v1")

    sessions = tuple(
        A.CalendarEntry(d * day, d * day + 22 * hour, "SESSION_OPEN")
        for d in range(14)
        if d % 7 < 5
    )
    weekly = A.CalendarVersion(ident("weekly"), "UTC", 0, 14 * day, sessions)
    fri_close, mon_open = 4 * day + 22 * hour, 7 * day
    p = A.partition_gap(
        fri_close - 2 * minute, mon_open + 5 * minute, calendars=(weekly,), tick_utc_verified=True
    )
    assert p.status is A.PartitionStatus.VERIFIED
    assert p.open_ms == 7 * minute  # SYNTHETIC: 2 min before close + 5 min after open


# ---- C11: quarantine re-entry regression (M3) --------------------------------------------------


REVIEWER = C.Authority("reviewer-1", "INDEPENDENT_DATA_REVIEWER")
QUANT = C.Authority("quant-1", "QUANT_APPROVER")


def _ticks(n: int, start: int = 1_000, flags: str | None = "6") -> tuple[C.TickRecord, ...]:
    return tuple(
        C.TickRecord(start + 250 * i, f"1.1{i:03d}0", f"1.1{i:03d}5", flags) for i in range(n)
    )


def _quarantine(
    records: tuple[C.TickRecord, ...],
    *,
    dataset_id: str = "ds-A",
    scope: C.Scope = "DATASET",
    window_id: str | None = None,
) -> C.RegisterUpdated:
    result = C.record_quarantine(
        b"",
        C.anchor_for(()),
        C.QuarantineRequest(
            C.CandidateDataset(dataset_id, records),
            scope,
            window_id,
            "SYN.PAIR",
            "hard:crossed",
            "CRIT-SYN",
            ("EV-1",),
            "sub-1",
            ("author-1",),
            (),
            REVIEWER,
            10,
            False,
        ),
    )
    assert isinstance(result, C.RegisterUpdated), result
    return result


def test_c11_quarantined_content_under_new_id_and_metadata_is_still_caught() -> None:
    records = _ticks(8)
    q = _quarantine(records)
    renamed = C.CandidateDataset("ds-renamed", records, (("conversion", "offset re-declared"),))
    assert C.check_reentry(renamed, q.register, q.anchor).outcome == "QUARANTINED_MATCH"
    reordered = C.CandidateDataset("ds-reordered", tuple(reversed(records)))
    result = C.check_reentry(reordered, q.register, q.anchor)
    assert (result.outcome, result.relation) == ("QUARANTINED_MATCH", "MULTISET")


# ---- C12: unknown lineage never yields INDEPENDENT ---------------------------------------------


def test_c12_unknown_lineage_of_own_target_is_unverified() -> None:
    mystery = ContentIdentity("syn-unknown", CANON)
    lineage = {**LINEAGE, mystery: DatasetLineage(mystery, INST_A, 0, 1_000, "UNKNOWN")}
    target = (tgt(300, 400, dataset_id="DS-?", content=mystery),)
    book = Book()
    book.register("P1", target)
    book.evaluate("E1", "P1", target)
    assert classify(book, "E1", lineage).outcome is Outcome.UNVERIFIED


def test_c12_unknown_lineage_of_a_possible_contender_is_unverified() -> None:
    mystery = ContentIdentity("syn-unknown", CANON)
    lineage = {**LINEAGE, mystery: DatasetLineage(mystery, INST_B, 0, 1_000, "UNKNOWN")}
    book = Book()
    book.register("P1", W)
    book.register(
        "P2", (tgt(0, 100, instrument=INST_B, dataset_id="DS-?", content=mystery),), family="FAM-2"
    )
    book.evaluate("E1", "P1", W)
    # Whether P2 contends for W cannot be decided, so E1 fails closed.
    assert classify(book, "E1", lineage).outcome is Outcome.UNVERIFIED


# ---- N1: exposure between registration and evaluation (R-B2, R-B3) -----------------------------


def test_n1_unregistered_peek_between_registration_and_run_is_exploratory() -> None:
    book = Book()
    book.register("P2", W)
    book.evaluate("E0-peek", None, W)
    book.evaluate("E2", "P2", W)
    result = classify(book, "E2")
    assert result.outcome is Outcome.EXPLORATORY
    assert "footprint exposed after registration and before this evaluation" in result.reasons


def test_n1_protected_view_between_registration_and_run_is_exploratory() -> None:
    book = Book()
    book.register("P2", W)
    book.view_protected("V1", W)
    book.evaluate("E2", "P2", W)
    assert classify(book, "E2").outcome is Outcome.EXPLORATORY


def test_n1_relabel_of_contended_result_is_refused() -> None:
    book = _variants_upfront()
    decision = request_relabel(classify(book, "E-a"), Outcome.INDEPENDENT)
    assert not decision.accepted
    assert decision.outcome is Outcome.EXPLORATORY


# ---- N1: Quant multiplicity declaration (QE-7) -------------------------------------------------


def _declared(*, quant: str = "QA-SYN", correction: str = "CORR-SYN", peek: bool = False) -> Book:
    book = Book()
    for v in VARIANTS:
        book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    declare(book, "D-1", ("P-a", "P-b", "P-c"), quant=quant, correction=correction)
    if peek:
        book.evaluate("E-peek", None, W)
    for v in VARIANTS:
        book.evaluate(f"E-{v}", f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    return book


def test_multiplicity_declaration_yields_exactly_one_qualification() -> None:
    book = _declared()
    assert verify_chain(book.ledger) == ()
    # V9 F-2: the references are free text, so by default the set is NOT gate eligible. Only an
    # injected verifier (real definition OPEN, QE-7) can yield the single qualification; this one
    # is a SYNTHETIC test double, not an authority registry.
    assert classify_set(book, "D-1").outcome is Outcome.PENDING_QUANT_AUTHORITY
    last = book.ledger[-1]
    verified = classify_multiplicity_set(
        (book.ledger,),
        (LedgerAnchor(last.position, last.entry_hash),),
        LINEAGE,
        "D-1",
        authority_verifier=lambda quant, correction: (quant, correction) == ("QA-SYN", "CORR-SYN"),
    )
    assert verified.outcome is Outcome.INDEPENDENT
    # No member can be cited on its own.
    assert set(_outcomes(book, ("E-a", "E-b", "E-c")).values()) == {Outcome.EXPLORATORY}


def test_multiplicity_declaration_without_quant_authority_or_correction_is_unverified() -> None:
    assert classify_set(_declared(quant=""), "D-1").outcome is Outcome.UNVERIFIED
    assert classify_set(_declared(correction=""), "D-1").outcome is Outcome.UNVERIFIED


def test_multiplicity_declaration_does_not_excuse_a_peek() -> None:
    assert classify_set(_declared(peek=True), "D-1").outcome is Outcome.EXPLORATORY


def test_multiplicity_declaration_after_a_member_evaluation_is_not_independent() -> None:
    book = Book()
    for v in VARIANTS:
        book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    book.evaluate("E-a", "P-a", W, policy="POL-a", family="FAM-a")
    declare(book, "D-1", ("P-a", "P-b", "P-c"))
    for v in ("b", "c"):
        book.evaluate(f"E-{v}", f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    assert classify_set(book, "D-1").outcome is Outcome.EXPLORATORY


def test_multiplicity_set_with_unevaluated_member_is_unverified() -> None:
    book = Book()
    for v in VARIANTS:
        book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    declare(book, "D-1", ("P-a", "P-b", "P-c"))
    book.evaluate("E-a", "P-a", W, policy="POL-a", family="FAM-a")
    assert classify_set(book, "D-1").outcome is Outcome.UNVERIFIED


def test_two_declared_sets_on_one_window_yield_no_qualification() -> None:
    book = Book()
    for v in ("a", "b", "c", "d"):
        book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    declare(book, "D-1", ("P-a", "P-b"))
    declare(book, "D-2", ("P-c", "P-d"))
    for v in ("a", "b", "c", "d"):
        book.evaluate(f"E-{v}", f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    assert classify_set(book, "D-1").outcome is Outcome.EXPLORATORY
    assert classify_set(book, "D-2").outcome is Outcome.EXPLORATORY


def test_registration_in_two_declarations_breaks_the_ledger() -> None:
    book = Book()
    for v in VARIANTS:
        book.register(f"P-{v}", W, policy=f"POL-{v}", family=f"FAM-{v}")
    declare(book, "D-1", ("P-a", "P-b"))
    declare(book, "D-2", ("P-b", "P-c"))
    assert "preregistration P-b declared in more than one set" in verify_chain(book.ledger)


# ---- N2: frozen outcomes, no file drawer (R-B4) ------------------------------------------------


def test_n2_later_supersede_on_other_window_does_not_change_earlier_outcome() -> None:
    book = Book()
    book.register("P1", W)
    book.evaluate("E1", "P1", W)
    before = classify(book, "E1").outcome
    book.register("P9", (tgt(500, 600),), supersedes=("P1",))
    assert before is Outcome.INDEPENDENT
    assert classify(book, "E1").outcome is Outcome.INDEPENDENT


def test_n2_voluntarily_downgraded_result_must_still_be_disclosed() -> None:
    book = Book()
    book.register("P1", W)
    book.evaluate("E1", "P1", W)
    downgrade = request_relabel(classify(book, "E1"), Outcome.EXPLORATORY)
    assert downgrade.accepted
    assert check_evaluation_disclosure(book.ledger, W, LINEAGE, []) == (
        "undisclosed evaluation E1",
    )


# ---- N3: Track C content relation feeds exposure (R-B5) ----------------------------------------


SYN_INST = "SYN.PAIR"
SYN_ALIAS = "SYN.PAIR-RELABEL"
BASE = _ticks(8, start=0)  # raw_msc 0, 250, ..., 1750 used as SYNTHETIC verified UTC ms


def _book_for(target_first: Target, target_second: Target) -> Book:
    book = Book()
    book.register("P1", (target_first,))
    book.evaluate("E1", "P1", (target_first,))
    book.register("P2", (target_second,), family="FAM-2")
    book.evaluate("E2", "P2", (target_second,), family="FAM-2")
    return book


def _target(
    instrument: str,
    records: tuple[C.TickRecord, ...],
    dataset_id: str,
    version: str = C.CANONICALIZATION_VERSION,
) -> Target:
    start, end = X.span(records)
    return Target(instrument, start, end, dataset_id, X.exposure_identity(records, version))


def test_n3_reordered_copy_under_new_id_is_same_exposure_content() -> None:
    reordered = tuple(reversed(BASE))
    assert X.content_relation(reordered, BASE) == "MULTISET"
    assert X.exposure_identity(reordered) == X.exposure_identity(BASE)
    lineage = X.lineage_registry([X.KnownDataset(SYN_INST, BASE)])
    book = _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_INST, reordered, "DS-2"))
    assert classify(book, "E2", lineage).outcome is Outcome.EXPLORATORY


def test_n3_reordered_copy_with_relabelled_instrument_is_not_eligible() -> None:
    reordered = tuple(reversed(BASE))
    lineage = X.lineage_registry([X.KnownDataset(SYN_INST, BASE)])
    book = _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_ALIAS, reordered, "DS-2"))
    assert classify(book, "E2", lineage).outcome is Outcome.UNVERIFIED


def test_n3_other_canonicalization_version_is_a_conflict_and_unverified() -> None:
    other = "nexora.md6-tick-content/0-SYN"
    first = _target(SYN_INST, BASE, "DS-1")
    second = _target(SYN_ALIAS, BASE, "DS-2", version=other)
    lineage: dict[ContentIdentity, DatasetLineage] = {
        first.content_identity: DatasetLineage(first.content_identity, SYN_INST, 0, 2_000, "ROOT"),
        second.content_identity: DatasetLineage(
            second.content_identity, SYN_ALIAS, 0, 2_000, "ROOT"
        ),
    }
    book = _book_for(first, second)
    result = classify(book, "E2", lineage)
    assert result.outcome is Outcome.UNVERIFIED
    assert any("CANONICALIZATION_CONFLICT" in r for r in result.reasons)


def test_n3_subset_under_relabelled_instrument_inherits_exposure() -> None:
    subset = BASE[2:6]
    assert X.content_relation(subset, BASE) == "SUBSET"
    lineage = X.lineage_registry(
        [X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_ALIAS, subset)]
    )
    book = _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_ALIAS, subset, "DS-SUB"))
    assert classify(book, "E2", lineage).outcome is Outcome.EXPLORATORY


def test_n3_superset_under_relabelled_instrument_is_exposed_by_its_evaluated_subset() -> None:
    subset = BASE[2:6]
    lineage = X.lineage_registry(
        [X.KnownDataset(SYN_ALIAS, subset), X.KnownDataset(SYN_INST, BASE)]
    )
    book = _book_for(_target(SYN_ALIAS, subset, "DS-SUB"), _target(SYN_INST, BASE, "DS-1"))
    assert classify(book, "E2", lineage).outcome is Outcome.EXPLORATORY


def test_n3_partial_overlap_cannot_be_mapped_and_fails_closed() -> None:
    other = BASE[4:] + _ticks(4, start=5_000)
    assert X.content_relation(other, BASE) == "PARTIAL_OVERLAP"
    lineage = X.lineage_registry([X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_ALIAS, other)])
    book = _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_ALIAS, other, "DS-OTHER"))
    assert classify(book, "E2", lineage).outcome is Outcome.UNVERIFIED


def test_n3_disjoint_content_stays_independent() -> None:
    fresh = _ticks(8, start=10_000)
    lineage = X.lineage_registry([X.KnownDataset(SYN_INST, BASE), X.KnownDataset(SYN_ALIAS, fresh)])
    book = _book_for(_target(SYN_INST, BASE, "DS-1"), _target(SYN_ALIAS, fresh, "DS-FRESH"))
    assert classify(book, "E2", lineage).outcome is Outcome.INDEPENDENT


# ---- N4: WINDOW-scope quarantine granularity (R-C3) --------------------------------------------


def test_n4_window_quarantine_blocks_containing_dataset_but_not_sibling_windows() -> None:
    records = _ticks(12)
    q = _quarantine(records[4:8], dataset_id="ds-B-w2", scope="WINDOW", window_id="W2")
    whole = C.check_reentry(C.CandidateDataset("ds-B", records), q.register, q.anchor)
    # A-24 is applied per evaluation window. The whole dataset still contains W2, so it is blocked
    # as a candidate; each sibling window is its own candidate and is not blocked by W2.
    assert (whole.outcome, whole.relation) == ("DERIVED_FROM_QUARANTINED", "SUPERSET")
    for name, part in (("W1", records[:4]), ("W3", records[8:])):
        sibling = C.check_reentry(C.CandidateDataset(f"ds-B-{name}", part), q.register, q.anchor)
        assert sibling.outcome == "NOT_KNOWN", name


# ---- N5: subset of requalified content (R-C2) --------------------------------------------------


def _requalify(
    q: C.RegisterUpdated,
    register: bytes,
    anchor: C.RegisterAnchor,
    records: tuple[C.TickRecord, ...],
    evidence: str,
    at: int,
    with_records: bool = True,
) -> C.RegisterUpdated | C.Refused:
    return C.requalify(
        register,
        anchor,
        C.RequalificationRequest(
            q.entry.entry_hash,
            C.content_identity(records),
            (evidence,),
            QUANT,
            ("author-1",),
            "SYN rationale",
            at,
            False,
            records if with_records else (),
        ),
    )


def test_n5_subset_of_quarantined_content_can_be_requalified_itself() -> None:
    records = _ticks(8)
    q = _quarantine(records)
    full = _requalify(q, q.register, q.anchor, records, "EV-new-2", 20, with_records=False)
    assert isinstance(full, C.RegisterUpdated)
    window = records[:4]
    before = C.check_reentry(C.CandidateDataset("ds-A-w1", window), full.register, full.anchor)
    assert before.outcome == "DERIVED_FROM_QUARANTINED"
    without_records = _requalify(q, full.register, full.anchor, window, "EV-new-3", 30, False)
    assert isinstance(without_records, C.Refused)
    sub = _requalify(q, full.register, full.anchor, window, "EV-new-3", 30)
    assert isinstance(sub, C.RegisterUpdated)
    after = C.check_reentry(C.CandidateDataset("ds-A-w1", window), sub.register, sub.anchor)
    assert after.outcome == "REQUALIFIED"
    assert not after.trust_granted
    history = C.requalification_history(sub.register, sub.anchor, q.entry.entry_hash)
    assert [e.kind for e in history] == ["QUARANTINE", "REQUALIFICATION", "REQUALIFICATION"]


def test_n5_records_outside_the_quarantined_content_cannot_be_requalified() -> None:
    records = _ticks(8)
    q = _quarantine(records)
    foreign = records[:3] + _ticks(1, start=90_000)
    refused = _requalify(q, q.register, q.anchor, foreign, "EV-new-2", 20)
    assert isinstance(refused, C.Refused)
    mismatched = C.requalify(
        q.register,
        q.anchor,
        C.RequalificationRequest(
            q.entry.entry_hash,
            C.content_identity(records[:4]),
            ("EV-x",),
            QUANT,
            ("author-1",),
            "SYN",
            20,
            False,
            records[:3],
        ),
    )
    assert isinstance(mismatched, C.Refused)


def test_n5_sibling_window_of_requalified_subset_stays_blocked() -> None:
    records = _ticks(8)
    q = _quarantine(records)
    sub = _requalify(q, q.register, q.anchor, records[:4], "EV-new-2", 20)
    assert isinstance(sub, C.RegisterUpdated)
    other = C.check_reentry(C.CandidateDataset("ds-A-w2", records[4:]), sub.register, sub.anchor)
    assert other.outcome == "DERIVED_FROM_QUARANTINED"


# ---- N6: flags presence change (R-C1) ----------------------------------------------------------


def test_n6_reextraction_without_flags_is_representation_drift() -> None:
    records = _ticks(8)
    q = _quarantine(records)
    no_flags = C.CandidateDataset("ds-A-reextract", tuple(replace(r, flags=None) for r in records))
    result = C.check_reentry(no_flags, q.register, q.anchor)
    assert (result.outcome, result.relation) == (
        "CANONICALIZATION_CONFLICT",
        "REPRESENTATION_DRIFT",
    )
    assert not result.may_proceed_to_part_a


def test_n6_reextraction_with_other_flags_is_representation_drift() -> None:
    records = _ticks(8, flags=None)
    q = _quarantine(records)
    flagged = C.CandidateDataset("ds-A-flags", tuple(replace(r, flags="2") for r in records))
    assert C.check_reentry(flagged, q.register, q.anchor).outcome == "CANONICALIZATION_CONFLICT"
