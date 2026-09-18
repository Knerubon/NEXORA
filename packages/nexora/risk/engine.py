"""Deterministic risk engine for research/paper flows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from threading import RLock
from zoneinfo import ZoneInfo

from nexora.risk.models import RiskDecision, RiskPolicy, RiskProposal, RiskState, signal_fingerprint


class RiskInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class RiskEngine:
    policy: RiskPolicy
    _state: RiskState
    _decisions: dict[str, RiskDecision]
    _released: dict[str, Decimal]
    _proposals: dict[str, RiskProposal]
    _account_id: str | None
    _lock: RLock

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
        self._released = {}
        self._proposals = {}
        self._account_id = None
        self._lock = RLock()

    def evaluate(self, proposal: RiskProposal) -> RiskDecision:
        with self._lock:
            return self._evaluate(proposal)

    def _evaluate(self, proposal: RiskProposal) -> RiskDecision:
        if proposal.proposal_id in self._decisions:
            if self._proposals[proposal.proposal_id] != proposal:
                raise RiskInputError("proposal_identity_conflict")
            return self._decisions[proposal.proposal_id]
        error = self._validate(proposal)
        if error is not None:
            return self._reject(proposal, error, (error,))
        self._account_id = proposal.account.account_id
        self._proposals[proposal.proposal_id] = proposal
        self._roll_trading_day(proposal)
        self._state = replace(
            self._state, equity_peak=max(self._state.equity_peak, proposal.account.equity)
        )
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
            proposal.account.exposure_in_use + self._state.reserved_exposure + reserved_risk
        )
        if total_exposure > self.policy.max_total_exposure:
            decision = self._reject(proposal, "exposure_limit", ("max_total_exposure",))
            return self._cache_decision(decision)

        projected_daily_loss = (
            abs(min(self._state.daily_pnl, Decimal("0")))
            + self._state.reserved_exposure
            + proposal.account.exposure_in_use
            + reserved_risk
        )
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
            effective_time=proposal.signal.decision_time,
            policy_version=self.policy.version,
            source_refs=proposal.signal.source_refs,
            account_id=proposal.account.account_id,
            symbol=proposal.signal.symbol,
            side=proposal.signal.side,
            signal_hash=signal_fingerprint(proposal.signal),
            expires_at=proposal.signal.decision_time
            + timedelta(seconds=self.policy.approval_ttl_seconds),
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
        with self._lock:
            if not realized_pnl.is_finite():
                raise RiskInputError("invalid_realized_pnl")
            if proposal_id in self._released:
                if self._released[proposal_id] != realized_pnl:
                    raise RiskInputError("release_identity_conflict")
                return
            decision = self._decisions.get(proposal_id)
            if decision is None or decision.action == "reject":
                return
            self._released[proposal_id] = realized_pnl
            self._state = replace(
                self._state,
                reserved_exposure=self._state.reserved_exposure - decision.reserved_risk,
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

    def _validate(self, proposal: RiskProposal) -> str | None:
        signal = proposal.signal
        times = (
            signal.decision_time,
            signal.confirmation_time,
            signal.occurrence_time,
            proposal.account.observed_at,
            proposal.price.observed_at,
        )
        if any(t.tzinfo is None or t.utcoffset() is None for t in times):
            raise RiskInputError("timezone_required")
        if not proposal.proposal_id or not proposal.account.account_id:
            return "missing_identity"
        if self._account_id not in (None, proposal.account.account_id):
            return "account_mismatch"
        if (
            signal.status != "active"
            or signal.side not in {"long", "short"}
            or signal.confirmation_time > signal.decision_time
            or signal.occurrence_time > signal.confirmation_time
        ):
            return "invalid_signal"
        if proposal.price.symbol != signal.symbol:
            return "symbol_mismatch"
        for value in (
            proposal.account.equity,
            proposal.account.balance,
            proposal.requested_size,
            proposal.stop_distance,
        ):
            if not value.is_finite() or value <= 0:
                return "invalid_account_or_size"
        if not proposal.account.exposure_in_use.is_finite() or proposal.account.exposure_in_use < 0:
            return "invalid_exposure"
        if proposal.price.price is not None and (
            not proposal.price.price.is_finite() or proposal.price.price <= 0
        ):
            return "invalid_price"
        for observed in (proposal.account.observed_at, proposal.price.observed_at):
            age = (signal.decision_time - observed).total_seconds()
            if not 0 <= age <= self.policy.max_input_age_seconds:
                return "stale_or_future_input"
        day = signal.decision_time.astimezone(ZoneInfo(self.policy.timezone)).date().isoformat()
        if self._state.trading_day != "unknown" and day < self._state.trading_day:
            return "out_of_order_day"
        return None

    def _roll_trading_day(self, proposal: RiskProposal) -> None:
        trading_day = (
            proposal.signal.decision_time.astimezone(ZoneInfo(self.policy.timezone))
            .date()
            .isoformat()
        )
        if self._state.trading_day != trading_day:
            self._state = replace(
                self._state,
                trading_day=trading_day,
                daily_pnl=Decimal("0"),
                equity_peak=max(self._state.equity_peak, proposal.account.equity),
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
            effective_time=proposal.signal.decision_time,
            policy_version=self.policy.version,
            source_refs=proposal.signal.source_refs,
            account_id=proposal.account.account_id,
            symbol=proposal.signal.symbol,
            side=proposal.signal.side,
            signal_hash=signal_fingerprint(proposal.signal),
            expires_at=proposal.signal.decision_time
            + timedelta(seconds=self.policy.approval_ttl_seconds),
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
