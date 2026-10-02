"""Risk migration boundary scaffold (ADR-033 section 12). Contract only, not wired.

This module defines the narrower proposal shape a future
``RiskEngine.evaluate_reduction()`` would consume. It does NOT add that method to
RiskEngine and does NOT change RiskEngine's existing behavior in any way —
``RiskProposal``/``RiskEngine.evaluate()`` keep their exact current shape.

BLOCKER (documented per task instruction, not worked around): RiskDecision.signal_id
(packages/nexora/risk/models.py) is a required, non-optional str field. A future
evaluate_reduction() must not satisfy it with a synthetic/fake ResearchSignal or a
fabricated signal_id for a position-management action (ADR-033 section 15) — that
would silently reintroduce the exact "risk-reducing-action-pretending-to-be-a-signal"
anti-pattern the TradeIntent/PositionOrigin boundary (ADR-033 section 10) exists to
prevent. Resolving this (a new decision type for reductions, or a widened
RiskDecision with a non-signal identity field) is a RiskEngine-owning decision, out
of this task's scope, and is left to the implementer of evaluate_reduction() per
ADR-033 section 15's explicit instruction. This module adds no workaround for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nexora.autonomous_contracts import RISK_REDUCING_KINDS, PositionOrigin, TradeIntent


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
