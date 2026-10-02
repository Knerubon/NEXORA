"""Validates the pure contract shapes frozen by ADR-033. No I/O, no runtime wiring."""

from __future__ import annotations

import pytest
from nexora.autonomous_contracts import (
    RISK_REDUCING_KINDS,
    TRADE_STATE_TRANSITIONS,
    EntryOrigin,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
    TradeState,
    TradingMode,
    is_risk_reducing,
)


def test_trading_mode_has_exactly_three_values() -> None:
    assert {mode.value for mode in TradingMode} == {"SHADOW", "ASSISTED", "AUTO"}


def test_transition_table_matches_adr_033_section_e_for_every_non_terminal_state() -> None:
    assert TRADE_STATE_TRANSITIONS[TradeState.SETUP] == frozenset(
        {TradeState.WAIT_ENTRY, TradeState.BLOCKED, TradeState.CANCELLED}
    )
    assert TRADE_STATE_TRANSITIONS[TradeState.WAIT_ENTRY] == frozenset(
        {TradeState.ENTRY_PENDING, TradeState.BLOCKED, TradeState.REJECTED, TradeState.CANCELLED}
    )
    assert TRADE_STATE_TRANSITIONS[TradeState.ENTRY_PENDING] == frozenset(
        {TradeState.OPEN, TradeState.REJECTED, TradeState.EMERGENCY}
    )
    assert TRADE_STATE_TRANSITIONS[TradeState.OPEN] == frozenset(
        {TradeState.MANAGING, TradeState.EXIT_PENDING, TradeState.EMERGENCY}
    )
    assert TRADE_STATE_TRANSITIONS[TradeState.MANAGING] == frozenset(
        {TradeState.EXIT_PENDING, TradeState.EMERGENCY}
    )
    assert TRADE_STATE_TRANSITIONS[TradeState.EXIT_PENDING] == frozenset(
        {TradeState.CLOSED, TradeState.EMERGENCY}
    )


def test_every_terminal_state_has_zero_outgoing_transitions() -> None:
    terminal = {
        TradeState.CLOSED,
        TradeState.BLOCKED,
        TradeState.REJECTED,
        TradeState.CANCELLED,
        TradeState.EMERGENCY,
    }
    non_terminal = set(TradeState) - terminal
    assert non_terminal == {
        TradeState.SETUP,
        TradeState.WAIT_ENTRY,
        TradeState.ENTRY_PENDING,
        TradeState.OPEN,
        TradeState.MANAGING,
        TradeState.EXIT_PENDING,
    }
    for state in terminal:
        assert TRADE_STATE_TRANSITIONS[state] == frozenset()


def test_every_state_is_reachable_in_the_transition_table() -> None:
    assert set(TRADE_STATE_TRANSITIONS.keys()) == set(TradeState)


@pytest.mark.parametrize(
    "kind", [TradeIntentKind.REDUCE, TradeIntentKind.CLOSE, TradeIntentKind.MODIFY_PROTECTION]
)
def test_risk_reducing_kinds_are_classified_correctly(kind: TradeIntentKind) -> None:
    assert is_risk_reducing(kind)
    assert kind in RISK_REDUCING_KINDS


def test_open_is_not_risk_reducing() -> None:
    assert not is_risk_reducing(TradeIntentKind.OPEN)
    assert TradeIntentKind.OPEN not in RISK_REDUCING_KINDS


def test_open_intent_requires_entry_origin() -> None:
    origin = EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1")
    intent = TradeIntent(
        kind=TradeIntentKind.OPEN,
        symbol="EURUSD",
        side="long",
        origin=origin,
        proposal_id="proposal:1",
    )
    assert intent.kind is TradeIntentKind.OPEN


def test_open_intent_rejects_position_origin() -> None:
    origin = PositionOrigin(position_id="pos:1", exit_decision_ref="exit:1")
    with pytest.raises(ValueError, match="open_requires_entry_origin"):
        TradeIntent(
            kind=TradeIntentKind.OPEN,
            symbol="EURUSD",
            side="long",
            origin=origin,
            proposal_id="proposal:1",
        )


@pytest.mark.parametrize(
    "kind", [TradeIntentKind.REDUCE, TradeIntentKind.CLOSE, TradeIntentKind.MODIFY_PROTECTION]
)
def test_risk_reducing_intent_requires_position_origin(kind: TradeIntentKind) -> None:
    origin = EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1")
    with pytest.raises(ValueError, match="risk_reducing_kind_requires_position_origin"):
        TradeIntent(
            kind=kind,
            symbol="EURUSD",
            side="long",
            origin=origin,
            proposal_id="proposal:1",
        )


def test_risk_reducing_intent_accepts_position_origin_not_a_research_signal() -> None:
    origin = PositionOrigin(position_id="pos:1", exit_decision_ref="exit:1")
    intent = TradeIntent(
        kind=TradeIntentKind.CLOSE,
        symbol="EURUSD",
        side="long",
        origin=origin,
        proposal_id="proposal:1",
    )
    assert isinstance(intent.origin, PositionOrigin)
    assert not hasattr(intent.origin, "signal_id")


@pytest.mark.parametrize(
    ("signal_decision_ref", "entry_readiness_ref"),
    [("", "readiness:1"), ("signal:1", ""), ("   ", "readiness:1"), ("signal:1", "   ")],
)
def test_entry_origin_rejects_blank_reference(
    signal_decision_ref: str, entry_readiness_ref: str
) -> None:
    with pytest.raises(ValueError, match="missing_entry_origin_reference"):
        EntryOrigin(
            signal_decision_ref=signal_decision_ref, entry_readiness_ref=entry_readiness_ref
        )


@pytest.mark.parametrize(
    ("position_id", "exit_decision_ref"),
    [("", "exit:1"), ("pos:1", ""), ("   ", "exit:1"), ("pos:1", "   ")],
)
def test_position_origin_rejects_blank_reference(position_id: str, exit_decision_ref: str) -> None:
    with pytest.raises(ValueError, match="missing_position_origin_reference"):
        PositionOrigin(position_id=position_id, exit_decision_ref=exit_decision_ref)


def test_trade_intent_rejects_blank_identity() -> None:
    origin = EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1")
    with pytest.raises(ValueError, match="missing_trade_intent_identity"):
        TradeIntent(
            kind=TradeIntentKind.OPEN,
            symbol="   ",
            side="long",
            origin=origin,
            proposal_id="proposal:1",
        )
