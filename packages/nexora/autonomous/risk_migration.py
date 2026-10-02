"""Risk migration boundary scaffold (ADR-033 section 12; ADR-034 section 2).
Contract only, not wired.

This module defines the narrower proposal/decision shapes a future
``RiskEngine.evaluate_reduction()`` would consume and return. It does NOT add
that method to RiskEngine and does NOT change RiskEngine's existing behavior in
any way — ``RiskProposal``/``RiskDecision``/``RiskEngine.evaluate()`` keep their
exact current shape. OPEN stays entirely on the existing entry Risk path
(ADR-034 section 2) — this module only ever covers REDUCE/CLOSE/MODIFY_PROTECTION.

BLOCKER, now RESOLVED by ADR-034 section 2 (previously documented, not worked
around, in ADR-033 section 15): ``RiskDecision.signal_id``
(packages/nexora/risk/models.py) is a required, non-optional ``str`` field. A
future ``evaluate_reduction()`` must not satisfy it with a synthetic/fake
``ResearchSignal`` or a fabricated ``signal_id`` for a position-management
action — that would silently reintroduce the exact
"risk-reducing-action-pretending-to-be-a-signal" anti-pattern the
``TradeIntent``/``PositionOrigin`` boundary (ADR-033 section 10) exists to
prevent. ADR-034 resolves this by freezing ``RiskReductionDecision`` below as a
**new, distinct decision type** — it never reuses or widens ``RiskDecision``,
so ``signal_id`` is simply not a field a reduction decision needs to satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from nexora.autonomous_contracts import (
    RISK_REDUCING_KINDS,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
)


@dataclass(frozen=True, slots=True)
class RiskReductionProposal:
    """ADR-033 section 12 migration boundary. Never consumed by RiskEngine today."""

    proposal_id: str
    position_id: str
    trade_intent: TradeIntent
    current_exposure: Decimal

    def __post_init__(self) -> None:
        if not self.proposal_id.strip() or not self.position_id.strip():
            raise ValueError("missing_risk_reduction_identity")
        if self.trade_intent.kind not in RISK_REDUCING_KINDS:
            raise ValueError("risk_reduction_requires_risk_reducing_kind")
        if not isinstance(self.trade_intent.origin, PositionOrigin):
            raise ValueError("risk_reduction_requires_position_origin")
        if self.trade_intent.origin.position_id != self.position_id:
            raise ValueError("risk_reduction_position_id_mismatch")
        if not self.current_exposure.is_finite() or self.current_exposure < 0:
            raise ValueError("invalid_current_exposure")


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskReductionDecision:
    """ADR-034 section 2. The output type a future
    ``RiskEngine.evaluate_reduction(RiskReductionProposal) -> RiskReductionDecision``
    would return. Deliberately **not** ``RiskDecision`` (see module docstring) —
    a sibling type covering REDUCE/CLOSE/MODIFY_PROTECTION only. Never consumed
    by ``RiskEngine`` or ``PaperSimulator`` today; this freezes the shape only.

    ``action`` reuses ``TradeIntentKind`` (restricted to the risk-reducing
    members) rather than inventing a parallel vocabulary. ``resulting_quantity``
    is the remaining position quantity after an allowed REDUCE/CLOSE;
    ``resulting_protection`` is the new stop price after an allowed
    MODIFY_PROTECTION. A denied decision carries neither, by construction.
    """

    decision_id: str
    proposal_id: str
    position_id: str
    action: TradeIntentKind
    allowed: bool
    reason_codes: tuple[str, ...]
    resulting_quantity: Decimal | None = None
    resulting_protection: Decimal | None = None
    policy_version: str = ""
    effective_time: datetime | None = None
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.decision_id.strip()
            or not self.proposal_id.strip()
            or not self.position_id.strip()
        ):
            raise ValueError("missing_risk_reduction_decision_identity")
        if self.action not in RISK_REDUCING_KINDS:
            raise ValueError("risk_reduction_decision_requires_risk_reducing_action")
        if self.allowed and self.reason_codes:
            raise ValueError("allowed_decision_must_have_no_reason_codes")
        if not self.allowed and not self.reason_codes:
            raise ValueError("denied_decision_requires_reason_codes")

        if self.allowed:
            if (
                self.action in (TradeIntentKind.REDUCE, TradeIntentKind.CLOSE)
                and self.resulting_quantity is None
            ):
                raise ValueError("allowed_reduce_or_close_requires_resulting_quantity")
            if (
                self.action is TradeIntentKind.MODIFY_PROTECTION
                and self.resulting_protection is None
            ):
                raise ValueError("allowed_modify_protection_requires_resulting_protection")
            if self.resulting_quantity is not None and (
                not self.resulting_quantity.is_finite() or self.resulting_quantity < 0
            ):
                raise ValueError("invalid_resulting_quantity")
            if self.resulting_protection is not None and not self.resulting_protection.is_finite():
                raise ValueError("invalid_resulting_protection")
        elif self.resulting_quantity is not None or self.resulting_protection is not None:
            raise ValueError("denied_decision_must_not_carry_resulting_state")
