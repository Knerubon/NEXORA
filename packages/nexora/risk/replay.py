"""P10 replay integration for risk evaluation parity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal

from nexora.risk.engine import RiskEngine
from nexora.risk.models import AccountSnapshot, PriceSnapshot, RiskDecision, RiskProposal
from nexora.signals import ResearchSignal


@dataclass(frozen=True, slots=True)
class RiskReplayResult:
    decisions: tuple[RiskDecision, ...]
    accepted: int
    rejected: int


def replay_signals_with_risk(
    *,
    engine: RiskEngine,
    signals: tuple[ResearchSignal, ...],
    quality_status: str,
    starting_equity: Decimal,
) -> RiskReplayResult:
    decisions: list[RiskDecision] = []
    for index, signal in enumerate(signals, 1):
        observed_at = signal.decision_time.astimezone(UTC)
        proposal = RiskProposal(
            proposal_id=f"replay-{index}:{signal.signal_id}",
            signal=signal,
            stop_distance=Decimal("1.0"),
            requested_size=Decimal("1.0"),
            quality_status=quality_status,
            account=AccountSnapshot(
                account_id="paper-account",
                currency=engine.policy.currency,
                equity=starting_equity,
                balance=starting_equity,
                exposure_in_use=Decimal("0"),
                observed_at=observed_at,
            ),
            price=PriceSnapshot(
                symbol=signal.symbol,
                price=Decimal("100.0"),
                status="live",
                observed_at=observed_at,
            ),
        )
        decision = engine.evaluate(proposal)
        decisions.append(decision)
        if decision.action == "allow":
            simulated_pnl = Decimal("15.0") if signal.side == "long" else Decimal("-10.0")
            engine.release(proposal.proposal_id, realized_pnl=simulated_pnl)
    accepted = sum(1 for decision in decisions if decision.action == "allow")
    rejected = len(decisions) - accepted
    return RiskReplayResult(decisions=tuple(decisions), accepted=accepted, rejected=rejected)
