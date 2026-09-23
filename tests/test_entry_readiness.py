from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from nexora.entry_readiness import EntryReadinessSnapshot, evaluate_entry_readiness
from nexora.experience import ExperienceService
from nexora.experience.engine import fingerprint
from nexora.research import ResearchPipeline
from nexora.research.runtime import RuntimeConfig
from nexora.signals.models import SignalAction, SignalDecision
from nexora.storage import SQLiteJournal
from nexora.trendline.models import (
    TrendlineAnchor,
    TrendlineKind,
    TrendlineLifecycleState,
    TrendlineLine,
    TrendlineSnapshot,
)

from tests.test_readiness_regressions import events, pipeline_config

SYMBOL = "XAUUSD"
BASE = datetime(2026, 1, 1, tzinfo=UTC)

ALIGNED_KIND: dict[SignalAction, TrendlineKind] = {
    "BUY": "bullish_support",
    "SELL": "bearish_resistance",
}
OPPOSITE_KIND: dict[SignalAction, TrendlineKind] = {
    "BUY": "bearish_resistance",
    "SELL": "bullish_support",
}


def _decision(action: SignalAction) -> SignalDecision:
    return SignalDecision(
        action=action,
        score=80 if action != "WAIT" else 0,
        entry_zone=None,
        invalidation_price=None,
        invalidation_reason=None,
        targets=(),
        risk_reward=None,
        patterns=(),
        positive_evidence=(),
        negative_evidence=(),
        future_conditions=(),
        config_version="test-v1",
        engine_version="test-v1:entry-readiness",
        source_refs=(),
    )


def _anchor(
    kind: Literal["high", "low"], price: str, column_id: int, pivot_id: str
) -> TrendlineAnchor:
    return TrendlineAnchor(
        pivot_kind=kind,
        price=Decimal(price),
        column_id=column_id,
        source_pivot_id=pivot_id,
        event_time=BASE,
    )


def _line(
    kind: TrendlineKind, state: TrendlineLifecycleState, line_id: str = "line-1"
) -> TrendlineLine:
    pivot_kind: Literal["high", "low"] = "low" if kind == "bullish_support" else "high"
    anchor_a = _anchor(pivot_kind, "100.0", 2, "tr-2")
    anchor_b = _anchor(pivot_kind, "101.5", 5, "tr-5")
    return TrendlineLine(
        line_id=line_id,
        kind=kind,
        state=state,
        anchor_a=anchor_a,
        anchor_b=anchor_b,
        slope_price_per_column=Decimal("0.5"),
        projected_price_at_latest_column=Decimal("101.5"),
        touch_columns=(),
        break_column=None,
        break_transition_id=None,
        retest_column=None,
        retest_resolved_column=None,
        retest_resolved_sequence=None,
        retest_outcome="none",
        replaced_by_line_id=None,
        age_columns=0,
        config_version="p3-fixed-v1",
        evidence=(),
    )


def _trendline(
    *, bullish: TrendlineLine | None = None, bearish: TrendlineLine | None = None
) -> TrendlineSnapshot:
    return TrendlineSnapshot(
        schema_version=1,
        symbol=SYMBOL,
        sequence=1,
        active_bullish=bullish,
        active_bearish=bearish,
        history=(),
    )


def _aligned_trendline(
    action: SignalAction, state: TrendlineLifecycleState | None
) -> TrendlineSnapshot:
    if state is None:
        return _trendline()
    kind = ALIGNED_KIND[action]
    line = _line(kind, state)
    if kind == "bullish_support":
        return _trendline(bullish=line)
    return _trendline(bearish=line)


def _eval(
    action: SignalAction, state: TrendlineLifecycleState | None
) -> EntryReadinessSnapshot:
    return evaluate_entry_readiness(
        decision=_decision(action),
        trendline=_aligned_trendline(action, state),
        config_version="cv1",
    )


# --- decision table (ADR-021 Decision 5), both directions -----------------------------------

STATES: tuple[TrendlineLifecycleState | None, ...] = (
    None,
    "active",
    "broken",
    "retesting",
    "retest_held",
    "retest_failed",
)


@pytest.mark.parametrize("action", ["BUY", "SELL"])
@pytest.mark.parametrize("state", STATES)
def test_decision_table_row(action: SignalAction, state: TrendlineLifecycleState | None) -> None:
    snap = _eval(action, state)
    expected = {
        None: "READY",
        "active": "READY",
        "broken": "BLOCKED",
        "retesting": "DEVELOPING",
        "retest_held": "BLOCKED",
        "retest_failed": "READY",
    }[state]
    assert snap.state == expected
    assert snap.signal_action == action


def test_wait_never_produces_ready_or_blocked() -> None:
    for state in STATES:
        for action in ("BUY", "SELL"):  # trendline aligned kind is irrelevant to WAIT
            snap = evaluate_entry_readiness(
                decision=_decision("WAIT"),
                trendline=_aligned_trendline(action, state),
                config_version="cv1",
            )
            assert snap.state == "NOT_READY"
            assert snap.signal_action == "WAIT"
            assert snap.blockers == ()
            assert snap.pending_confirmations == ()


def test_missing_aligned_trendline_is_ready_for_buy_and_sell() -> None:
    for action in ("BUY", "SELL"):
        snap = _eval(action, None)
        assert snap.state == "READY"
        assert snap.blockers == ()
        assert snap.pending_confirmations == ()


def test_active_aligned_trendline_is_ready() -> None:
    snap = _eval("BUY", "active")
    assert snap.state == "READY"
    assert snap.blockers == ()
    assert snap.pending_confirmations == ()


def test_broken_aligned_trendline_is_blocked_with_exactly_one_blocker() -> None:
    snap = _eval("BUY", "broken")
    assert snap.state == "BLOCKED"
    assert len(snap.blockers) == 1
    assert snap.blockers[0].code == "aligned_trendline_broken"
    assert snap.blockers[0].side == "BUY"
    assert snap.blockers[0].trendline_kind == "bullish_support"
    assert snap.pending_confirmations == ()


def test_retest_held_is_blocked_with_exactly_one_blocker() -> None:
    snap = _eval("SELL", "retest_held")
    assert snap.state == "BLOCKED"
    assert len(snap.blockers) == 1
    assert snap.blockers[0].code == "aligned_trendline_retest_held"
    assert snap.blockers[0].trendline_kind == "bearish_resistance"
    assert snap.pending_confirmations == ()


def test_retesting_is_developing_with_exactly_one_pending_and_zero_blockers() -> None:
    snap = _eval("BUY", "retesting")
    assert snap.state == "DEVELOPING"
    assert snap.blockers == ()
    assert len(snap.pending_confirmations) == 1
    assert snap.pending_confirmations[0].code == "aligned_trendline_retest_pending"


def test_retest_failed_is_ready_with_zero_blockers_and_zero_pending() -> None:
    snap = _eval("SELL", "retest_failed")
    assert snap.state == "READY"
    assert snap.blockers == ()
    assert snap.pending_confirmations == ()


# --- opposite-kind trendline has zero effect (ADR-021 Decision 4) ---------------------------


_OPPOSITE_STATES: tuple[TrendlineLifecycleState, ...] = (
    "active",
    "broken",
    "retesting",
    "retest_held",
    "retest_failed",
)


@pytest.mark.parametrize("action", ["BUY", "SELL"])
@pytest.mark.parametrize("opposite_state", _OPPOSITE_STATES)
def test_opposite_kind_trendline_never_changes_state(
    action: SignalAction, opposite_state: TrendlineLifecycleState
) -> None:
    opposite_kind = OPPOSITE_KIND[action]
    opposite_line = _line(opposite_kind, opposite_state, line_id="opposite-line")
    trendline = (
        _trendline(bearish=opposite_line)
        if opposite_kind == "bearish_resistance"
        else _trendline(bullish=opposite_line)
    )
    with_conflict = evaluate_entry_readiness(
        decision=_decision(action), trendline=trendline, config_version="cv1"
    )
    without_conflict = evaluate_entry_readiness(
        decision=_decision(action), trendline=_trendline(), config_version="cv1"
    )
    assert with_conflict.state == without_conflict.state == "READY"
    assert with_conflict.blockers == ()
    assert with_conflict.pending_confirmations == ()


# --- purity / non-mutation / determinism -----------------------------------------------------


def test_never_mutates_or_replaces_signal_decision() -> None:
    decision = _decision("BUY")
    snapshot_before = replace(decision)
    evaluate_entry_readiness(
        decision=decision, trendline=_aligned_trendline("BUY", "broken"), config_version="cv1"
    )
    assert decision == snapshot_before
    assert decision.action == "BUY"


@pytest.mark.parametrize("action", ["WAIT", "BUY", "SELL"])
@pytest.mark.parametrize("state", STATES)
def test_deterministic_repeatability(
    action: SignalAction, state: TrendlineLifecycleState | None
) -> None:
    decision = _decision(action)
    trendline = _aligned_trendline(action if action != "WAIT" else "BUY", state)
    first = evaluate_entry_readiness(decision=decision, trendline=trendline, config_version="cv1")
    second = evaluate_entry_readiness(decision=decision, trendline=trendline, config_version="cv1")
    assert first == second


# --- prefix invariance through the real pipeline (ADR-021 Decision 11) ----------------------


def test_prefix_invariance_through_pipeline() -> None:
    all_events = events()
    prefix_len = 5

    prefix_pipeline = ResearchPipeline(pipeline_config())
    prefix_snapshots = [
        prefix_pipeline.process(e)["entry_readiness"] for e in all_events[:prefix_len]
    ]

    full_pipeline = ResearchPipeline(pipeline_config())
    full_snapshots = [full_pipeline.process(e)["entry_readiness"] for e in all_events]

    assert prefix_snapshots == full_snapshots[:prefix_len]


def test_entry_readiness_wired_into_pipeline_output_and_consistent_with_signal() -> None:
    pipeline = ResearchPipeline(pipeline_config())
    for event in events():
        output = pipeline.process(event)
        readiness = output["entry_readiness"]
        decision = output["signals"]["decision"]
        assert readiness["signal_action"] == decision["action"]
        if decision["action"] == "WAIT":
            assert readiness["state"] == "NOT_READY"
            assert readiness["blockers"] == []
            assert readiness["pending_confirmations"] == []
        else:
            assert readiness["state"] in ("READY", "DEVELOPING", "BLOCKED")


# --- Experience boundary (ADR-021 Decision 12) -----------------------------------------------


def _experience_output(entry_readiness: object) -> dict[str, object]:
    return {
        "signals": {
            "decision": {
                "action": "WAIT",
                "score": 0,
                "buy_strength": 0,
                "sell_strength": 0,
                "strength_available": False,
                "engine_version": "synthetic-engine-v1",
                "config_version": "synthetic-config-v1",
                "patterns": [],
                "positive_evidence": [],
                "negative_evidence": [],
                "future_conditions": [],
                "entry_zone": None,
                "invalidation_price": None,
                "targets": [],
                "risk_reward": None,
            },
            "latest": None,
        },
        "matrix": {"resolutions": []},
        "structure": {"pivots": [], "levels": []},
        "regime": {"state": {"label": "trend", "reason": "fixture", "config_version": "r1"}},
        "columns": [],
        "transitions": [],
        "entry_readiness": entry_readiness,
    }


def test_fingerprint_unaffected_by_entry_readiness() -> None:
    before = _experience_output({"state": "NOT_READY"})
    after = _experience_output({"state": "BLOCKED"})
    assert fingerprint("scope", before) == fingerprint("scope", after)


def test_freeze_context_includes_entry_readiness(tmp_path: Path) -> None:
    journal = SQLiteJournal(tmp_path / "experiences.sqlite")
    try:
        runtime_config = RuntimeConfig(pipeline_config(), "USD/oz")
        service = ExperienceService(journal, runtime_config, "research:test")
        readiness_payload = {"state": "DEVELOPING", "signal_action": "WAIT"}
        service.observe(events()[0], _experience_output(readiness_payload))
        stored = service.repository.all()[0]
        assert stored.context()["entry_readiness"] == readiness_payload
    finally:
        journal.close()
