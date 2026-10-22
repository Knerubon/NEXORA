"""Adversarial tests for the MD-6 evaluation exposure lineage rule.

Executable specification of the MD-6 policy PROPOSAL (Quant sign-off PENDING). Test-only reference
model; not runtime enforcement.

All identities, windows and timestamps are SYNTHETIC. No outcome value (P/L, win rate, trades)
exists anywhere in these fixtures. Ledger order is by position; recorded_at is a synthetic counter.
"""

from __future__ import annotations

from dataclasses import replace

from tests.md6_exposure_reference import (
    Abandonment,
    Assessment,
    ContentIdentity,
    DatasetLineage,
    Evaluation,
    ExposureStatus,
    Ledger,
    LedgerAnchor,
    LineageEdge,
    Outcome,
    Piece,
    ProtectedStatisticsView,
    Registration,
    Target,
    append,
    check_family_disclosure,
    classify_evaluation,
    collide,
    family_outcome,
    old_rule_d15_gate_eligible,
    old_rule_d16_view_permitted,
    registrations_touching,
    request_relabel,
    verify_chain,
)

CANON = "canon/SYNTHETIC-0"
INST_A = "SYN.INSTR-A"
INST_B = "SYN.INSTR-B"
ROOT_A = ContentIdentity("syn-content-root-a", CANON)
ROOT_B = ContentIdentity("syn-content-root-b", CANON)
CHILD_MID = ContentIdentity("syn-content-child-mid", CANON)
CHILD_LATE = ContentIdentity("syn-content-child-late", CANON)

LINEAGE: dict[ContentIdentity, DatasetLineage] = {
    ROOT_A: DatasetLineage(ROOT_A, INST_A, 0, 1_000, "ROOT"),
    ROOT_B: DatasetLineage(ROOT_B, INST_B, 0, 1_000, "ROOT"),
    # SYNTHETIC derived datasets (e.g. a resample) of ROOT_A, time preserved.
    CHILD_MID: DatasetLineage(
        CHILD_MID, INST_A, 50, 150, "DERIVED", (LineageEdge(ROOT_A, INST_A, 50, 150),)
    ),
    CHILD_LATE: DatasetLineage(
        CHILD_LATE, INST_A, 100, 200, "DERIVED", (LineageEdge(ROOT_A, INST_A, 100, 200),)
    ),
}


def tgt(
    start: int,
    end: int,
    *,
    instrument: str = INST_A,
    dataset_id: str = "DS-1",
    content: ContentIdentity = ROOT_A,
) -> Target:
    return Target(instrument, start, end, dataset_id, content)


class Book:
    """Builds a synthetic ledger; every append advances a synthetic clock by one."""

    def __init__(self) -> None:
        self.ledger: Ledger = ()
        self.clock = 0
        self.reg_ts: dict[str, int] = {}

    def _tick(self) -> int:
        self.clock += 1
        return self.clock

    def register(
        self,
        prereg_id: str,
        targets: tuple[Target, ...],
        *,
        family: str = "FAM-1",
        strategy: str = "STRAT-v1",
        policy: str = "POLICY-v1",
        prior: ExposureStatus = "UNEXPOSED",
        supersedes: tuple[str, ...] = (),
    ) -> None:
        ts = self._tick()
        self.reg_ts[prereg_id] = ts
        record = Registration(
            prereg_id,
            strategy,
            policy,
            family,
            targets,
            prior,
            ts,
            "DEC-SYN",
            "APR-SYN",
            supersedes,
        )
        self.ledger = append(self.ledger, record, ts)

    def evaluate(
        self,
        eval_id: str,
        prereg_id: str | None,
        targets: tuple[Target, ...],
        *,
        family: str = "FAM-1",
        strategy: str = "STRAT-v1",
        policy: str = "POLICY-v1",
        prior: ExposureStatus = "UNEXPOSED",
        registration_ts: int | None = None,
    ) -> None:
        ts = self._tick()
        if registration_ts is None and prereg_id is not None:
            registration_ts = self.reg_ts.get(prereg_id)
        record = Evaluation(
            eval_id,
            prereg_id,
            strategy,
            policy,
            family,
            targets,
            prior,
            registration_ts,
            "DEC-SYN",
            "APR-SYN",
            ts,
        )
        self.ledger = append(self.ledger, record, ts)

    def view_protected(self, view_id: str, targets: tuple[Target, ...]) -> None:
        ts = self._tick()
        self.ledger = append(
            self.ledger, ProtectedStatisticsView(view_id, targets, "VIEWER-SYN", ts), ts
        )

    def abandon(self, prereg_id: str) -> None:
        ts = self._tick()
        self.ledger = append(self.ledger, Abandonment(prereg_id, "REASON-SYN", ts), ts)

    def anchors(self) -> tuple[LedgerAnchor, ...]:
        last = self.ledger[-1]
        return (LedgerAnchor(last.position, last.entry_hash),)

    def classify(self, eval_id: str) -> Assessment:
        return classify_evaluation((self.ledger,), self.anchors(), LINEAGE, eval_id)


W = (tgt(0, 100),)  # SYNTHETIC evaluation window [0, 100) on INST_A


def _first_evaluation_seen() -> Book:
    """P1 registered on W, then evaluated (outcomes observed; values never recorded)."""
    book = Book()
    book.register("P1", W)
    book.evaluate("E1", "P1", W)
    return book


def test_baseline_first_evaluation_is_independent() -> None:
    book = _first_evaluation_seen()
    assert book.classify("E1").outcome is Outcome.INDEPENDENT
    assert verify_chain(book.ledger) == ()


# ---- B1 -------------------------------------------------------------------------------------


def test_b1_identical_windows_new_family_id_is_exploratory() -> None:
    book = _first_evaluation_seen()
    book.register("P2", W, family="FAM-NEW")
    book.evaluate("E2", "P2", W, family="FAM-NEW")
    result = book.classify("E2")
    assert result.outcome is Outcome.EXPLORATORY
    assert result.exposed_parts == (Piece(INST_A, 0, 100, ROOT_A),)


def test_b1_old_rule_new_family_resets_and_wrongly_admits() -> None:
    book = _first_evaluation_seen()
    book.register("P2", W, family="FAM-NEW")
    book.evaluate("E2", "P2", W, family="FAM-NEW")
    # PR-head D15 text: new preregistration id + new evaluation family => eligible. Wrong.
    assert old_rule_d15_gate_eligible(book.ledger, "E2") is True
    assert book.classify("E2").outcome is Outcome.EXPLORATORY


# ---- B2 -------------------------------------------------------------------------------------


def test_b2_identical_data_new_dataset_id_is_exploratory() -> None:
    book = _first_evaluation_seen()
    renamed = (tgt(0, 100, dataset_id="DS-RENAMED"),)  # same content_identity ROOT_A
    book.register("P2", renamed)
    book.evaluate("E2", "P2", renamed)
    assert old_rule_d15_gate_eligible(book.ledger, "E2") is True  # old rule: new dataset id
    assert book.classify("E2").outcome is Outcome.EXPLORATORY


def test_b2_instrument_relabel_with_same_content_is_not_eligible() -> None:
    book = _first_evaluation_seen()
    alias = (tgt(0, 100, instrument="SYN.INSTR-A-ALIAS", dataset_id="DS-ALIAS"),)
    book.register("P2", alias)
    book.evaluate("E2", "P2", alias)
    # Label conflicts with the content's recorded instrument: identity unverifiable.
    assert book.classify("E2").outcome is Outcome.UNVERIFIED


def test_b2_content_key_collides_across_instrument_labels() -> None:
    a = Piece("SYN.X", 0, 100, ROOT_A)
    b = Piece("SYN.Y", 40, 60, ROOT_A)
    assert collide(a, b) == Piece("SYN.X", 40, 60, ROOT_A)
    assert collide(a, Piece("SYN.Y", 40, 60, ROOT_B)) is None


# ---- B3 -------------------------------------------------------------------------------------


def test_b3_overlapping_window_is_exploratory_and_disjoint_part_is_eligible() -> None:
    book = _first_evaluation_seen()  # [0, 100) exposed
    overlapping = (tgt(50, 150),)
    disjoint = (tgt(100, 150),)
    book.register("P2", overlapping, family="FAM-2")
    book.register("P3", disjoint, family="FAM-3")  # before any exposure of [100, 150)
    book.evaluate("E3", "P3", disjoint, family="FAM-3")
    book.evaluate("E2", "P2", overlapping, family="FAM-2")
    e2 = book.classify("E2")
    assert e2.outcome is Outcome.EXPLORATORY
    assert e2.exposed_parts == (Piece(INST_A, 50, 100, ROOT_A),)
    assert book.classify("E3").outcome is Outcome.INDEPENDENT


def test_b3_disjoint_part_registered_after_its_exposure_is_exploratory() -> None:
    book = _first_evaluation_seen()
    book.register("P2", (tgt(50, 150),), family="FAM-2")
    book.evaluate("E2", "P2", (tgt(50, 150),), family="FAM-2")  # exposes [100, 150) too
    book.register("P3", (tgt(100, 150),), family="FAM-3")
    book.evaluate("E3", "P3", (tgt(100, 150),), family="FAM-3")
    assert book.classify("E3").outcome is Outcome.EXPLORATORY


# ---- B4 -------------------------------------------------------------------------------------


def test_b4_reregister_policy_after_viewing_results_is_exploratory() -> None:
    book = _first_evaluation_seen()
    # SYNTHETIC: POLICY-v2 stands for a different (OPEN) max_gap_ms; no value is implied.
    book.register("P2", W, policy="POLICY-v2", supersedes=("P1",))
    book.evaluate("E2", "P2", W, policy="POLICY-v2")
    assert book.classify("E2").outcome is Outcome.EXPLORATORY
    # The earlier result is now superseded: also not gate-eligible, and must be disclosed.
    assert book.classify("E1").outcome is Outcome.EXPLORATORY


# ---- B5 -------------------------------------------------------------------------------------


HOLDOUT = (tgt(500, 600, dataset_id="DS-HOLDOUT"),)


def test_b5_strategy_change_after_holdout_exposure_is_exploratory() -> None:
    book = Book()
    book.register("P1", HOLDOUT, strategy="STRAT-v1")
    book.evaluate("E1", "P1", HOLDOUT, strategy="STRAT-v1")
    book.register("P2", HOLDOUT, strategy="STRAT-v2", family="FAM-HOLDOUT-2")
    book.evaluate("E2", "P2", HOLDOUT, strategy="STRAT-v2", family="FAM-HOLDOUT-2")
    assert book.classify("E1").outcome is Outcome.INDEPENDENT
    assert book.classify("E2").outcome is Outcome.EXPLORATORY


def test_b5_changed_strategy_under_original_registration_is_exploratory() -> None:
    book = Book()
    book.register("P1", HOLDOUT, strategy="STRAT-v1")
    book.evaluate("E1", "P1", HOLDOUT, strategy="STRAT-v1")
    book.evaluate("E1b", "P1", HOLDOUT, strategy="STRAT-v2")
    result = book.classify("E1b")
    assert result.outcome is Outcome.EXPLORATORY
    assert "evaluation deviates from registration in strategy_version" in result.reasons


# ---- B6 -------------------------------------------------------------------------------------


def test_b6_relabel_exploratory_as_independent_is_refused() -> None:
    book = _first_evaluation_seen()
    book.register("P2", W, family="FAM-NEW")
    book.evaluate("E2", "P2", W, family="FAM-NEW")
    current = book.classify("E2")
    decision = request_relabel(current, Outcome.INDEPENDENT)
    assert decision.accepted is False
    assert decision.outcome is Outcome.EXPLORATORY
    # A fresh registration appended afterwards cannot launder E2 either.
    book.register("P3", W, family="FAM-LAUNDER")
    assert book.classify("E2").outcome is Outcome.EXPLORATORY
    # Voluntary downgrade is the only manual change permitted.
    clean = Book()
    clean.register("Q1", W)
    clean.evaluate("F1", "Q1", W)
    assert request_relabel(clean.classify("F1"), Outcome.EXPLORATORY).accepted is True
    assert (
        request_relabel(Assessment(Outcome.UNVERIFIED, ()), Outcome.INDEPENDENT).accepted is False
    )


def test_b6_false_unexposed_declaration_does_not_help() -> None:
    book = _first_evaluation_seen()
    book.register("P2", W, family="FAM-NEW", prior="UNEXPOSED")
    book.evaluate("E2", "P2", W, family="FAM-NEW", prior="UNEXPOSED")
    result = book.classify("E2")
    assert result.outcome is Outcome.EXPLORATORY
    assert "registration declares UNEXPOSED but ledger shows EXPOSED" in result.reasons


def test_declared_exposed_on_clean_ledger_fails_closed_to_exploratory() -> None:
    book = Book()
    book.register("P1", W, prior="EXPOSED")
    book.evaluate("E1", "P1", W, prior="EXPOSED")
    assert book.classify("E1").outcome is Outcome.EXPLORATORY


# ---- B7 -------------------------------------------------------------------------------------


def test_b7_partially_overlapping_derived_dataset_is_exploratory() -> None:
    book = _first_evaluation_seen()  # ROOT_A [0, 100) exposed
    child = (tgt(50, 150, dataset_id="DS-CHILD", content=CHILD_MID),)
    book.register("P2", child, family="FAM-CHILD")
    book.evaluate("E2", "P2", child, family="FAM-CHILD")
    result = book.classify("E2")
    assert result.outcome is Outcome.EXPLORATORY
    assert Piece(INST_A, 50, 100, CHILD_MID) in result.exposed_parts


def test_b7_derived_dataset_on_unexposed_interval_is_independent() -> None:
    book = _first_evaluation_seen()
    child = (tgt(100, 200, dataset_id="DS-CHILD-LATE", content=CHILD_LATE),)
    book.register("P2", child, family="FAM-CHILD")
    book.evaluate("E2", "P2", child, family="FAM-CHILD")
    assert book.classify("E2").outcome is Outcome.INDEPENDENT


def test_b7_exposure_of_child_taints_parent() -> None:
    book = Book()
    child = (tgt(50, 150, dataset_id="DS-CHILD", content=CHILD_MID),)
    book.evaluate("X1", None, child)  # unregistered exploratory run on the child
    book.register("P1", (tgt(0, 300),))
    book.evaluate("E1", "P1", (tgt(0, 300),))
    result = book.classify("E1")
    assert result.outcome is Outcome.EXPLORATORY
    assert Piece(INST_A, 50, 150, ROOT_A) in result.exposed_parts


# ---- B8 -------------------------------------------------------------------------------------


def test_b8_broken_hash_chain_is_unverified() -> None:
    book = _first_evaluation_seen()
    book.register("P2", (tgt(300, 400),), family="FAM-2")
    book.evaluate("E2", "P2", (tgt(300, 400),), family="FAM-2")
    original = book.ledger[0].record
    assert isinstance(original, Registration)
    tampered_reg = replace(book.ledger[0], record=replace(original, targets=()))
    ledger = (tampered_reg, *book.ledger[1:])
    result = classify_evaluation((ledger,), book.anchors(), LINEAGE, "E2")
    assert result.outcome is Outcome.UNVERIFIED


def test_b8_gap_in_ledger_is_unverified() -> None:
    book = _first_evaluation_seen()  # E1 at position 1 is the exposure
    book.register("P2", W, family="FAM-NEW")
    book.evaluate("E2", "P2", W, family="FAM-NEW")
    ledger = (book.ledger[0], *book.ledger[2:])  # drop the inconvenient exposure entry
    result = classify_evaluation((ledger,), book.anchors(), LINEAGE, "E2")
    assert result.outcome is Outcome.UNVERIFIED


def test_b8_two_ledgers_disagree_is_unverified() -> None:
    base = _first_evaluation_seen()
    fork_a = Book()
    fork_a.ledger, fork_a.clock, fork_a.reg_ts = base.ledger, base.clock, dict(base.reg_ts)
    fork_b = Book()
    fork_b.ledger, fork_b.clock, fork_b.reg_ts = base.ledger, base.clock, dict(base.reg_ts)
    fork_a.register("P2", (tgt(300, 400),), family="FAM-2")
    fork_a.evaluate("E2", "P2", (tgt(300, 400),), family="FAM-2")
    fork_b.view_protected("V1", (tgt(300, 400),))
    fork_b.register("P2", (tgt(300, 400),), family="FAM-2")
    result = classify_evaluation((fork_a.ledger, fork_b.ledger), fork_a.anchors(), LINEAGE, "E2")
    assert result.outcome is Outcome.UNVERIFIED
    # A stale but consistent replica (strict prefix) is not a conflict.
    ok = classify_evaluation((fork_a.ledger, base.ledger), fork_a.anchors(), LINEAGE, "E2")
    assert ok.outcome is Outcome.INDEPENDENT


def test_b8_tail_truncated_against_anchor_is_unverified() -> None:
    book = _first_evaluation_seen()
    book.register("P2", (tgt(300, 400),), family="FAM-2")
    book.evaluate("E2", "P2", (tgt(300, 400),), family="FAM-2")
    anchors = book.anchors()
    book.view_protected("V-LATE", (tgt(900, 950),))
    late_anchor = book.anchors()
    truncated = book.ledger[:-1]
    assert classify_evaluation((truncated,), anchors, LINEAGE, "E2").outcome is (
        Outcome.INDEPENDENT
    )
    result = classify_evaluation((truncated,), late_anchor, LINEAGE, "E2")
    assert result.outcome is Outcome.UNVERIFIED


def test_b8_missing_history_and_unknown_lineage_are_unverified() -> None:
    book = _first_evaluation_seen()
    assert classify_evaluation((), book.anchors(), LINEAGE, "E1").outcome is Outcome.UNVERIFIED
    assert classify_evaluation((book.ledger,), (), LINEAGE, "E1").outcome is Outcome.UNVERIFIED
    unknown = dict(LINEAGE)
    unknown[ROOT_A] = DatasetLineage(ROOT_A, INST_A, 0, 1_000, "UNKNOWN")
    assert classify_evaluation((book.ledger,), book.anchors(), unknown, "E1").outcome is (
        Outcome.UNVERIFIED
    )
    missing_parent = {k: v for k, v in LINEAGE.items() if k != ROOT_A}
    child = (tgt(100, 200, dataset_id="DS-CHILD-LATE", content=CHILD_LATE),)
    book.register("P2", child, family="FAM-CHILD")
    book.evaluate("E2", "P2", child, family="FAM-CHILD")
    assert (
        classify_evaluation((book.ledger,), book.anchors(), missing_parent, "E2").outcome
        is Outcome.UNVERIFIED
    )


def test_b8_missing_required_identity_is_unverified() -> None:
    book = Book()
    book.register("P1", W)
    reg = book.ledger[0].record
    assert isinstance(reg, Registration)
    bad = Book()
    bad.ledger = append((), replace(reg, approval_ref=""), reg.registration_timestamp)
    bad.clock, bad.reg_ts = book.clock, dict(book.reg_ts)
    bad.evaluate("E1", "P1", W)
    assert bad.classify("E1").outcome is Outcome.UNVERIFIED


def test_b8_registration_timestamp_conflict_is_unverified() -> None:
    book = Book()
    book.register("P1", W)
    book.evaluate("E1", "P1", W, registration_ts=999)
    assert book.classify("E1").outcome is Outcome.UNVERIFIED


# ---- B9 -------------------------------------------------------------------------------------


def test_b9_retrospective_registration_is_rejected() -> None:
    book = Book()
    book.evaluate("E1", "P1", W, registration_ts=2)  # claims a registration not yet made
    book.register("P1", W)  # registered after the evaluation it covers
    result = book.classify("E1")
    assert result.outcome is Outcome.REJECTED_RETROSPECTIVE
    assert old_rule_d15_gate_eligible(book.ledger, "E1") is False


# ---- B10 ------------------------------------------------------------------------------------


def test_b10_future_evaluation_on_unexposed_data_is_independent() -> None:
    book = _first_evaluation_seen()  # INST_A [0, 100) exposed
    book.register("P2", W, family="FAM-NEW")
    book.evaluate("E2", "P2", W, family="FAM-NEW")  # exploratory, exposes again
    fresh = (tgt(600, 700), tgt(0, 100, instrument=INST_B, dataset_id="DS-B", content=ROOT_B))
    book.register("P3", fresh, family="FAM-FUTURE", strategy="STRAT-v2")
    book.evaluate("E3", "P3", fresh, family="FAM-FUTURE", strategy="STRAT-v2")
    result = book.classify("E3")
    assert result.outcome is Outcome.INDEPENDENT
    assert result.exposed_parts == ()


# ---- D16 gap and holdout statistics ---------------------------------------------------------


def test_d16_protected_statistics_view_counts_as_exposure() -> None:
    book = Book()
    book.register("P1", HOLDOUT, family="FAM-H1")
    book.view_protected("V1", HOLDOUT)  # gap counts viewed after P1: permitted by old D16
    book.register("P2", HOLDOUT, family="FAM-H2")  # e.g. a different max_gap_ms (OPEN)
    book.evaluate("E2", "P2", HOLDOUT, family="FAM-H2")
    assert old_rule_d16_view_permitted(book.ledger, "V1") is True
    assert old_rule_d15_gate_eligible(book.ledger, "E2") is True
    assert book.classify("E2").outcome is Outcome.EXPLORATORY


# ---- Other rules ----------------------------------------------------------------------------


def test_unregistered_evaluation_is_exploratory() -> None:
    book = Book()
    book.evaluate("X1", None, W)
    assert book.classify("X1").outcome is Outcome.EXPLORATORY


def test_repeat_evaluation_under_one_registration_is_exploratory() -> None:
    book = _first_evaluation_seen()
    book.evaluate("E1-rerun", "P1", W)
    assert book.classify("E1").outcome is Outcome.INDEPENDENT
    assert book.classify("E1-rerun").outcome is Outcome.EXPLORATORY


def test_abandoned_registration_is_exploratory_and_disclosed() -> None:
    book = _first_evaluation_seen()
    book.abandon("P1")
    assert book.classify("E1").outcome is Outcome.EXPLORATORY
    touching = registrations_touching(book.ledger, W, LINEAGE)
    assert [(d.preregistration_id, d.status) for d in touching] == [("P1", "ABANDONED")]


def test_family_disclosure_lists_all_preregistrations_with_count() -> None:
    book = _first_evaluation_seen()
    book.register("P2", W, policy="POLICY-v2", supersedes=("P1",))
    book.register("P3", (tgt(50, 150),), family="FAM-3")
    book.abandon("P3")
    book.register("P4", (tgt(800, 900),), family="FAM-4")  # does not touch W
    touching = registrations_touching(book.ledger, W, LINEAGE)
    assert [(d.preregistration_id, d.status) for d in touching] == [
        ("P1", "SUPERSEDED"),
        ("P2", "ACTIVE"),
        ("P3", "ABANDONED"),
    ]
    complete = check_family_disclosure(book.ledger, W, LINEAGE, ["P1", "P2", "P3"], 3)
    assert complete == ()
    hidden = check_family_disclosure(book.ledger, W, LINEAGE, ["P2"], 1)
    assert "undisclosed preregistration P1" in hidden
    assert "undisclosed preregistration P3" in hidden
    wrong_count = check_family_disclosure(book.ledger, W, LINEAGE, ["P1", "P2", "P3"], 2)
    assert wrong_count != ()


def test_family_outcome_requires_complete_disclosure() -> None:
    clean = Book()
    clean.register("Q1", W)
    clean.evaluate("F1", "Q1", W)
    a = clean.classify("F1")
    assert family_outcome([a], ()) is Outcome.INDEPENDENT
    assert family_outcome([a], ("undisclosed preregistration Q0",)) is Outcome.UNVERIFIED
    assert family_outcome([a, Assessment(Outcome.EXPLORATORY, ())], ()) is Outcome.EXPLORATORY
    assert family_outcome([], ()) is Outcome.UNVERIFIED


def test_ledger_records_carry_no_outcome_fields() -> None:
    forbidden = {"pnl", "profit", "win_rate", "trades", "expectancy", "drawdown", "signals"}
    for cls in (Registration, Evaluation, ProtectedStatisticsView, Abandonment, Target):
        fields = set(cls.__dataclass_fields__)
        assert not fields & forbidden, cls.__name__
