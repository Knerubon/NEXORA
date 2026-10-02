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


def test_terminal_states_have_no_outgoing_transitions() -> None:
    terminal = {
        TradeState.CLOSED,
        TradeState.BLOCKED,
        TradeState.REJECTED,
        TradeState.CANCELLED,
        TradeState.EMERGENCY,
    }
    for state in terminal:
        assert TRADE_STATE_TRANSITIONS[state] == frozenset()


def test_transition_table_matches_adr_033_section_e() -> None:
    assert TRADE_STATE_TRANSITIONS[TradeState.SETUP] == frozenset(
        {TradeState.WAIT_ENTRY, TradeState.BLOCKED, TradeState.CANCELLED}
    )
    assert TRADE_STATE_TRANSITIONS[TradeState.ENTRY_PENDING] == frozenset(
        {TradeState.OPEN, TradeState.REJECTED, TradeState.EMERGENCY}
    )
    assert TradeState.EMERGENCY in TRADE_STATE_TRANSITIONS[TradeState.OPEN]
    assert TradeState.EMERGENCY in TRADE_STATE_TRANSITIONS[TradeState.MANAGING]


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
