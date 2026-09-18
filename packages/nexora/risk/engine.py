"""Deterministic risk engine for research/paper flows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC
from decimal import ROUND_DOWN, Decimal

from nexora.risk.models import RiskDecision, RiskPolicy, RiskProposal, RiskState


class RiskInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class RiskEngine:
    policy: RiskPolicy
    _state: RiskState
    _decisions: dict[str, RiskDecision]

    def __init__(self, policy: RiskPolicy) -> None:
        self.policy = policy
        self._state = RiskState(
            status="ready",
            policy_version=policy.version,
            trading_day="unknown",
            daily_pnl=Decimal("0"),
            equity_peak=Decimal("0"),
            reserved_exposure=Decimal("0"),
            kill_switch=False,
            seen_proposals=(),
        )
        self._decisions = {}

    def evaluate(self, proposal: RiskProposal) -> RiskDecision:
        if proposal.proposal_id in self._decisions:
            return self._decisions[proposal.proposal_id]
        self._roll_trading_day(proposal)
        if self._state.kill_switch:
            decision = self._reject(proposal, "kill_switch_active", ("kill_switch",))
            return self._cache_decision(decision)
        if proposal.account.currency != self.policy.currency:
            decision = self._reject(proposal, "currency_mismatch", ("unsupported_currency",))
            return self._cache_decision(decision)
        if proposal.quality_status not in {"complete", "partial"}:
            decision = self._reject(proposal, "quality_unavailable", ("quality_unknown",))
            return self._cache_decision(decision)
        if proposal.price.status != "live" or proposal.price.price is None:
            decision = self._reject(proposal, "stale_or_missing_price", ("price_unavailable",))
            return self._cache_decision(decision)
        if proposal.requested_size <= 0:
            raise RiskInputError("invalid_requested_size")
        if proposal.stop_distance < self.policy.min_stop_distance:
            decision = self._reject(proposal, "stop_distance_too_small", ("invalid_stop_distance",))
            return self._cache_decision(decision)

        max_size_by_trade = self.policy.max_risk_per_trade / proposal.stop_distance
        capped_size = min(proposal.requested_size, max_size_by_trade)
        size = _quantize_down(capped_size, self.policy.size_step)
        if size <= 0:
            decision = self._reject(proposal, "size_quantized_to_zero", ("invalid_size_step",))
            return self._cache_decision(decision)
        reserved_risk = proposal.stop_distance * size
        total_exposure = (
            proposal.account.exposure_in_use
            + self._state.reserved_exposure
            + reserved_risk
        )
        if total_exposure > self.policy.max_total_exposure:
            decision = self._reject(proposal, "exposure_limit", ("max_total_exposure",))
            return self._cache_decision(decision)

        projected_daily_loss = abs(min(self._state.daily_pnl, Decimal("0"))) + reserved_risk
        if projected_daily_loss > self.policy.max_daily_loss:
            decision = self._reject(proposal, "daily_loss_limit", ("max_daily_loss",))
            return self._cache_decision(decision)
        drawdown = self._compute_drawdown(proposal.account.equity)
        if drawdown > self.policy.max_drawdown:
            decision = self._reject(proposal, "drawdown_limit", ("max_drawdown",))
            return self._cache_decision(decision)

        decision = RiskDecision(
            decision_id=f"{proposal.proposal_id}:{self.policy.version}",
            proposal_id=proposal.proposal_id,
            signal_id=proposal.signal.signal_id,
            action="allow",
            reason="allowed",
            reason_codes=("policy_pass",),
            approved_size=size,
            reserved_risk=reserved_risk,
            effective_time=proposal.price.observed_at,
            policy_version=self.policy.version,
            source_refs=proposal.signal.source_refs,
        )
        self._state = replace(
            self._state,
            status="ready",
            reserved_exposure=self._state.reserved_exposure + reserved_risk,
            seen_proposals=self._state.seen_proposals + (proposal.proposal_id,),
            equity_peak=max(self._state.equity_peak, proposal.account.equity),
        )
        return self._cache_decision(decision)

    def release(self, proposal_id: str, *, realized_pnl: Decimal) -> None:
        decision = self._decisions.get(proposal_id)
        if decision is None or decision.action == "reject":
            return
        self._state = replace(
            self._state,
            reserved_exposure=max(
                self._state.reserved_exposure - decision.reserved_risk,
                Decimal("0"),
            ),
            daily_pnl=self._state.daily_pnl + realized_pnl,
        )

    def activate_kill_switch(self) -> None:
        self._state = replace(self._state, kill_switch=True, status="kill_switch")

    def deactivate_kill_switch(self) -> None:
        self._state = replace(self._state, kill_switch=False, status="ready")

    def state(self) -> RiskState:
        return self._state

    def decisions(self) -> tuple[RiskDecision, ...]:
        return tuple(self._decisions[item] for item in self._state.seen_proposals)

    def _roll_trading_day(self, proposal: RiskProposal) -> None:
        trading_day = proposal.account.observed_at.astimezone(UTC).date().isoformat()
        if self._state.trading_day != trading_day:
            self._state = replace(
                self._state,
                trading_day=trading_day,
                daily_pnl=Decimal("0"),
                reserved_exposure=Decimal("0"),
                equity_peak=proposal.account.equity,
            )

    def _reject(self, proposal: RiskProposal, reason: str, codes: tuple[str, ...]) -> RiskDecision:
        return RiskDecision(
            decision_id=f"{proposal.proposal_id}:{self.policy.version}",
            proposal_id=proposal.proposal_id,
            signal_id=proposal.signal.signal_id,
            action="reject",
            reason=reason,
            reason_codes=codes,
            approved_size=Decimal("0"),
            reserved_risk=Decimal("0"),
            effective_time=proposal.price.observed_at,
            policy_version=self.policy.version,
            source_refs=proposal.signal.source_refs,
        )

    def _cache_decision(self, decision: RiskDecision) -> RiskDecision:
        self._decisions[decision.proposal_id] = decision
        if decision.proposal_id not in self._state.seen_proposals:
            self._state = replace(
                self._state,
                seen_proposals=self._state.seen_proposals + (decision.proposal_id,),
                status=(
                    "kill_switch"
                    if self._state.kill_switch
                    else ("blocked" if decision.action == "reject" else self._state.status)
                ),
            )
        return decision

    def _compute_drawdown(self, equity: Decimal) -> Decimal:
        peak = max(self._state.equity_peak, equity)
        if peak <= 0:
            return Decimal("0")
        return peak - equity


def _quantize_down(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return units * step
