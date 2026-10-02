"""Fail-closed/adversarial tests for Autonomous Core Phase 1 (ADR-033).

No I/O, no broker calls, no runtime wiring is exercised here -- these tests only
validate the pure contract/authority shapes in packages/nexora/autonomous/.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nexora.autonomous.authority import (
    AuthorityDecision,
    ExistingPositionAuthority,
    NewTradeAuthority,
    TradingConfig,
    authorize_trade_intent,
)
from nexora.autonomous.broker_capabilities import BrokerCapabilities
from nexora.autonomous.health import Health, SystemHealthGate, SystemHealthSnapshot
from nexora.autonomous.risk_migration import RiskReductionProposal
from nexora.autonomous_contracts import (
    EntryOrigin,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
    TradingMode,
)
from nexora.market_data.instruments import FeedBinding, InstrumentDefinition, PriceGrid
from nexora.risk.models import RiskDecision

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _healthy_snapshot(**overrides: Health) -> SystemHealthSnapshot:
    axes = dict.fromkeys(
        ("market_data", "broker", "account", "capabilities", "reconciliation", "journal"),
        Health.HEALTHY,
    )
    axes.update(overrides)
    return SystemHealthSnapshot(observed_at=NOW, **axes)


def _allow_risk_decision() -> RiskDecision:
    return RiskDecision(
        decision_id="prop:1:v1",
        proposal_id="prop:1",
        signal_id="signal:1",
        action="allow",
        reason="allowed",
        reason_codes=("policy_pass",),
        approved_size=Decimal("1"),
        reserved_risk=Decimal("10"),
        effective_time=NOW,
        policy_version="v1",
        source_refs=("src:1",),
    )


def _reject_risk_decision() -> RiskDecision:
    return RiskDecision(
        decision_id="prop:1:v1",
        proposal_id="prop:1",
        signal_id="signal:1",
        action="reject",
        reason="exposure_limit",
        reason_codes=("max_total_exposure",),
        approved_size=Decimal("0"),
        reserved_risk=Decimal("0"),
        effective_time=NOW,
        policy_version="v1",
        source_refs=("src:1",),
    )


# --------------------------------------------------------------------------------
# SystemHealthGate
# --------------------------------------------------------------------------------


def test_default_snapshot_is_unknown_on_every_axis_fail_closed() -> None:
    snapshot = SystemHealthSnapshot(observed_at=NOW)
    assert all(value is Health.UNKNOWN for _, value in snapshot.axes())
    assert SystemHealthGate.blocks_new_trade(snapshot)


def test_fully_healthy_snapshot_does_not_block_new_trade() -> None:
    assert not SystemHealthGate.blocks_new_trade(_healthy_snapshot())


def test_naive_observed_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires_timezone"):
        SystemHealthSnapshot(observed_at=datetime(2026, 10, 2, 12, 0))


@pytest.mark.parametrize(
    "axis", ["market_data", "broker", "account", "capabilities", "reconciliation", "journal"]
)
@pytest.mark.parametrize(
    "value", [Health.UNKNOWN, Health.UNHEALTHY, Health.STALE, Health.UNSYNCHRONIZED]
)
def test_any_degraded_axis_blocks_new_trade(axis: str, value: Health) -> None:
    snapshot = _healthy_snapshot(**{axis: value})
    assert SystemHealthGate.blocks_new_trade(snapshot)
    assert axis in SystemHealthGate.degraded_axes(snapshot)


# --------------------------------------------------------------------------------
# NewTradeAuthority -- fail-closed on every degraded axis
# --------------------------------------------------------------------------------


def test_new_trade_authority_allows_when_everything_is_healthy_and_ready() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert decision == AuthorityDecision(allowed=True, reason_codes=())


@pytest.mark.parametrize(
    "value", [Health.UNKNOWN, Health.UNHEALTHY, Health.STALE, Health.UNSYNCHRONIZED]
)
def test_new_trade_authority_denies_on_degraded_market_data(value: Health) -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(market_data=value),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert "health_degraded:market_data" in decision.reason_codes


def test_new_trade_authority_denies_on_unsynchronized_reconciliation() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(reconciliation=Health.UNSYNCHRONIZED),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert "health_degraded:reconciliation" in decision.reason_codes


def test_new_trade_authority_denies_disconnected_broker() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(broker=Health.UNHEALTHY),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert "health_degraded:broker" in decision.reason_codes


@pytest.mark.parametrize("state", ["DEVELOPING", "NOT_READY", "BLOCKED"])
def test_new_trade_authority_denies_when_entry_not_ready(state: str) -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(),
        entry_readiness=state,  # type: ignore[arg-type]
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert "entry_not_ready" in decision.reason_codes


def test_new_trade_authority_denies_when_risk_rejects() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(),
        entry_readiness="READY",
        risk_decision=_reject_risk_decision(),
    )
    assert not decision.allowed
    assert "risk_decision_not_allow" in decision.reason_codes


def test_new_trade_authority_always_denies_auto_mode_phase1() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.AUTO, assisted_confirmation=True),
        health=_healthy_snapshot(),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert "auto_mode_not_governed" in decision.reason_codes


def test_new_trade_authority_denies_shadow_mode() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.SHADOW),
        health=_healthy_snapshot(),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert "trading_mode_shadow_observes_only" in decision.reason_codes


def test_new_trade_authority_denies_assisted_without_confirmation() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=False),
        health=_healthy_snapshot(),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert "assisted_confirmation_missing" in decision.reason_codes


def test_authority_decision_cannot_be_allowed_with_reason_codes() -> None:
    with pytest.raises(ValueError, match="allowed_decision_must_have_no_reason_codes"):
        AuthorityDecision(allowed=True, reason_codes=("x",))


def test_authority_decision_cannot_be_denied_without_reason_codes() -> None:
    with pytest.raises(ValueError, match="denied_decision_requires_reason_codes"):
        AuthorityDecision(allowed=False, reason_codes=())


# --------------------------------------------------------------------------------
# ExistingPositionAuthority -- degradation must never justify increasing risk
# --------------------------------------------------------------------------------


def test_existing_position_authority_rejects_open_kind() -> None:
    with pytest.raises(ValueError, match="existing_position_authority_does_not_evaluate_open"):
        ExistingPositionAuthority.evaluate(
            health=_healthy_snapshot(), intent_kind=TradeIntentKind.OPEN
        )


def test_existing_position_authority_allows_everything_when_healthy() -> None:
    for kind in (TradeIntentKind.REDUCE, TradeIntentKind.CLOSE, TradeIntentKind.MODIFY_PROTECTION):
        decision = ExistingPositionAuthority.evaluate(
            health=_healthy_snapshot(), intent_kind=kind, protection_change="TIGHTEN"
        )
        assert decision.allowed


def test_existing_position_authority_denies_close_when_broker_unhealthy() -> None:
    decision = ExistingPositionAuthority.evaluate(
        health=_healthy_snapshot(broker=Health.UNHEALTHY), intent_kind=TradeIntentKind.CLOSE
    )
    assert not decision.allowed
    assert "broker_unhealthy_fail_closed" in decision.reason_codes


def test_existing_position_authority_denies_reduce_when_broker_unhealthy() -> None:
    decision = ExistingPositionAuthority.evaluate(
        health=_healthy_snapshot(broker=Health.UNHEALTHY), intent_kind=TradeIntentKind.REDUCE
    )
    assert not decision.allowed


def test_existing_position_authority_allows_reduce_and_close_under_non_broker_degradation() -> None:
    degraded = _healthy_snapshot(market_data=Health.STALE)
    for kind in (TradeIntentKind.REDUCE, TradeIntentKind.CLOSE):
        decision = ExistingPositionAuthority.evaluate(health=degraded, intent_kind=kind)
        assert decision.allowed


def test_existing_position_authority_denies_unclassified_modify_protection() -> None:
    decision = ExistingPositionAuthority.evaluate(
        health=_healthy_snapshot(market_data=Health.UNKNOWN),
        intent_kind=TradeIntentKind.MODIFY_PROTECTION,
    )
    assert not decision.allowed
    assert "protection_change_unclassified" in decision.reason_codes


def test_existing_position_authority_denies_widen_under_degradation_never_increase_risk() -> None:
    decision = ExistingPositionAuthority.evaluate(
        health=_healthy_snapshot(account=Health.UNSYNCHRONIZED),
        intent_kind=TradeIntentKind.MODIFY_PROTECTION,
        protection_change="WIDEN",
    )
    assert not decision.allowed
    assert "degraded_blocks_exposure_increase" in decision.reason_codes


def test_existing_position_authority_allows_tighten_under_non_broker_degradation() -> None:
    decision = ExistingPositionAuthority.evaluate(
        health=_healthy_snapshot(capabilities=Health.STALE),
        intent_kind=TradeIntentKind.MODIFY_PROTECTION,
        protection_change="TIGHTEN",
    )
    assert decision.allowed


# --------------------------------------------------------------------------------
# authorize_trade_intent dispatch + invalid-origin / AI-cannot-override guarantees
# --------------------------------------------------------------------------------


def test_authorize_trade_intent_dispatches_open_to_new_trade_authority() -> None:
    intent = TradeIntent(
        kind=TradeIntentKind.OPEN,
        symbol="EURUSD",
        side="long",
        origin=EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1"),
        proposal_id="proposal:1",
    )
    decision = authorize_trade_intent(
        intent,
        health=_healthy_snapshot(),
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert decision.allowed


def test_authorize_trade_intent_open_requires_config_entry_readiness_and_risk() -> None:
    intent = TradeIntent(
        kind=TradeIntentKind.OPEN,
        symbol="EURUSD",
        side="long",
        origin=EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1"),
        proposal_id="proposal:1",
    )
    with pytest.raises(
        ValueError, match="open_intent_requires_config_entry_readiness_and_risk_decision"
    ):
        authorize_trade_intent(intent, health=_healthy_snapshot())


def test_authorize_trade_intent_dispatches_close_to_existing_position_authority() -> None:
    intent = TradeIntent(
        kind=TradeIntentKind.CLOSE,
        symbol="EURUSD",
        side="long",
        origin=PositionOrigin(position_id="pos:1", exit_decision_ref="exit:1"),
        proposal_id="proposal:1",
    )
    decision = authorize_trade_intent(intent, health=_healthy_snapshot())
    assert decision.allowed


def test_authorize_trade_intent_signature_has_no_ai_analysis_parameter() -> None:
    """ADR-033 section 19: AIAnalysis must have no slot in the authority chain."""

    import inspect

    params = inspect.signature(authorize_trade_intent).parameters
    assert "ai_analysis" not in params
    assert not any("ai" in name.lower() for name in params)

    for fn in (NewTradeAuthority.evaluate, ExistingPositionAuthority.evaluate):
        fn_params = inspect.signature(fn).parameters
        assert not any("ai" in name.lower() for name in fn_params)


def test_trade_intent_open_rejects_position_origin_invalid_origin() -> None:
    with pytest.raises(ValueError, match="open_requires_entry_origin"):
        TradeIntent(
            kind=TradeIntentKind.OPEN,
            symbol="EURUSD",
            side="long",
            origin=PositionOrigin(position_id="pos:1", exit_decision_ref="exit:1"),
            proposal_id="proposal:1",
        )


def test_trade_intent_close_rejects_entry_origin_invalid_origin() -> None:
    with pytest.raises(ValueError, match="risk_reducing_kind_requires_position_origin"):
        TradeIntent(
            kind=TradeIntentKind.CLOSE,
            symbol="EURUSD",
            side="long",
            origin=EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1"),
            proposal_id="proposal:1",
        )


# --------------------------------------------------------------------------------
# BrokerCapabilities -- broker-agnostic, fail-closed shape
# --------------------------------------------------------------------------------


def _feed_binding() -> FeedBinding:
    return FeedBinding(
        instrument_id="inst-1",
        broker_id="broker-1",
        symbol="EXAMPLE",
        price_grid=PriceGrid(digits=2, point=Decimal("0.01"), trade_tick_size=Decimal("0.01")),
        time_offset_seconds=0,
    )


def _instrument() -> InstrumentDefinition:
    return InstrumentDefinition(
        instrument_id="inst-1",
        currency_base="USD",
        currency_profit="USD",
        trade_calc_mode=0,
        trade_contract_size=Decimal("100000"),
        chart_mode=0,
    )


def _capabilities(**overrides: object) -> BrokerCapabilities:
    fields: dict[str, object] = dict(
        binding=_feed_binding(),
        instrument=_instrument(),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
        stops_level=Decimal("10"),
        freeze_level=Decimal("5"),
        filling_modes=("FOK",),
        execution_mode="market",
        session_policy_ref="policy:session:1",
        spread_policy_ref="policy:spread:1",
        margin_policy_ref="policy:margin:1",
        observed_at=NOW,
    )
    fields.update(overrides)
    return BrokerCapabilities(**fields)  # type: ignore[arg-type]


def test_broker_capabilities_contract_size_reads_from_instrument_not_duplicated() -> None:
    capabilities = _capabilities()
    assert capabilities.contract_size == Decimal("100000")


def test_broker_capabilities_rejects_instrument_binding_mismatch() -> None:
    mismatched_instrument = InstrumentDefinition(
        instrument_id="other-instrument",
        currency_base="USD",
        currency_profit="USD",
        trade_calc_mode=0,
        trade_contract_size=Decimal("100000"),
        chart_mode=0,
    )
    with pytest.raises(ValueError, match="broker_capabilities_instrument_binding_mismatch"):
        _capabilities(instrument=mismatched_instrument)


def test_broker_capabilities_rejects_naive_observed_at() -> None:
    with pytest.raises(ValueError, match="requires_timezone"):
        _capabilities(observed_at=datetime(2026, 10, 2, 12, 0))


def test_broker_capabilities_rejects_empty_filling_modes_no_hardcoded_default() -> None:
    with pytest.raises(ValueError, match="missing_filling_modes"):
        _capabilities(filling_modes=())


def test_broker_capabilities_rejects_volume_max_below_min() -> None:
    with pytest.raises(ValueError, match="volume_max_below_volume_min"):
        _capabilities(volume_min=Decimal("10"), volume_max=Decimal("1"))


# --------------------------------------------------------------------------------
# RiskReductionProposal -- migration boundary, never a synthetic ResearchSignal
# --------------------------------------------------------------------------------


def _position_intent(kind: TradeIntentKind = TradeIntentKind.CLOSE) -> TradeIntent:
    return TradeIntent(
        kind=kind,
        symbol="EURUSD",
        side="long",
        origin=PositionOrigin(position_id="pos:1", exit_decision_ref="exit:1"),
        proposal_id="proposal:1",
    )


def test_risk_reduction_proposal_accepts_valid_position_scoped_intent() -> None:
    proposal = RiskReductionProposal(
        proposal_id="rr:1",
        position_id="pos:1",
        trade_intent=_position_intent(),
        current_exposure=Decimal("500"),
    )
    assert proposal.current_exposure == Decimal("500")


def test_risk_reduction_proposal_rejects_open_kind_not_risk_reducing() -> None:
    open_intent = TradeIntent(
        kind=TradeIntentKind.OPEN,
        symbol="EURUSD",
        side="long",
        origin=EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1"),
        proposal_id="proposal:1",
    )
    with pytest.raises(ValueError, match="risk_reduction_requires_risk_reducing_kind"):
        RiskReductionProposal(
            proposal_id="rr:1",
            position_id="pos:1",
            trade_intent=open_intent,
            current_exposure=Decimal("0"),
        )


def test_risk_reduction_proposal_rejects_mismatched_position_id() -> None:
    with pytest.raises(ValueError, match="risk_reduction_position_id_mismatch"):
        RiskReductionProposal(
            proposal_id="rr:1",
            position_id="pos:other",
            trade_intent=_position_intent(),
            current_exposure=Decimal("0"),
        )


def test_risk_reduction_proposal_rejects_negative_exposure() -> None:
    with pytest.raises(ValueError, match="invalid_current_exposure"):
        RiskReductionProposal(
            proposal_id="rr:1",
            position_id="pos:1",
            trade_intent=_position_intent(),
            current_exposure=Decimal("-1"),
        )
