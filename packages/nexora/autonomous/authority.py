"""NewTradeAuthority and ExistingPositionAuthority (ADR-033 sections 8 and 11).

Deterministic, fail-closed trade authority. No function here accepts an AIAnalysis
value (ADR-033 section 19 — forbidden by construction, not by convention: there is
no parameter slot for it). No I/O, no broker calls, no execution path.

Governance note (AGENTS.md section 0/9): Phase 1 prohibits live/demo broker orders
and AUTO auto-trading. NewTradeAuthority enforces this directly — TradingMode.AUTO
is never authorized by this module, independent of every other input, until a
dedicated governance ADR lifts AGENTS.md section 0 for a narrow, named scope
(ADR-033 section 21). This is an explicit safety rule, not a placeholder default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nexora.autonomous.health import SystemHealthGate, SystemHealthSnapshot
from nexora.autonomous_contracts import (
    RISK_REDUCING_KINDS,
    EntryOrigin,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
    TradingMode,
)
from nexora.entry_readiness.models import EntryReadinessState
from nexora.risk.models import RiskDecision

ProtectionChange = Literal["TIGHTEN", "WIDEN"]


@dataclass(frozen=True, slots=True)
class TradingConfig:
    """Minimal trading-mode input to NewTradeAuthority (ADR-033 section 8/11).

    ``assisted_confirmation`` is the human confirmation flag for TradingMode.ASSISTED;
    it is meaningless for SHADOW (never trades) and for AUTO (never authorized in
    Phase 1 regardless of this flag).
    """

    mode: TradingMode
    assisted_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    """Deterministic allow/deny with machine reason codes. Never carries AI input."""

    allowed: bool
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.allowed and self.reason_codes:
            raise ValueError("allowed_decision_must_have_no_reason_codes")
        if not self.allowed and not self.reason_codes:
            raise ValueError("denied_decision_requires_reason_codes")


def _deny(*reason_codes: str) -> AuthorityDecision:
    return AuthorityDecision(allowed=False, reason_codes=tuple(reason_codes))


_ALLOW = AuthorityDecision(allowed=True, reason_codes=())


class NewTradeAuthority:
    """ADR-033 section 11. The ONLY authority that may ever approve TradeIntentKind.OPEN.

    Fail-closed: UNKNOWN, UNHEALTHY, STALE or UNSYNCHRONIZED on any SystemHealthGate
    axis denies new exposure. A degraded or absent entry readiness state denies.
    A non-allow RiskDecision denies. TradingMode.AUTO is always denied in Phase 1.
    """

    @staticmethod
    def evaluate(
        *,
        config: TradingConfig,
        health: SystemHealthSnapshot,
        entry_readiness: EntryReadinessState,
        risk_decision: RiskDecision,
    ) -> AuthorityDecision:
        reasons: list[str] = []

        if config.mode is TradingMode.AUTO:
            reasons.append("auto_mode_not_governed")
        elif config.mode is TradingMode.SHADOW:
            reasons.append("trading_mode_shadow_observes_only")
        elif config.mode is TradingMode.ASSISTED and not config.assisted_confirmation:
            reasons.append("assisted_confirmation_missing")

        reasons.extend(f"health_degraded:{axis}" for axis in SystemHealthGate.degraded_axes(health))

        if entry_readiness != "READY":
            reasons.append("entry_not_ready")

        if risk_decision.action != "allow":
            reasons.append("risk_decision_not_allow")

        if reasons:
            return _deny(*reasons)
        return _ALLOW


class ExistingPositionAuthority:
    """ADR-033 section 11. Separate from NewTradeAuthority by design: a degraded
    system must never be interpreted as automatic permission to abandon a position,
    nor as permission to increase its risk.

    Broker connectivity UNHEALTHY denies every action, including CLOSE (an order
    sent into a broken connection cannot be verified, so it is not a safety action).
    Any other degraded axis allows REDUCE/CLOSE (always exposure-reducing by
    TradeIntentKind) and allows MODIFY_PROTECTION only when the caller explicitly
    classifies it as TIGHTEN. A WIDEN, or an unclassified MODIFY_PROTECTION, is
    denied under degradation: TradeIntent carries no size/quantity field the type
    system could use to prove a MODIFY_PROTECTION reduces risk (ADR-033 section 10
    enforcement note), so this is the fail-closed substitute until a future contract
    can validate it directly.
    """

    @staticmethod
    def evaluate(
        *,
        health: SystemHealthSnapshot,
        intent_kind: TradeIntentKind,
        protection_change: ProtectionChange | None = None,
    ) -> AuthorityDecision:
        if intent_kind is TradeIntentKind.OPEN:
            raise ValueError("existing_position_authority_does_not_evaluate_open")
        if intent_kind not in RISK_REDUCING_KINDS:
            raise ValueError("unsupported_trade_intent_kind")

        if SystemHealthGate.broker_unhealthy(health):
            return _deny("broker_unhealthy_fail_closed")

        degraded = SystemHealthGate.degraded_axes(health)
        if not degraded:
            return _ALLOW

        if intent_kind in (TradeIntentKind.REDUCE, TradeIntentKind.CLOSE):
            return _ALLOW

        # MODIFY_PROTECTION under degradation requires an explicit, non-inferred
        # classification. No exposure-increasing action may be disguised as a
        # reduction by omission.
        if protection_change is None:
            return _deny("protection_change_unclassified")
        if protection_change == "WIDEN":
            return _deny("degraded_blocks_exposure_increase")
        return _ALLOW


def authorize_trade_intent(
    intent: TradeIntent,
    *,
    health: SystemHealthSnapshot,
    config: TradingConfig | None = None,
    entry_readiness: EntryReadinessState | None = None,
    risk_decision: RiskDecision | None = None,
    protection_change: ProtectionChange | None = None,
) -> AuthorityDecision:
    """Routes a TradeIntent to the correct authority by kind (ADR-033 section 8).

    No parameter here is an AIAnalysis value. This function only dispatches; it adds
    no additional authorization logic of its own beyond the kind/origin dispatch that
    TradeIntent's own ``__post_init__`` already enforces.
    """

    if intent.kind is TradeIntentKind.OPEN:
        if not isinstance(intent.origin, EntryOrigin):
            raise ValueError("open_requires_entry_origin")
        if config is None or entry_readiness is None or risk_decision is None:
            raise ValueError("open_intent_requires_config_entry_readiness_and_risk_decision")
        return NewTradeAuthority.evaluate(
            config=config,
            health=health,
            entry_readiness=entry_readiness,
            risk_decision=risk_decision,
        )

    if not isinstance(intent.origin, PositionOrigin):
        raise ValueError("risk_reducing_kind_requires_position_origin")
    return ExistingPositionAuthority.evaluate(
        health=health, intent_kind=intent.kind, protection_change=protection_change
    )
