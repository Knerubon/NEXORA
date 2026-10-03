"""Execution Guard (ADR-034 pipeline: Authority -> Execution Guard -> ExecutionRequest).

Pure, deterministic, side-effect free, fail-closed. No I/O, no broker/MT5/network/DB
code, no journal, no clock read. It imports nothing from pattern/signal/feature code.

The guard is the last gate before an ``ExecutionRequest`` may exist. It consumes only
already-produced, typed inputs:

* ``AuthorityDecision`` -- read via its typed ``policy_status``/``transmissibility``,
  never by parsing ``reason_codes`` (ADR-034 section 3).
* ``TradeIntent`` -- the sole source of the request (via ``build_execution_request``,
  so ``idempotency_key``/``origin_ref`` cannot drift; ADR-034 sections 4/6).
* ``ReconciliationStatus`` -- ``reconciliation_blocks_new_trade`` gates OPEN only
  (ADR-034 section 7); risk-reducing intents are not blocked by reconciliation alone.
* ``TradingConfig`` (mode) and a kill-switch flag.

Documented limits:

* Duplicate-order checking is deliberately NOT done here. The durable idempotency
  store is a separate track (ADR-034 section 6); the guard only guarantees the request
  carries the deterministic key such a store would use.
* The kill switch blocks OPEN (new exposure) only. Blocking risk-reducing intents
  would be a new trading semantic not frozen by ADR-033/034, so it is not invented here;
  risk-reducing intents remain gated by authority transmissibility and mode.
* Allowing a request is never an instruction to transmit; broker execution stays
  disabled (AGENTS.md section 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from nexora.autonomous.authority import (
    AuthorityDecision,
    AuthorityPolicyStatus,
    ExecutionTransmissibility,
    TradingConfig,
)
from nexora.autonomous_contracts import TradeIntent, TradeIntentKind, TradingMode, is_risk_reducing
from nexora.execution.idempotency import build_execution_request
from nexora.execution.models import (
    ExecutionContractError,
    ExecutionRequest,
    PriceConstraint,
    ProtectionRequest,
)
from nexora.execution.reconciliation import ReconciliationStatus
from nexora.execution.reconciliation import blocks_new_trade as reconciliation_blocks_new_trade


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """Typed guard outcome. ``request`` is present if and only if ``allowed``."""

    allowed: bool
    reason_codes: tuple[str, ...]
    request: ExecutionRequest | None = None

    def __post_init__(self) -> None:
        if self.allowed:
            if self.reason_codes or self.request is None:
                raise ValueError("allowed_guard_decision_requires_request_and_no_reasons")
        elif not self.reason_codes or self.request is not None:
            raise ValueError("denied_guard_decision_requires_reasons_and_no_request")


def _deny(*reason_codes: str) -> GuardDecision:
    return GuardDecision(allowed=False, reason_codes=tuple(reason_codes))


def evaluate_execution_guard(
    *,
    authority: AuthorityDecision | None,
    intent: TradeIntent | None,
    reconciliation: ReconciliationStatus | None,
    config: TradingConfig | None,
    kill_switch: bool | None,
    request_id: str,
    instrument_id: str,
    created_at: datetime,
    quantity: Decimal | None = None,
    price_constraint: PriceConstraint | None = None,
    protection: ProtectionRequest | None = None,
    position_ref: str | None = None,
) -> GuardDecision:
    """Return a ``GuardDecision``; an ``ExecutionRequest`` only when every rule passes.

    Any missing or wrongly-typed input denies (fail closed). All applicable reason
    codes are reported, in a fixed order, so the result is deterministic.
    """

    invalid: list[str] = []
    if not isinstance(authority, AuthorityDecision):
        invalid.append("invalid_authority")
    if not isinstance(intent, TradeIntent):
        invalid.append("invalid_intent")
    if not isinstance(reconciliation, ReconciliationStatus):
        invalid.append("invalid_reconciliation")
    if not isinstance(config, TradingConfig) or not isinstance(config.mode, TradingMode):
        invalid.append("invalid_trading_config")
    if not isinstance(kill_switch, bool):
        invalid.append("invalid_kill_switch")
    if invalid:
        return _deny(*invalid)

    # Narrowing for the type checker; guarded by the fail-closed checks above.
    assert isinstance(authority, AuthorityDecision)
    assert isinstance(intent, TradeIntent)
    assert isinstance(reconciliation, ReconciliationStatus)
    assert isinstance(config, TradingConfig)

    reasons: list[str] = []

    if config.mode is TradingMode.AUTO:
        reasons.append("auto_mode_not_governed")
    elif config.mode is TradingMode.SHADOW:
        reasons.append("trading_mode_shadow_observes_only")
    elif (
        config.mode is TradingMode.ASSISTED
        and intent.kind is TradeIntentKind.OPEN
        and not config.assisted_confirmation
    ):
        reasons.append("assisted_confirmation_missing")

    if authority.policy_status is not AuthorityPolicyStatus.AUTHORIZED:
        reasons.append("authority_denied")
    elif authority.transmissibility is ExecutionTransmissibility.NOT_TRANSMITTABLE:
        reasons.append("authority_not_transmittable")
    elif authority.transmissibility is not ExecutionTransmissibility.TRANSMITTABLE:
        reasons.append("authority_not_applicable")

    if not is_risk_reducing(intent.kind):
        if reconciliation_blocks_new_trade(reconciliation):
            reasons.append(f"reconciliation_blocks_new_trade:{reconciliation.value}")
        if kill_switch:
            reasons.append("kill_switch_armed")

    if reasons:
        return _deny(*reasons)

    try:
        request = build_execution_request(
            intent,
            request_id=request_id,
            instrument_id=instrument_id,
            created_at=created_at,
            quantity=quantity,
            price_constraint=price_constraint,
            protection=protection,
            position_ref=position_ref,
        )
    except (ExecutionContractError, AttributeError, TypeError):
        return _deny("execution_request_invalid")
    return GuardDecision(allowed=True, reason_codes=(), request=request)
