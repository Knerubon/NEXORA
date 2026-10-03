"""Tests for the Execution Contract Freeze V1 (ADR-034). No I/O, no broker
calls, no runtime wiring -- these tests only validate the pure contract shapes
in packages/nexora/execution/, the ManualOrigin extension in
packages/nexora/autonomous_contracts.py, the AuthorityDecision structural
split in packages/nexora/autonomous/authority.py, and RiskReductionDecision in
packages/nexora/autonomous/risk_migration.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nexora.autonomous.authority import (
    AuthorityPolicyStatus,
    ExecutionTransmissibility,
    ExistingPositionAuthority,
    NewTradeAuthority,
    TradingConfig,
    authorize_trade_intent,
)
from nexora.autonomous.health import Health, SystemHealthSnapshot
from nexora.autonomous.risk_migration import RiskReductionDecision
from nexora.autonomous_contracts import (
    EntryOrigin,
    ManualOrigin,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
    TradeState,
    TradingMode,
)
from nexora.execution import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    PriceConstraint,
    ProtectionRequest,
    ReconciliationFinding,
    ReconciliationRecord,
    ReconciliationStatus,
    build_execution_request,
    build_manual_close_all_intents,
    execution_request_idempotency_key,
    is_safe_to_retry_without_reconciliation,
    owned_open_positions,
    reconciliation_blocks_new_trade,
)
from nexora.execution.models import ExecutionContractError
from nexora.position.models import PositionRecord, ProtectionLevels
from nexora.risk.models import RiskDecision

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


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
# 1. ManualOrigin -- identity, no fake signal identity, OPEN vs reducing-kind rules
# --------------------------------------------------------------------------------


def test_manual_origin_requires_operator_ref_and_manual_request_id() -> None:
    with pytest.raises(ValueError, match="missing_manual_origin_identity"):
        ManualOrigin(operator_ref="", manual_request_id="req:1", requested_at=NOW)
    with pytest.raises(ValueError, match="missing_manual_origin_identity"):
        ManualOrigin(operator_ref="operator:1", manual_request_id="   ", requested_at=NOW)


def test_manual_origin_requires_timezone_aware_requested_at() -> None:
    with pytest.raises(ValueError, match="requires_timezone"):
        ManualOrigin(
            operator_ref="operator:1",
            manual_request_id="req:1",
            requested_at=datetime(2026, 10, 3, 12, 0),
        )


def test_manual_origin_has_no_signal_identity_fields() -> None:
    """Hard requirement: a manual order must never fake signal identity."""

    origin = ManualOrigin(operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW)
    for forbidden in (
        "signal_decision_ref",
        "entry_readiness_ref",
        "exit_decision_ref",
        "signal_id",
    ):
        assert not hasattr(origin, forbidden)


def test_manual_open_intent_accepts_manual_origin_without_position_id() -> None:
    origin = ManualOrigin(operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW)
    intent = TradeIntent(
        kind=TradeIntentKind.OPEN,
        symbol="EURUSD",
        side="long",
        origin=origin,
        proposal_id="req:1",
    )
    assert isinstance(intent.origin, ManualOrigin)


def test_manual_open_intent_rejects_manual_origin_with_position_id() -> None:
    origin = ManualOrigin(
        operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW, position_id="pos:1"
    )
    with pytest.raises(ValueError, match="manual_open_origin_must_not_reference_position"):
        TradeIntent(
            kind=TradeIntentKind.OPEN,
            symbol="EURUSD",
            side="long",
            origin=origin,
            proposal_id="req:1",
        )


@pytest.mark.parametrize(
    "kind", [TradeIntentKind.REDUCE, TradeIntentKind.CLOSE, TradeIntentKind.MODIFY_PROTECTION]
)
def test_manual_risk_reducing_intent_requires_position_id(kind: TradeIntentKind) -> None:
    origin = ManualOrigin(operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW)
    with pytest.raises(ValueError, match="manual_risk_reducing_origin_requires_position_id"):
        TradeIntent(kind=kind, symbol="EURUSD", side="long", origin=origin, proposal_id="req:1")


@pytest.mark.parametrize(
    "kind", [TradeIntentKind.REDUCE, TradeIntentKind.CLOSE, TradeIntentKind.MODIFY_PROTECTION]
)
def test_manual_risk_reducing_intent_accepts_manual_origin_with_position_id(
    kind: TradeIntentKind,
) -> None:
    origin = ManualOrigin(
        operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW, position_id="pos:1"
    )
    intent = TradeIntent(
        kind=kind, symbol="EURUSD", side="long", origin=origin, proposal_id="req:1"
    )
    assert isinstance(intent.origin, ManualOrigin)


def test_entry_origin_and_position_origin_contracts_unchanged() -> None:
    """EntryOrigin/PositionOrigin keep their exact existing shape/behavior."""

    open_intent = TradeIntent(
        kind=TradeIntentKind.OPEN,
        symbol="EURUSD",
        side="long",
        origin=EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1"),
        proposal_id="proposal:1",
    )
    assert isinstance(open_intent.origin, EntryOrigin)
    close_intent = TradeIntent(
        kind=TradeIntentKind.CLOSE,
        symbol="EURUSD",
        side="long",
        origin=PositionOrigin(position_id="pos:1", exit_decision_ref="exit:1"),
        proposal_id="proposal:2",
    )
    assert isinstance(close_intent.origin, PositionOrigin)


# --------------------------------------------------------------------------------
# 3. AuthorityDecision structural split -- policy vs transmissibility
# --------------------------------------------------------------------------------


def test_allowed_decision_is_authorized_and_transmittable() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert decision.allowed
    assert decision.policy_status is AuthorityPolicyStatus.AUTHORIZED
    assert decision.transmissibility is ExecutionTransmissibility.TRANSMITTABLE


def test_policy_denial_is_denied_and_not_applicable() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(),
        entry_readiness="NOT_READY",
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert decision.policy_status is AuthorityPolicyStatus.DENIED
    assert decision.transmissibility is ExecutionTransmissibility.NOT_APPLICABLE


def test_broker_unhealthy_close_is_authorized_but_not_transmittable() -> None:
    """ADR-034 section 3: authorized in principle, blocked only by broker
    connectivity -- a structurally distinct outcome from a policy veto, and
    consumers must read the typed fields, not parse reason_codes.
    """

    decision = ExistingPositionAuthority.evaluate(
        health=_healthy_snapshot(broker=Health.UNHEALTHY), intent_kind=TradeIntentKind.CLOSE
    )
    assert not decision.allowed
    assert decision.policy_status is AuthorityPolicyStatus.AUTHORIZED
    assert decision.transmissibility is ExecutionTransmissibility.NOT_TRANSMITTABLE


def test_modify_protection_unclassified_is_policy_denied_not_transmission_blocked() -> None:
    decision = ExistingPositionAuthority.evaluate(
        health=_healthy_snapshot(market_data=Health.UNKNOWN),
        intent_kind=TradeIntentKind.MODIFY_PROTECTION,
    )
    assert decision.policy_status is AuthorityPolicyStatus.DENIED
    assert decision.transmissibility is ExecutionTransmissibility.NOT_APPLICABLE


def test_new_trade_authority_degraded_health_is_policy_denied_never_transmission_only() -> None:
    """A degraded SystemHealthGate state always blocks OPEN as a *policy*
    matter (ADR-033 section 11) -- it is never reclassified as merely a
    transmission problem for new exposure.
    """

    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        health=_healthy_snapshot(broker=Health.UNHEALTHY),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert decision.policy_status is AuthorityPolicyStatus.DENIED
    assert decision.transmissibility is ExecutionTransmissibility.NOT_APPLICABLE


# --------------------------------------------------------------------------------
# authorize_trade_intent -- manual OPEN routing (no EntryReadiness input)
# --------------------------------------------------------------------------------


def test_authorize_trade_intent_routes_manual_open_without_entry_readiness() -> None:
    origin = ManualOrigin(operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW)
    intent = TradeIntent(
        kind=TradeIntentKind.OPEN, symbol="EURUSD", side="long", origin=origin, proposal_id="req:1"
    )
    decision = authorize_trade_intent(
        intent,
        health=_healthy_snapshot(),
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        risk_decision=_allow_risk_decision(),
    )
    assert decision.allowed


def test_authorize_trade_intent_manual_open_still_denies_on_rejected_risk() -> None:
    origin = ManualOrigin(operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW)
    intent = TradeIntent(
        kind=TradeIntentKind.OPEN, symbol="EURUSD", side="long", origin=origin, proposal_id="req:1"
    )
    decision = authorize_trade_intent(
        intent,
        health=_healthy_snapshot(),
        config=TradingConfig(mode=TradingMode.ASSISTED, assisted_confirmation=True),
        risk_decision=_reject_risk_decision(),
    )
    assert not decision.allowed
    assert "risk_decision_not_allow" in decision.reason_codes


def test_authorize_trade_intent_manual_close_dispatches_to_existing_position_authority() -> None:
    origin = ManualOrigin(
        operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW, position_id="pos:1"
    )
    intent = TradeIntent(
        kind=TradeIntentKind.CLOSE, symbol="EURUSD", side="long", origin=origin, proposal_id="req:1"
    )
    decision = authorize_trade_intent(intent, health=_healthy_snapshot())
    assert decision.allowed


# --------------------------------------------------------------------------------
# 2. RiskReductionDecision -- OPEN vs reducing-kind contract separation
# --------------------------------------------------------------------------------


def test_risk_reduction_decision_requires_risk_reducing_action() -> None:
    with pytest.raises(ValueError, match="risk_reduction_decision_requires_risk_reducing_action"):
        RiskReductionDecision(
            decision_id="rrd:1",
            proposal_id="rr:1",
            position_id="pos:1",
            action=TradeIntentKind.OPEN,
            allowed=False,
            reason_codes=("x",),
        )


def test_risk_reduction_decision_allowed_reduce_requires_resulting_quantity() -> None:
    with pytest.raises(ValueError, match="allowed_reduce_or_close_requires_resulting_quantity"):
        RiskReductionDecision(
            decision_id="rrd:1",
            proposal_id="rr:1",
            position_id="pos:1",
            action=TradeIntentKind.REDUCE,
            allowed=True,
            reason_codes=(),
        )


def test_risk_reduction_decision_allowed_modify_protection_requires_resulting_protection() -> None:
    with pytest.raises(ValueError, match="allowed_modify_protection_requires_resulting_protection"):
        RiskReductionDecision(
            decision_id="rrd:1",
            proposal_id="rr:1",
            position_id="pos:1",
            action=TradeIntentKind.MODIFY_PROTECTION,
            allowed=True,
            reason_codes=(),
        )


def test_risk_reduction_decision_allowed_modify_protection_valid_positive_value() -> None:
    decision = RiskReductionDecision(
        decision_id="rrd:1",
        proposal_id="rr:1",
        position_id="pos:1",
        action=TradeIntentKind.MODIFY_PROTECTION,
        allowed=True,
        reason_codes=(),
        resulting_protection=Decimal("1.5"),
    )
    assert decision.resulting_protection == Decimal("1.5")


@pytest.mark.parametrize(
    "resulting_protection",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
    ids=["zero", "negative", "nan", "positive_infinity", "negative_infinity"],
)
def test_risk_reduction_decision_rejects_invalid_resulting_protection(
    resulting_protection: Decimal,
) -> None:
    with pytest.raises(ValueError, match="invalid_resulting_protection"):
        RiskReductionDecision(
            decision_id="rrd:1",
            proposal_id="rr:1",
            position_id="pos:1",
            action=TradeIntentKind.MODIFY_PROTECTION,
            allowed=True,
            reason_codes=(),
            resulting_protection=resulting_protection,
        )


def test_risk_reduction_decision_denied_must_not_carry_resulting_state() -> None:
    with pytest.raises(ValueError, match="denied_decision_must_not_carry_resulting_state"):
        RiskReductionDecision(
            decision_id="rrd:1",
            proposal_id="rr:1",
            position_id="pos:1",
            action=TradeIntentKind.CLOSE,
            allowed=False,
            reason_codes=("max_total_exposure",),
            resulting_quantity=Decimal("0"),
        )


def test_risk_reduction_decision_valid_allowed_close() -> None:
    decision = RiskReductionDecision(
        decision_id="rrd:1",
        proposal_id="rr:1",
        position_id="pos:1",
        action=TradeIntentKind.CLOSE,
        allowed=True,
        reason_codes=(),
        resulting_quantity=Decimal("0"),
    )
    assert decision.resulting_quantity == Decimal("0")


def test_risk_decision_and_risk_reduction_decision_are_distinct_types() -> None:
    """ADR-034 section 2/15: never satisfies RiskDecision.signal_id with a
    fabricated value for a position-management action -- RiskReductionDecision
    has no signal_id field at all.
    """

    assert not hasattr(RiskReductionDecision, "signal_id")
    fields = {f for f in RiskReductionDecision.__dataclass_fields__}
    assert "signal_id" not in fields


# --------------------------------------------------------------------------------
# 4. ExecutionRequest -- broker-agnostic validation
# --------------------------------------------------------------------------------


def _open_request(**overrides: object) -> ExecutionRequest:
    fields: dict[str, object] = dict(
        request_id="req:1",
        idempotency_key="exec:OPEN:proposal:1",
        intent_proposal_id="proposal:1",
        origin_ref="readiness:1",
        instrument_id="inst-1",
        side="long",
        action=TradeIntentKind.OPEN,
        quantity=Decimal("1"),
        created_at=NOW,
    )
    fields.update(overrides)
    return ExecutionRequest(**fields)  # type: ignore[arg-type]


def test_execution_request_open_requires_positive_quantity_and_no_position_ref() -> None:
    with pytest.raises(ExecutionContractError, match="open_requires_positive_quantity"):
        _open_request(quantity=None)
    with pytest.raises(ExecutionContractError, match="open_must_not_reference_position"):
        _open_request(position_ref="pos:1")


def test_execution_request_reduce_requires_position_ref_and_quantity() -> None:
    with pytest.raises(ExecutionContractError, match="reduce_requires_position_ref"):
        _open_request(action=TradeIntentKind.REDUCE, idempotency_key="exec:REDUCE:proposal:1")
    with pytest.raises(ExecutionContractError, match="reduce_requires_positive_quantity"):
        _open_request(
            action=TradeIntentKind.REDUCE,
            idempotency_key="exec:REDUCE:proposal:1",
            position_ref="pos:1",
            quantity=None,
        )


def test_execution_request_close_requires_position_ref_quantity_optional() -> None:
    with pytest.raises(ExecutionContractError, match="close_requires_position_ref"):
        _open_request(
            action=TradeIntentKind.CLOSE, idempotency_key="exec:CLOSE:proposal:1", quantity=None
        )
    request = _open_request(
        action=TradeIntentKind.CLOSE,
        idempotency_key="exec:CLOSE:proposal:1",
        position_ref="pos:1",
        quantity=None,
    )
    assert request.quantity is None


def test_execution_request_modify_protection_requires_position_ref_and_protection_no_quantity() -> (
    None
):
    with pytest.raises(ExecutionContractError, match="modify_protection_requires_position_ref"):
        _open_request(
            action=TradeIntentKind.MODIFY_PROTECTION,
            idempotency_key="exec:MODIFY_PROTECTION:proposal:1",
            quantity=None,
        )
    with pytest.raises(ExecutionContractError, match="modify_protection_must_not_carry_quantity"):
        _open_request(
            action=TradeIntentKind.MODIFY_PROTECTION,
            idempotency_key="exec:MODIFY_PROTECTION:proposal:1",
            position_ref="pos:1",
        )
    with pytest.raises(
        ExecutionContractError, match="modify_protection_requires_protection_payload"
    ):
        _open_request(
            action=TradeIntentKind.MODIFY_PROTECTION,
            idempotency_key="exec:MODIFY_PROTECTION:proposal:1",
            position_ref="pos:1",
            quantity=None,
        )
    request = _open_request(
        action=TradeIntentKind.MODIFY_PROTECTION,
        idempotency_key="exec:MODIFY_PROTECTION:proposal:1",
        position_ref="pos:1",
        quantity=None,
        protection=ProtectionRequest(stop_price=Decimal("1.5")),
    )
    assert request.protection is not None


def test_execution_request_rejects_naive_created_at() -> None:
    with pytest.raises(ExecutionContractError, match="requires_timezone"):
        _open_request(created_at=datetime(2026, 10, 3, 12, 0))


def test_execution_request_has_no_broker_specific_fields() -> None:
    """ADR-034 section 4: no MT5-specific object, no broker symbol/digits/lot
    step/filling-mode/broker-name field anywhere on this generic contract.
    """

    field_names = set(ExecutionRequest.__dataclass_fields__)
    forbidden_substrings = ("broker_symbol", "mt5", "digits", "lot_step", "filling", "broker_name")
    for name in field_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower()


def test_price_constraint_and_protection_request_reject_invalid_values() -> None:
    with pytest.raises(ExecutionContractError, match="invalid_limit_price"):
        PriceConstraint(limit_price=Decimal("-1"))
    with pytest.raises(ExecutionContractError, match="invalid_stop_price"):
        ProtectionRequest(stop_price=Decimal("0"))
    with pytest.raises(ExecutionContractError, match="protection_request_requires_a_value"):
        ProtectionRequest()


# --------------------------------------------------------------------------------
# 6. Idempotency -- stable identity derivation
# --------------------------------------------------------------------------------


def test_execution_request_idempotency_key_is_stable_for_same_intent() -> None:
    intent = TradeIntent(
        kind=TradeIntentKind.OPEN,
        symbol="EURUSD",
        side="long",
        origin=EntryOrigin(signal_decision_ref="signal:1", entry_readiness_ref="readiness:1"),
        proposal_id="proposal:1",
    )
    key_a = execution_request_idempotency_key(intent)
    key_b = execution_request_idempotency_key(intent)
    assert key_a == key_b == "exec:OPEN:proposal:1"


def test_execution_request_idempotency_key_differs_by_kind_for_same_proposal() -> None:
    reduce_intent = TradeIntent(
        kind=TradeIntentKind.REDUCE,
        symbol="EURUSD",
        side="long",
        origin=PositionOrigin(position_id="pos:1", exit_decision_ref="exit:1"),
        proposal_id="proposal:1",
    )
    close_intent = TradeIntent(
        kind=TradeIntentKind.CLOSE,
        symbol="EURUSD",
        side="long",
        origin=PositionOrigin(position_id="pos:1", exit_decision_ref="exit:1"),
        proposal_id="proposal:1",
    )
    assert execution_request_idempotency_key(reduce_intent) != execution_request_idempotency_key(
        close_intent
    )


def test_build_execution_request_derives_idempotency_key_and_origin_ref_from_intent() -> None:
    origin = ManualOrigin(operator_ref="operator:1", manual_request_id="req:1", requested_at=NOW)
    intent = TradeIntent(
        kind=TradeIntentKind.OPEN, symbol="EURUSD", side="long", origin=origin, proposal_id="req:1"
    )
    request = build_execution_request(
        intent,
        request_id="exec-req:1",
        instrument_id="inst-1",
        created_at=NOW,
        quantity=Decimal("1"),
    )
    assert request.idempotency_key == "exec:OPEN:req:1"
    assert request.origin_ref == "req:1"
    assert request.intent_proposal_id == "req:1"


# --------------------------------------------------------------------------------
# 5. ExecutionResult -- partial/unknown semantics, unsafe-retry invariant
# --------------------------------------------------------------------------------


def _result(**overrides: object) -> ExecutionResult:
    fields: dict[str, object] = dict(
        result_id="res:1",
        request_ref="req:1",
        status=ExecutionStatus.FILLED,
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        remaining_quantity=Decimal("0"),
        observed_at=NOW,
    )
    fields.update(overrides)
    return ExecutionResult(**fields)  # type: ignore[arg-type]


def test_unknown_result_must_not_assert_remaining_quantity() -> None:
    with pytest.raises(
        ExecutionContractError, match="unknown_result_must_not_assert_remaining_quantity"
    ):
        _result(
            status=ExecutionStatus.UNKNOWN,
            filled_quantity=Decimal("0"),
            remaining_quantity=Decimal("1"),
        )
    result = _result(
        status=ExecutionStatus.UNKNOWN, filled_quantity=Decimal("0"), remaining_quantity=None
    )
    assert result.remaining_quantity is None


def test_rejected_result_must_have_zero_fill() -> None:
    with pytest.raises(ExecutionContractError, match="rejected_result_must_have_zero_fill"):
        _result(
            status=ExecutionStatus.REJECTED,
            filled_quantity=Decimal("1"),
            remaining_quantity=Decimal("0"),
        )


def test_partially_filled_requires_positive_fill_and_remaining() -> None:
    with pytest.raises(
        ExecutionContractError, match="partially_filled_requires_positive_fill_and_remaining"
    ):
        _result(
            status=ExecutionStatus.PARTIALLY_FILLED,
            filled_quantity=Decimal("1"),
            remaining_quantity=Decimal("0"),
        )
    result = _result(
        status=ExecutionStatus.PARTIALLY_FILLED,
        requested_quantity=Decimal("2"),
        filled_quantity=Decimal("1"),
        remaining_quantity=Decimal("1"),
    )
    assert result.status is ExecutionStatus.PARTIALLY_FILLED


@pytest.mark.parametrize(
    "status", [ExecutionStatus.UNKNOWN, ExecutionStatus.ACCEPTED, ExecutionStatus.PARTIALLY_FILLED]
)
def test_unsafe_statuses_are_never_safe_to_retry(status: ExecutionStatus) -> None:
    kwargs: dict[str, object] = dict(
        result_id="res:1",
        request_ref="req:1",
        status=status,
        requested_quantity=Decimal("2"),
        observed_at=NOW,
    )
    if status is ExecutionStatus.UNKNOWN:
        kwargs["filled_quantity"] = Decimal("0")
        kwargs["remaining_quantity"] = None
    elif status is ExecutionStatus.ACCEPTED:
        kwargs["filled_quantity"] = Decimal("0")
        kwargs["remaining_quantity"] = Decimal("2")
    else:
        kwargs["filled_quantity"] = Decimal("1")
        kwargs["remaining_quantity"] = Decimal("1")
    result = ExecutionResult(**kwargs)  # type: ignore[arg-type]
    assert not is_safe_to_retry_without_reconciliation(result)


def test_clean_rejected_zero_fill_is_safe_to_retry() -> None:
    result = _result(
        status=ExecutionStatus.REJECTED,
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("1"),
    )
    assert is_safe_to_retry_without_reconciliation(result)


def test_filled_result_is_not_safe_to_retry_would_duplicate() -> None:
    result = _result()
    assert result.status is ExecutionStatus.FILLED
    assert not is_safe_to_retry_without_reconciliation(result)


# --------------------------------------------------------------------------------
# 7. Reconciliation -- states and the UNSYNCHRONIZED/UNKNOWN => NO NEW TRADE rule
# --------------------------------------------------------------------------------


def test_match_finding_is_synchronized_and_does_not_block_new_trade() -> None:
    record = ReconciliationRecord(
        position_ref="pos:1", finding=ReconciliationFinding.MATCH, observed_at=NOW
    )
    assert record.status is ReconciliationStatus.SYNCHRONIZED
    assert not reconciliation_blocks_new_trade(record.status)


@pytest.mark.parametrize(
    "finding",
    [
        ReconciliationFinding.LOCAL_OPEN_BROKER_MISSING,
        ReconciliationFinding.BROKER_POSITION_LOCAL_MISSING,
        ReconciliationFinding.QUANTITY_MISMATCH,
        ReconciliationFinding.PROTECTION_MISMATCH,
    ],
)
def test_mismatch_findings_are_unsynchronized_and_block_new_trade(
    finding: ReconciliationFinding,
) -> None:
    record = ReconciliationRecord(
        position_ref="pos:1",
        finding=finding,
        local_quantity=Decimal("1"),
        broker_quantity=Decimal("2"),
        observed_at=NOW,
    )
    assert record.status is ReconciliationStatus.UNSYNCHRONIZED
    assert reconciliation_blocks_new_trade(record.status)


@pytest.mark.parametrize(
    "finding",
    [
        ReconciliationFinding.EXECUTION_RESULT_UNKNOWN,
        ReconciliationFinding.RESTART_RECOVERY_PENDING,
    ],
)
def test_unknown_findings_block_new_trade(finding: ReconciliationFinding) -> None:
    record = ReconciliationRecord(position_ref="pos:1", finding=finding, observed_at=NOW)
    assert record.status is ReconciliationStatus.UNKNOWN
    assert reconciliation_blocks_new_trade(record.status)


def test_reconciliation_requires_timezone_aware_observed_at() -> None:
    with pytest.raises(ValueError, match="requires_timezone"):
        ReconciliationRecord(
            position_ref="pos:1",
            finding=ReconciliationFinding.MATCH,
            observed_at=datetime(2026, 10, 3, 12, 0),
        )


def test_quantity_mismatch_requires_both_quantities() -> None:
    with pytest.raises(ValueError, match="quantity_mismatch_requires_both_quantities"):
        ReconciliationRecord(
            position_ref="pos:1", finding=ReconciliationFinding.QUANTITY_MISMATCH, observed_at=NOW
        )


# --------------------------------------------------------------------------------
# 8. CLOSE ALL ownership boundary
# --------------------------------------------------------------------------------


def _position(
    position_id: str, symbol: str, state: TradeState, quantity: Decimal
) -> PositionRecord:
    return PositionRecord(
        position_id=position_id,
        symbol=symbol,
        side="long",
        state=state,
        quantity=quantity,
        entry_price=Decimal("1.1"),
        protection=ProtectionLevels(stop_price=Decimal("1.0"), target_prices=(Decimal("1.2"),)),
        opened_at=NOW,
        source_signal_decision_ref="signal:1",
        source_entry_readiness_ref="readiness:1",
    )


def test_owned_open_positions_includes_only_open_and_managing() -> None:
    positions = (
        _position("pos:1", "EURUSD", TradeState.OPEN, Decimal("1")),
        _position("pos:2", "EURUSD", TradeState.MANAGING, Decimal("1")),
        _position("pos:3", "EURUSD", TradeState.CLOSED, Decimal("0")),
    )
    owned = owned_open_positions(positions)
    assert {p.position_id for p in owned} == {"pos:1", "pos:2"}


def test_owned_open_positions_scoped_by_symbol() -> None:
    positions = (
        _position("pos:1", "EURUSD", TradeState.OPEN, Decimal("1")),
        _position("pos:2", "GBPUSD", TradeState.OPEN, Decimal("1")),
    )
    owned = owned_open_positions(positions, symbol="EURUSD")
    assert {p.position_id for p in owned} == {"pos:1"}


def test_build_manual_close_all_intents_never_touches_positions_outside_the_given_collection() -> (
    None
):
    """CLOSE ALL ownership comes only from the durable positions explicitly
    passed in -- there is no broker call, no implicit universe of positions.
    """

    positions = (
        _position("pos:1", "EURUSD", TradeState.OPEN, Decimal("1")),
        _position("pos:2", "EURUSD", TradeState.CLOSED, Decimal("0")),
    )
    intents = build_manual_close_all_intents(
        positions,
        operator_ref="operator:1",
        manual_request_id_prefix="closeall:1",
        requested_at=NOW,
    )
    assert len(intents) == 1
    intent = intents[0]
    assert intent.kind is TradeIntentKind.CLOSE
    assert isinstance(intent.origin, ManualOrigin)
    assert intent.origin.position_id == "pos:1"


def test_build_manual_close_all_intents_each_get_distinct_idempotent_identity() -> None:
    positions = (
        _position("pos:1", "EURUSD", TradeState.OPEN, Decimal("1")),
        _position("pos:2", "EURUSD", TradeState.MANAGING, Decimal("1")),
    )
    intents = build_manual_close_all_intents(
        positions,
        operator_ref="operator:1",
        manual_request_id_prefix="closeall:1",
        requested_at=NOW,
    )
    proposal_ids = {intent.proposal_id for intent in intents}
    assert len(proposal_ids) == 2


# --------------------------------------------------------------------------------
# 9. Safety invariants
# --------------------------------------------------------------------------------


def test_denied_authority_decision_cannot_be_treated_as_transmittable() -> None:
    decision = NewTradeAuthority.evaluate(
        config=TradingConfig(mode=TradingMode.AUTO, assisted_confirmation=True),
        health=_healthy_snapshot(),
        entry_readiness="READY",
        risk_decision=_allow_risk_decision(),
    )
    assert decision.transmissibility is not ExecutionTransmissibility.TRANSMITTABLE


def test_auto_mode_never_authorized_in_phase_1() -> None:
    decision = NewTradeAuthority.evaluate_manual(
        config=TradingConfig(mode=TradingMode.AUTO, assisted_confirmation=True),
        health=_healthy_snapshot(),
        risk_decision=_allow_risk_decision(),
    )
    assert not decision.allowed
    assert "auto_mode_not_governed" in decision.reason_codes


def test_manual_open_authority_has_no_ai_analysis_parameter() -> None:
    import inspect

    params = inspect.signature(NewTradeAuthority.evaluate_manual).parameters
    assert not any("ai" in name.lower() for name in params)
