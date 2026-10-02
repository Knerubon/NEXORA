"""Deterministic tests for Position Supervisor Phase 1 (ADR-033 section 15).

No I/O, no RiskEngine/PaperSimulator wiring, no broker execution — these
tests exercise only the pure domain models and lifecycle mechanics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nexora.autonomous_contracts import TradeIntentKind, TradeState
from nexora.position.models import (
    ExitDecision,
    PositionInputError,
    PositionRecord,
    ProtectionLevels,
    open_position_from_signal,
)
from nexora.position.supervisor import apply_exit_decision, build_trade_intent, mark_closed
from nexora.signals.models import SignalDecision, SignalEvidence, SignalTarget

SYMBOL = "XAUUSD"
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _signal_decision(
    *, invalidation_price: Decimal | None, targets: tuple[SignalTarget, ...]
) -> SignalDecision:
    return SignalDecision(
        action="BUY",
        score=80,
        entry_zone=None,
        invalidation_price=invalidation_price,
        invalidation_reason="structure_break" if invalidation_price else None,
        targets=targets,
        risk_reward=None,
        patterns=(),
        positive_evidence=(),
        negative_evidence=(),
        future_conditions=(),
        config_version="test-v1",
        engine_version="test-v1:signals",
        source_refs=("signal:evidence:1",),
    )


def _evidence(component: str = "structure") -> tuple[SignalEvidence, ...]:
    return (
        SignalEvidence(
            component=component,
            code="test_evidence",
            points=0,
            polarity="neutral",
            reason="test fixture evidence",
            source_refs=("evidence:1",),
        ),
    )


def _position(
    *,
    state: TradeState = TradeState.OPEN,
    quantity: Decimal = Decimal("1"),
    side: str = "long",
    stop_price: Decimal = Decimal("1900"),
) -> PositionRecord:
    return PositionRecord(
        position_id="pos:1",
        symbol=SYMBOL,
        side=side,
        state=state,
        quantity=quantity,
        entry_price=Decimal("1950"),
        protection=ProtectionLevels(stop_price=stop_price, target_prices=(Decimal("2000"),)),
        opened_at=BASE,
        source_signal_decision_ref="signal:1",
        source_entry_readiness_ref="readiness:1",
    )


def _exit_decision(
    *,
    action: str,
    reduce_quantity: Decimal | None = None,
    new_stop_price: Decimal | None = None,
    position_id: str = "pos:1",
    decision_id: str = "exit:1",
) -> ExitDecision:
    return ExitDecision(
        decision_id=decision_id,
        position_id=position_id,
        action=action,
        evidence=_evidence(),
        source_refs=("evidence:1",),
        decided_at=BASE,
        reduce_quantity=reduce_quantity,
        new_stop_price=new_stop_price,
    )


# --- open_position_from_signal: initial SL/TP consumption ------------------


def test_open_position_consumes_invalidation_and_targets_from_signal_decision() -> None:
    decision = _signal_decision(
        invalidation_price=Decimal("1900"),
        targets=(SignalTarget(name="TP1", price=Decimal("2000"), method="measured_move"),),
    )
    position = open_position_from_signal(
        position_id="pos:1",
        signal_decision=decision,
        signal_decision_ref="signal:1",
        entry_readiness_ref="readiness:1",
        side="long",
        symbol=SYMBOL,
        quantity=Decimal("1"),
        entry_price=Decimal("1950"),
        opened_at=BASE,
    )
    assert position.protection.stop_price == Decimal("1900")
    assert position.protection.target_prices == (Decimal("2000"),)
    assert position.state is TradeState.OPEN
    assert position.quantity == Decimal("1")


def test_open_position_rejects_missing_invalidation_price() -> None:
    decision = _signal_decision(
        invalidation_price=None,
        targets=(SignalTarget(name="TP1", price=Decimal("2000"), method="measured_move"),),
    )
    with pytest.raises(PositionInputError, match="signal_decision_missing_invalidation_price"):
        open_position_from_signal(
            position_id="pos:1",
            signal_decision=decision,
            signal_decision_ref="signal:1",
            entry_readiness_ref="readiness:1",
            side="long",
            symbol=SYMBOL,
            quantity=Decimal("1"),
            entry_price=Decimal("1950"),
            opened_at=BASE,
        )


def test_open_position_rejects_missing_targets() -> None:
    decision = _signal_decision(invalidation_price=Decimal("1900"), targets=())
    with pytest.raises(PositionInputError, match="signal_decision_missing_targets"):
        open_position_from_signal(
            position_id="pos:1",
            signal_decision=decision,
            signal_decision_ref="signal:1",
            entry_readiness_ref="readiness:1",
            side="long",
            symbol=SYMBOL,
            quantity=Decimal("1"),
            entry_price=Decimal("1950"),
            opened_at=BASE,
        )


def test_open_position_never_invents_stop_or_target_when_absent() -> None:
    """Guards against ever silently defaulting SL/TP (AGENTS.md section 9)."""
    decision = _signal_decision(invalidation_price=None, targets=())
    with pytest.raises(PositionInputError):
        open_position_from_signal(
            position_id="pos:1",
            signal_decision=decision,
            signal_decision_ref="signal:1",
            entry_readiness_ref="readiness:1",
            side="long",
            symbol=SYMBOL,
            quantity=Decimal("1"),
            entry_price=Decimal("1950"),
            opened_at=BASE,
        )


# --- ExitDecision contract validation ---------------------------------------


def test_partial_close_requires_positive_reduce_quantity() -> None:
    with pytest.raises(
        PositionInputError, match="partial_close_requires_positive_reduce_quantity"
    ):
        _exit_decision(action="PARTIAL_CLOSE", reduce_quantity=None)


def test_partial_close_rejects_new_stop_price() -> None:
    with pytest.raises(PositionInputError, match="new_stop_price_only_valid_for_tighten"):
        _exit_decision(
            action="PARTIAL_CLOSE", reduce_quantity=Decimal("0.5"), new_stop_price=Decimal("1")
        )


def test_tighten_requires_new_stop_price() -> None:
    with pytest.raises(PositionInputError, match="tighten_requires_new_stop_price"):
        _exit_decision(action="TIGHTEN", new_stop_price=None)


def test_tighten_rejects_reduce_quantity() -> None:
    with pytest.raises(PositionInputError, match="reduce_quantity_only_valid_for_partial_close"):
        _exit_decision(
            action="TIGHTEN", new_stop_price=Decimal("1910"), reduce_quantity=Decimal("0.5")
        )


def test_close_rejects_reduce_quantity_and_new_stop_price() -> None:
    with pytest.raises(PositionInputError, match="reduce_quantity_only_valid_for_partial_close"):
        _exit_decision(action="CLOSE", reduce_quantity=Decimal("0.5"))
    with pytest.raises(PositionInputError, match="new_stop_price_only_valid_for_tighten"):
        _exit_decision(action="CLOSE", new_stop_price=Decimal("1910"))


def test_hold_rejects_reduce_quantity_and_new_stop_price() -> None:
    with pytest.raises(PositionInputError, match="reduce_quantity_only_valid_for_partial_close"):
        _exit_decision(action="HOLD", reduce_quantity=Decimal("0.5"))
    with pytest.raises(PositionInputError, match="new_stop_price_only_valid_for_tighten"):
        _exit_decision(action="HOLD", new_stop_price=Decimal("1910"))


def test_exit_decision_requires_evidence() -> None:
    with pytest.raises(PositionInputError, match="missing_exit_evidence"):
        ExitDecision(
            decision_id="exit:1",
            position_id="pos:1",
            action="HOLD",
            evidence=(),
            source_refs=(),
            decided_at=BASE,
        )


def test_exit_decision_is_never_a_research_signal_shaped_object() -> None:
    decision = _exit_decision(action="HOLD")
    assert not hasattr(decision, "signal_id")
    assert not hasattr(decision, "side")
    assert not hasattr(decision, "decision_time")


# --- apply_exit_decision: lifecycle mechanics -------------------------------


def test_hold_returns_position_unchanged() -> None:
    position = _position()
    decision = _exit_decision(action="HOLD")
    result = apply_exit_decision(position, decision)
    assert result == position


def test_partial_close_with_residual_quantity_advances_open_to_managing() -> None:
    position = _position(state=TradeState.OPEN, quantity=Decimal("1"))
    decision = _exit_decision(action="PARTIAL_CLOSE", reduce_quantity=Decimal("0.4"))
    result = apply_exit_decision(position, decision)
    assert result.state is TradeState.MANAGING
    assert result.quantity == Decimal("0.6")


def test_partial_close_with_residual_quantity_stays_in_managing() -> None:
    position = _position(state=TradeState.MANAGING, quantity=Decimal("0.6"))
    decision = _exit_decision(action="PARTIAL_CLOSE", reduce_quantity=Decimal("0.1"))
    result = apply_exit_decision(position, decision)
    assert result.state is TradeState.MANAGING
    assert result.quantity == Decimal("0.5")


def test_partial_close_that_would_leave_zero_residual_is_rejected() -> None:
    """Frozen rule: a full exit must use CLOSE, not PARTIAL_CLOSE."""
    position = _position(state=TradeState.OPEN, quantity=Decimal("1"))
    decision = _exit_decision(action="PARTIAL_CLOSE", reduce_quantity=Decimal("1"))
    with pytest.raises(PositionInputError, match="partial_close_must_leave_residual_quantity"):
        apply_exit_decision(position, decision)


def test_partial_close_cannot_reduce_below_zero() -> None:
    position = _position(state=TradeState.OPEN, quantity=Decimal("1"))
    decision = _exit_decision(action="PARTIAL_CLOSE", reduce_quantity=Decimal("1.5"))
    with pytest.raises(PositionInputError, match="partial_close_must_leave_residual_quantity"):
        apply_exit_decision(position, decision)


def test_partial_close_rejected_once_already_exit_pending() -> None:
    position = _position(state=TradeState.EXIT_PENDING, quantity=Decimal("1"))
    decision = _exit_decision(action="PARTIAL_CLOSE", reduce_quantity=Decimal("0.1"))
    with pytest.raises(PositionInputError, match="illegal_position_state_transition"):
        apply_exit_decision(position, decision)


def test_close_from_open_advances_to_exit_pending_with_zero_quantity() -> None:
    position = _position(state=TradeState.OPEN, quantity=Decimal("1"))
    decision = _exit_decision(action="CLOSE")
    result = apply_exit_decision(position, decision)
    assert result.state is TradeState.EXIT_PENDING
    assert result.quantity == Decimal("0")


def test_close_from_managing_advances_to_exit_pending() -> None:
    position = _position(state=TradeState.MANAGING, quantity=Decimal("0.5"))
    decision = _exit_decision(action="CLOSE")
    result = apply_exit_decision(position, decision)
    assert result.state is TradeState.EXIT_PENDING
    assert result.quantity == Decimal("0")


def test_close_is_idempotent_when_already_exit_pending() -> None:
    position = _position(state=TradeState.EXIT_PENDING, quantity=Decimal("0"))
    decision = _exit_decision(action="CLOSE", decision_id="exit:2")
    result = apply_exit_decision(position, decision)
    assert result.state is TradeState.EXIT_PENDING
    assert result.quantity == Decimal("0")


def test_close_rejected_from_emergency_terminal_state() -> None:
    position = _position(state=TradeState.EMERGENCY, quantity=Decimal("1"))
    decision = _exit_decision(action="CLOSE")
    with pytest.raises(PositionInputError, match="illegal_position_state_transition"):
        apply_exit_decision(position, decision)


def test_tighten_updates_stop_price_only_for_long_when_stricter() -> None:
    position = _position(side="long", stop_price=Decimal("1900"))
    decision = _exit_decision(action="TIGHTEN", new_stop_price=Decimal("1930"))
    result = apply_exit_decision(position, decision)
    assert result.protection.stop_price == Decimal("1930")
    assert result.protection.target_prices == position.protection.target_prices
    assert result.quantity == position.quantity
    assert result.state == position.state


def test_tighten_rejects_widening_stop_for_long() -> None:
    position = _position(side="long", stop_price=Decimal("1900"))
    decision = _exit_decision(action="TIGHTEN", new_stop_price=Decimal("1880"))
    with pytest.raises(PositionInputError, match="protection_must_only_tighten"):
        apply_exit_decision(position, decision)


def test_tighten_rejects_widening_stop_for_short() -> None:
    position = _position(side="short", stop_price=Decimal("1900"))
    decision = _exit_decision(action="TIGHTEN", new_stop_price=Decimal("1920"))
    with pytest.raises(PositionInputError, match="protection_must_only_tighten"):
        apply_exit_decision(position, decision)


def test_tighten_accepts_stricter_stop_for_short() -> None:
    position = _position(side="short", stop_price=Decimal("1900"))
    decision = _exit_decision(action="TIGHTEN", new_stop_price=Decimal("1880"))
    result = apply_exit_decision(position, decision)
    assert result.protection.stop_price == Decimal("1880")


def test_tighten_rejected_once_exit_pending() -> None:
    position = _position(state=TradeState.EXIT_PENDING, quantity=Decimal("0"))
    decision = _exit_decision(action="TIGHTEN", new_stop_price=Decimal("1930"))
    with pytest.raises(PositionInputError, match="tighten_requires_open_or_managing_position"):
        apply_exit_decision(position, decision)


def test_apply_exit_decision_rejects_position_id_mismatch() -> None:
    position = _position()
    decision = _exit_decision(action="HOLD", position_id="pos:other")
    with pytest.raises(PositionInputError, match="exit_decision_position_mismatch"):
        apply_exit_decision(position, decision)


# --- mark_closed -------------------------------------------------------------


def test_mark_closed_from_exit_pending_with_zero_quantity() -> None:
    position = _position(state=TradeState.EXIT_PENDING, quantity=Decimal("0"))
    result = mark_closed(position)
    assert result.state is TradeState.CLOSED


def test_mark_closed_rejects_nonzero_quantity() -> None:
    position = _position(state=TradeState.EXIT_PENDING, quantity=Decimal("0.1"))
    with pytest.raises(PositionInputError, match="cannot_close_nonzero_quantity"):
        mark_closed(position)


def test_mark_closed_rejects_wrong_state_even_with_zero_quantity() -> None:
    position = _position(state=TradeState.MANAGING, quantity=Decimal("0"))
    with pytest.raises(PositionInputError, match="illegal_position_state_transition"):
        mark_closed(position)


# --- build_trade_intent -------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "kwargs", "expected_kind"),
    [
        ("PARTIAL_CLOSE", {"reduce_quantity": Decimal("0.1")}, TradeIntentKind.REDUCE),
        ("CLOSE", {}, TradeIntentKind.CLOSE),
        ("TIGHTEN", {"new_stop_price": Decimal("1930")}, TradeIntentKind.MODIFY_PROTECTION),
    ],
)
def test_build_trade_intent_maps_action_to_expected_kind(
    action: str, kwargs: dict[str, Decimal], expected_kind: TradeIntentKind
) -> None:
    position = _position()
    decision = _exit_decision(action=action, **kwargs)
    intent = build_trade_intent(position, decision, proposal_id="proposal:1")
    assert intent.kind is expected_kind
    assert intent.symbol == position.symbol
    assert intent.side == position.side
    assert intent.origin.position_id == position.position_id
    assert intent.origin.exit_decision_ref == decision.decision_id
    assert not hasattr(intent.origin, "signal_id")


def test_build_trade_intent_rejects_hold() -> None:
    position = _position()
    decision = _exit_decision(action="HOLD")
    with pytest.raises(PositionInputError, match="hold_has_no_trade_intent"):
        build_trade_intent(position, decision, proposal_id="proposal:1")


def test_build_trade_intent_rejects_position_id_mismatch() -> None:
    position = _position()
    decision = _exit_decision(action="CLOSE", position_id="pos:other")
    with pytest.raises(PositionInputError, match="exit_decision_position_mismatch"):
        build_trade_intent(position, decision, proposal_id="proposal:1")


# --- determinism -------------------------------------------------------------


def test_apply_exit_decision_is_deterministic_across_repeated_runs() -> None:
    position = _position(state=TradeState.OPEN, quantity=Decimal("1"))
    decision = _exit_decision(action="PARTIAL_CLOSE", reduce_quantity=Decimal("0.3"))
    first = apply_exit_decision(position, decision)
    second = apply_exit_decision(position, decision)
    assert first == second


def test_full_lifecycle_partial_then_close_then_mark_closed_is_deterministic() -> None:
    position = _position(state=TradeState.OPEN, quantity=Decimal("1"))

    partial = _exit_decision(action="PARTIAL_CLOSE", reduce_quantity=Decimal("0.4"))
    managing = apply_exit_decision(position, partial)
    assert managing.state is TradeState.MANAGING
    assert managing.quantity == Decimal("0.6")

    close = _exit_decision(action="CLOSE", decision_id="exit:2")
    exiting = apply_exit_decision(managing, close)
    assert exiting.state is TradeState.EXIT_PENDING
    assert exiting.quantity == Decimal("0")

    closed = mark_closed(exiting)
    assert closed.state is TradeState.CLOSED

    # Re-running the same sequence from the same starting position reproduces
    # identical intermediate and final states (replay determinism).
    managing_again = apply_exit_decision(position, partial)
    exiting_again = apply_exit_decision(managing_again, close)
    closed_again = mark_closed(exiting_again)
    assert (managing_again, exiting_again, closed_again) == (managing, exiting, closed)
