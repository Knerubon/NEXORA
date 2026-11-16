"""Historical replay harness for P&F Baseline V1 (research only, no execution path).

Per canonical event ``e_k`` the harness:

1. settles the open position against ``e_k``'s bid/ask (positions opened at ``e_j`` are
   only checked for ``k > j``),
2. fills or cancels a pending entry whose eligibility time has been reached,
3. advances the shared ``ResearchPipeline`` (P&F -> Matrix -> Structure -> Regime ->
   Signal -> Entry Readiness) with ``e_k`` only, then
4. turns a newly emitted signal into an opportunity: Readiness gate, single-position
   gate, signal geometry (invalidation = stop, TP1 = full-exit target), RiskEngine
   evaluation with the decision-time quote, and schedules the entry strictly after
   ``e_k``.

Nothing reads ``e_(k+1)`` or later while deciding at ``e_k``. Broker economics come only
from ``InstrumentCapabilities`` and ``CostModel``. No order, broker or MT5 call exists here.
"""

from __future__ import annotations

import hashlib
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Protocol

from nexora.artifacts import canonical_hash
from nexora.backtest.baseline_v1.models import (
    AccountConfig,
    CostBreakdown,
    CostModel,
    DataAuthorization,
    EvidenceCriteria,
    ExitReason,
    InstrumentCapabilities,
    OpportunityOutcome,
    OpportunityRecord,
    RegimeMetrics,
    ReplayInputError,
    ReplayMetrics,
    ReplayPolicy,
    ReplayResult,
    RunManifest,
    Side,
    TradeRecord,
    Verdict,
)
from nexora.backtest.datasets import verify_events
from nexora.backtest.models import DatasetManifest
from nexora.entry_readiness import EntryReadinessSnapshot
from nexora.market_data.models import NormalizedPriceEvent
from nexora.market_regime import RegimeSnapshot
from nexora.research import PipelineConfig, ResearchPipeline
from nexora.risk import RiskEngine
from nexora.risk.models import AccountSnapshot, PriceSnapshot, RiskProposal
from nexora.signals import ResearchSignal

HARNESS_VERSION = "pnf-baseline-v1-replay-1"
DECIMAL_PRECISION = 34
ZERO = Decimal("0")


class DecisionSource(Protocol):
    """The strategy stream consumed by the harness (``ResearchPipeline`` in production)."""

    def replay(self, event: NormalizedPriceEvent) -> None: ...

    def research_signals(self) -> tuple[ResearchSignal, ...]: ...

    def latest_entry_readiness(self) -> EntryReadinessSnapshot | None: ...

    def latest_regime(self) -> RegimeSnapshot | None: ...


@dataclass(slots=True)
class _Opportunity:
    signal: ResearchSignal
    index: int
    readiness: str
    regime: str
    outcome: OpportunityOutcome | None = None
    risk_reason: str | None = None
    stop: Decimal | None = None
    target: Decimal | None = None

    def freeze(self) -> OpportunityRecord:
        assert self.outcome is not None
        return OpportunityRecord(
            signal_id=self.signal.signal_id,
            side=self.signal.side,
            decision_event=self.signal.source_refs[-1] if self.signal.source_refs else "",
            decision_index=self.index,
            decision_time=self.signal.decision_time,
            readiness_state=self.readiness,
            regime=self.regime,
            outcome=self.outcome,
            risk_reason=self.risk_reason,
            stop_price=self.stop,
            target_price=self.target,
        )


@dataclass(slots=True)
class _Pending:
    opportunity: _Opportunity
    proposal_id: str
    quantity: Decimal
    reserved_risk: Decimal
    eligible_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class _Position:
    pending: _Pending
    entry_index: int
    entry_time: datetime
    entry_quote: Decimal  # raw ask (long) / bid (short)
    entry_fill: Decimal  # quote plus adverse slippage
    entry_spread: Decimal
    commission: Decimal
    commission_unknown: bool
    swap: Decimal = ZERO
    swap_unknown: int = 0


class BaselineV1Replay:
    def __init__(
        self,
        *,
        pipeline_config: PipelineConfig,
        policy: ReplayPolicy,
        costs: CostModel,
        account: AccountConfig,
        capabilities: InstrumentCapabilities,
        decision_source_factory: Callable[[PipelineConfig], DecisionSource] | None = None,
    ) -> None:
        """``decision_source_factory`` exists only for scripted mechanics tests."""
        if pipeline_config.signals.symbol != capabilities.symbol:
            raise ReplayInputError("capabilities_symbol_mismatch")
        if costs.currency != account.currency or capabilities.account_currency != account.currency:
            raise ReplayInputError("currency_mismatch")
        if account.risk_policy.size_step != capabilities.quantity_step:
            raise ReplayInputError("size_step_mismatch")
        self.pipeline_config = pipeline_config
        self.policy = policy
        self.costs = costs
        self.account = account
        self.capabilities = capabilities
        self._factory: Callable[[PipelineConfig], DecisionSource] = (
            decision_source_factory or ResearchPipeline
        )
        self.decision_source = "scripted" if decision_source_factory else "research_pipeline"

    # ------------------------------------------------------------------ public API
    def run(
        self,
        *,
        events: tuple[NormalizedPriceEvent, ...],
        dataset: DatasetManifest,
        expected_dataset_hash: str,
        authorization: DataAuthorization,
        criteria: EvidenceCriteria | None,
        code_sha: str,
        code_dirty: bool,
    ) -> ReplayResult:
        with localcontext() as ctx:
            ctx.prec = DECIMAL_PRECISION
            return self._run(
                events=events,
                dataset=dataset,
                expected_dataset_hash=expected_dataset_hash,
                authorization=authorization,
                criteria=criteria,
                code_sha=code_sha,
                code_dirty=code_dirty,
            )

    # ------------------------------------------------------------------ preflight
    def _preflight(
        self,
        events: tuple[NormalizedPriceEvent, ...],
        dataset: DatasetManifest,
        expected_dataset_hash: str,
        auth: DataAuthorization,
    ) -> None:
        # Data-scope checks come first, before any price is interpreted.
        if not auth.protected_registry_complete or not auth.registry_authority:
            raise ReplayInputError("protected_registry_incomplete")
        if auth.instrument_id != self.capabilities.instrument_id:
            raise ReplayInputError("instrument_not_authorized")
        scopes = (auth.development,) + ((auth.warmup,) if auth.warmup else ())
        for scope in scopes:
            if any(scope.overlaps(p) for p in auth.protected):
                raise ReplayInputError("scope_overlaps_protected_range")
        if auth.warmup is not None and auth.warmup.end > auth.development.start:
            raise ReplayInputError("warmup_must_precede_development")
        if not events:
            raise ReplayInputError("empty_events")
        if canonical_hash(dataset) != expected_dataset_hash:
            raise ReplayInputError("dataset_hash_mismatch")
        if dataset.quality_status != "complete":
            raise ReplayInputError("dataset_quality_not_complete")
        seen: set[str] = set()
        for event in events:
            if any(p.contains(event.event_time) for p in auth.protected):
                raise ReplayInputError("protected_range_event")
            if not any(scope.contains(event.event_time) for scope in scopes):
                raise ReplayInputError("event_outside_authorized_scope")
            if event.identity_key in seen:
                raise ReplayInputError("duplicate_event_identity")
            seen.add(event.identity_key)
            if event.source != auth.source or event.symbol != self.capabilities.symbol:
                raise ReplayInputError("event_source_or_symbol_not_authorized")
            if event.kind != "tick" or event.bid is None or event.ask is None:
                raise ReplayInputError("tick_bid_ask_required")
            if not (event.bid.is_finite() and event.ask.is_finite()) or not (
                ZERO < event.bid <= event.ask
            ):
                raise ReplayInputError("invalid_quote")
            if event.is_duplicate or event.is_out_of_order:
                raise ReplayInputError("noncanonical_event")
        try:
            verify_events(dataset, events)
        except ValueError as exc:
            raise ReplayInputError(str(exc)) from exc

    # ------------------------------------------------------------------ main loop
    def _run(
        self,
        *,
        events: tuple[NormalizedPriceEvent, ...],
        dataset: DatasetManifest,
        expected_dataset_hash: str,
        authorization: DataAuthorization,
        criteria: EvidenceCriteria | None,
        code_sha: str,
        code_dirty: bool,
    ) -> ReplayResult:
        self._preflight(events, dataset, expected_dataset_hash, authorization)
        pipeline = self._factory(self.pipeline_config)
        risk = RiskEngine(self.account.risk_policy)
        cash = self.account.initial_equity
        position: _Position | None = None
        pending: _Pending | None = None
        opportunities: list[_Opportunity] = []
        trades: list[TradeRecord] = []
        seen_signals: set[str] = set()
        equity_digest = hashlib.sha256()
        peak = cash
        max_dd = ZERO
        max_dd_pct = ZERO
        previous_time: datetime | None = None
        last_event = events[-1]

        for index, event in enumerate(events):
            assert event.bid is not None and event.ask is not None
            # 0. financing for a position held across a rollover boundary
            if position is not None and previous_time is not None:
                self._accrue_swap(position, previous_time, event.event_time)
            # 1. exits for positions opened on an earlier event
            if position is not None and position.entry_index < index:
                exit_reason, exit_quote, exit_fill = self._exit_check(position, event)
                if exit_reason is not None:
                    trade = self._close(position, index, event, exit_reason, exit_quote, exit_fill)
                    trades.append(trade)
                    # entry commission was already debited from cash at the fill
                    cash += trade.net_pnl + position.commission
                    risk.release(position.pending.proposal_id, realized_pnl=trade.net_pnl)
                    position = None
            # 2. pending entry
            if pending is not None and event.event_time >= pending.eligible_at:
                position, outcome = self._try_fill(pending, index, event)
                pending.opportunity.outcome = outcome
                if position is None:
                    risk.release(pending.proposal_id, realized_pnl=ZERO)
                else:
                    cash -= position.commission
                pending = None
            # 3. strategy decision on e_k only
            pipeline.replay(event)
            in_dev = authorization.development.contains(event.event_time)
            for signal in pipeline.research_signals():
                if signal.signal_id in seen_signals:
                    continue
                seen_signals.add(signal.signal_id)
                if signal.status != "active" or signal.decision_time != event.received_at:
                    continue
                if not in_dev:
                    continue  # warmup: state builds, no opportunity is booked
                equity_now = cash + self._unrealized(position, event)
                opportunity, new_pending = self._consider(
                    signal, index, event, pipeline, risk, cash, equity_now, position, pending
                )
                opportunities.append(opportunity)
                if new_pending is not None:
                    pending = new_pending
            # 4. mark-to-market account equity
            equity = cash + self._unrealized(position, event)
            if position is not None:
                equity -= position.swap
            equity_digest.update(f"{index}:{equity}\n".encode())
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > max_dd:
                max_dd = drawdown
            if peak > 0 and drawdown / peak > max_dd_pct:
                max_dd_pct = drawdown / peak
            previous_time = event.event_time

        if pending is not None:
            pending.opportunity.outcome = "no_entry_quote"
            risk.release(pending.proposal_id, realized_pnl=ZERO)
        open_trades: list[TradeRecord] = []
        if position is not None:
            open_trades.append(self._mark_open(position, last_event))
        final_equity = cash + sum((t.net_pnl + t.costs.commission for t in open_trades), ZERO)

        manifest = self._manifest(events, dataset, authorization, criteria, code_sha, code_dirty)
        frozen_opps = tuple(o.freeze() for o in opportunities)
        all_trades = tuple(trades) + tuple(open_trades)
        metrics = _metrics(
            tuple(trades),
            tuple(open_trades),
            frozen_opps,
            max_dd,
            max_dd_pct,
            final_equity,
            criteria,
        )
        verdict, reasons = _verdict(metrics, authorization, criteria)
        body = (manifest, frozen_opps, all_trades, equity_digest.hexdigest(), metrics, verdict)
        return ReplayResult(
            manifest=manifest,
            opportunities=frozen_opps,
            trades=all_trades,
            equity_curve_hash=equity_digest.hexdigest(),
            metrics=metrics,
            verdict=verdict,
            verdict_reasons=reasons,
            result_hash=canonical_hash(body),
        )

    # ------------------------------------------------------------------ steps
    def _consider(
        self,
        signal: ResearchSignal,
        index: int,
        event: NormalizedPriceEvent,
        pipeline: DecisionSource,
        risk: RiskEngine,
        cash: Decimal,
        equity: Decimal,
        position: _Position | None,
        pending: _Pending | None,
    ) -> tuple[_Opportunity, _Pending | None]:
        assert event.bid is not None and event.ask is not None
        readiness = pipeline.latest_entry_readiness()
        regime = pipeline.latest_regime()
        opp = _Opportunity(
            signal=signal,
            index=index,
            readiness=readiness.state if readiness else "UNAVAILABLE",
            regime=regime.state.label if regime else "unknown",
        )
        decision = signal.decision
        if decision is not None:
            opp.stop = decision.invalidation_price
            tp1 = next((t.price for t in decision.targets if t.name == "TP1"), None)
            opp.target = tp1
        expected_action = "BUY" if signal.side == "long" else "SELL"
        if readiness is None or readiness.state != "READY":
            opp.outcome = "readiness_not_ready"
            return opp, None
        if readiness.signal_action != expected_action:
            opp.outcome = "readiness_mismatch"
            return opp, None
        if position is not None or pending is not None:
            opp.outcome = "position_busy"
            return opp, None
        stop, target = opp.stop, opp.target
        reference = event.ask if signal.side == "long" else event.bid
        if (
            stop is None
            or target is None
            or (signal.side == "long" and not stop < reference < target)
            or (signal.side == "short" and not target < reference < stop)
        ):
            opp.outcome = "invalid_geometry"
            return opp, None
        stop_money = abs(reference - stop) * self.capabilities.contract_multiplier
        proposal = RiskProposal(
            proposal_id=f"{signal.signal_id}:risk",
            signal=signal,
            stop_distance=stop_money,
            requested_size=self.policy.requested_size,
            quality_status="complete",
            account=AccountSnapshot(
                account_id=self.account.account_id,
                currency=self.account.currency,
                equity=equity,
                balance=cash,
                exposure_in_use=ZERO,  # reservations live only inside RiskEngine
                observed_at=event.event_time,
            ),
            # Historical mapping: the decision event's own quote, current as of simulated
            # decision time (never a later quote).
            price=PriceSnapshot(
                symbol=signal.symbol, price=reference, status="live", observed_at=event.event_time
            ),
        )
        decision_r = risk.evaluate(proposal)
        if decision_r.action != "allow":
            opp.outcome = "risk_rejected"
            opp.risk_reason = decision_r.reason
            return opp, None
        quantity = _floor_step(decision_r.approved_size, self.capabilities.quantity_step)
        latency = timedelta(seconds=float(self.policy.entry_latency_seconds))
        expires = decision_r.expires_at or signal.decision_time
        return opp, _Pending(
            opportunity=opp,
            proposal_id=proposal.proposal_id,
            quantity=quantity,
            reserved_risk=decision_r.reserved_risk,
            eligible_at=signal.decision_time + latency,
            expires_at=expires,
        )

    def _try_fill(
        self, pending: _Pending, index: int, event: NormalizedPriceEvent
    ) -> tuple[_Position | None, OpportunityOutcome]:
        assert event.bid is not None and event.ask is not None
        opp = pending.opportunity
        assert opp.stop is not None and opp.target is not None
        if event.event_time > pending.expires_at:
            return None, "risk_approval_expired"
        max_age = timedelta(seconds=float(self.policy.max_entry_quote_age_seconds))
        if event.event_time - pending.eligible_at > max_age:
            return None, "stale_entry_quote"
        side = opp.signal.side
        if side == "long":
            if event.bid <= opp.stop:
                return None, "entry_through_stop"
            if event.bid >= opp.target:
                return None, "entry_through_target"
            quote = event.ask
            fill = quote + self.costs.slippage_price_units_per_market_fill
        else:
            if event.ask >= opp.stop:
                return None, "entry_through_stop"
            if event.ask <= opp.target:
                return None, "entry_through_target"
            quote = event.bid
            fill = quote - self.costs.slippage_price_units_per_market_fill
        commission, unknown = self._commission(pending.quantity)
        return (
            _Position(
                pending=pending,
                entry_index=index,
                entry_time=event.event_time,
                entry_quote=quote,
                entry_fill=fill,
                entry_spread=event.ask - event.bid,
                commission=commission,
                commission_unknown=unknown,
            ),
            "entered",
        )

    def _exit_check(
        self, position: _Position, event: NormalizedPriceEvent
    ) -> tuple[ExitReason | None, Decimal, Decimal]:
        assert event.bid is not None and event.ask is not None
        opp = position.pending.opportunity
        assert opp.stop is not None and opp.target is not None
        slip = self.costs.slippage_price_units_per_market_fill
        timed_out = self.policy.max_hold_seconds is not None and (
            event.event_time
            >= position.entry_time + timedelta(seconds=self.policy.max_hold_seconds)
        )
        if opp.signal.side == "long":
            if event.bid <= opp.stop:  # stop-market: first touching quote, gap fills worse
                return "stop", event.bid, event.bid - slip
            if event.bid >= opp.target:  # TP1 limit: never better than the target
                return "tp1", opp.target, opp.target
            if timed_out:
                return "max_hold", event.bid, event.bid - slip
            return None, ZERO, ZERO
        if event.ask >= opp.stop:
            return "stop", event.ask, event.ask + slip
        if event.ask <= opp.target:
            return "tp1", opp.target, opp.target
        if timed_out:
            return "max_hold", event.ask, event.ask + slip
        return None, ZERO, ZERO

    def _close(
        self,
        position: _Position,
        index: int,
        event: NormalizedPriceEvent,
        reason: ExitReason,
        exit_quote: Decimal,
        exit_fill: Decimal,
    ) -> TradeRecord:
        assert event.bid is not None and event.ask is not None
        exit_commission, exit_unknown = self._commission(position.pending.quantity)
        slip_fills = 1 + (1 if reason != "tp1" else 0)
        return self._record(
            position,
            status="closed",
            exit_index=index,
            exit_time=event.event_time,
            exit_quote=exit_quote,
            exit_fill=exit_fill,
            exit_reason=reason,
            exit_spread=event.ask - event.bid,
            exit_commission=exit_commission,
            exit_commission_unknown=exit_unknown,
            slip_fills=slip_fills,
        )

    def _mark_open(self, position: _Position, event: NormalizedPriceEvent) -> TradeRecord:
        assert event.bid is not None and event.ask is not None
        mark = event.bid if position.pending.opportunity.signal.side == "long" else event.ask
        return self._record(
            position,
            status="open_censored",
            exit_index=None,
            exit_time=None,
            exit_quote=mark,
            exit_fill=None,
            exit_reason=None,
            exit_spread=ZERO,
            exit_commission=ZERO,
            exit_commission_unknown=False,
            slip_fills=1,
        )

    def _record(
        self,
        position: _Position,
        *,
        status: str,
        exit_index: int | None,
        exit_time: datetime | None,
        exit_quote: Decimal,
        exit_fill: Decimal | None,
        exit_reason: ExitReason | None,
        exit_spread: Decimal,
        exit_commission: Decimal,
        exit_commission_unknown: bool,
        slip_fills: int,
    ) -> TradeRecord:
        opp = position.pending.opportunity
        assert opp.stop is not None and opp.target is not None
        qty = position.pending.quantity
        mult = self.capabilities.contract_multiplier
        direction = Decimal(1) if opp.signal.side == "long" else Decimal(-1)
        gross = (exit_quote - position.entry_quote) * direction * qty * mult
        slippage = self.costs.slippage_price_units_per_market_fill * slip_fills * qty * mult
        commission = position.commission + exit_commission
        costs = CostBreakdown(
            commission=commission,
            slippage=slippage,
            spread=(position.entry_spread + exit_spread) * qty * mult,
            swap=position.swap,
            swap_unknown_rollovers=position.swap_unknown,
            commission_unknown=position.commission_unknown or exit_commission_unknown,
        )
        net = gross - slippage - commission - position.swap
        return TradeRecord(
            trade_id=f"trade:{opp.signal.signal_id}",
            signal_id=opp.signal.signal_id,
            side=opp.signal.side,
            regime=opp.regime,
            quantity=qty,
            decision_index=opp.index,
            entry_index=position.entry_index,
            entry_time=position.entry_time,
            entry_price=position.entry_fill,
            stop_price=opp.stop,
            target_price=opp.target,
            exit_index=exit_index,
            exit_time=exit_time,
            exit_price=exit_fill,
            exit_reason=exit_reason,
            status="closed" if status == "closed" else "open_censored",
            gross_pnl=gross,
            costs=costs,
            net_pnl=net,
            risk_proposal_id=position.pending.proposal_id,
            reserved_risk=position.pending.reserved_risk,
        )

    def _unrealized(self, position: _Position | None, event: NormalizedPriceEvent) -> Decimal:
        if position is None:
            return ZERO
        assert event.bid is not None and event.ask is not None
        side: Side = position.pending.opportunity.signal.side
        mark = event.bid if side == "long" else event.ask
        direction = Decimal(1) if side == "long" else Decimal(-1)
        return (
            (mark - position.entry_fill)
            * direction
            * position.pending.quantity
            * self.capabilities.contract_multiplier
        )

    def _commission(self, quantity: Decimal) -> tuple[Decimal, bool]:
        rate = self.costs.commission_per_unit_per_side
        if rate is None:
            return ZERO, True
        return rate * quantity, False

    def _accrue_swap(self, position: _Position, start: datetime, end: datetime) -> None:
        crossings = _rollovers_between(start, end, self.costs.rollover_hour_utc)
        if crossings == 0:
            return
        side = position.pending.opportunity.signal.side
        rate = (
            self.costs.swap_long_per_unit_per_rollover
            if side == "long"
            else self.costs.swap_short_per_unit_per_rollover
        )
        if rate is None:
            position.swap_unknown += crossings
            return
        # Positive rate = charge to the account.
        position.swap += rate * crossings * position.pending.quantity

    def _manifest(
        self,
        events: tuple[NormalizedPriceEvent, ...],
        dataset: DatasetManifest,
        auth: DataAuthorization,
        criteria: EvidenceCriteria | None,
        code_sha: str,
        code_dirty: bool,
    ) -> RunManifest:
        return RunManifest(
            harness_version=HARNESS_VERSION,
            decision_source=self.decision_source,
            code_sha=code_sha,
            code_dirty=code_dirty,
            python=".".join(str(v) for v in sys.version_info[:3]),
            decimal_precision=DECIMAL_PRECISION,
            instrument=self.capabilities,
            data_use=auth.data_use,
            dataset_hash=canonical_hash(dataset),
            events_hash=canonical_hash(events),
            event_count=len(events),
            first_event_time=events[0].event_time,
            last_event_time=events[-1].event_time,
            development_range=auth.development,
            protected_registry_hash=canonical_hash(
                (auth.protected, auth.registry_authority, auth.registry_version)
            ),
            config_hash=canonical_hash(
                (
                    self.pipeline_config,
                    self.policy,
                    self.costs,
                    self.account,
                    self.capabilities,
                    HARNESS_VERSION,
                    self.decision_source,
                )
            ),
            pipeline_config_hash=canonical_hash(self.pipeline_config),
            policy_hash=canonical_hash(self.policy),
            cost_hash=canonical_hash(self.costs),
            account_hash=canonical_hash(self.account),
            criteria_hash=canonical_hash(criteria) if criteria else None,
        )


# ---------------------------------------------------------------------- helpers
def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).quantize(Decimal(1), rounding=ROUND_DOWN) * step


def _rollovers_between(start: datetime, end: datetime, hour: int) -> int:
    """Count rollover instants ``r`` with ``start < r <= end``."""
    if end <= start:
        return 0
    first = start.replace(hour=hour, minute=0, second=0, microsecond=0)
    if first <= start:
        first += timedelta(days=1)
    if first > end:
        return 0
    return 1 + int((end - first) // timedelta(days=1))


def _metrics(
    closed: tuple[TradeRecord, ...],
    open_trades: tuple[TradeRecord, ...],
    opportunities: tuple[OpportunityRecord, ...],
    max_dd: Decimal,
    max_dd_pct: Decimal,
    final_equity: Decimal,
    criteria: EvidenceCriteria | None,
) -> ReplayMetrics:
    count = len(closed)
    nets = [t.net_pnl for t in closed]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    net = sum(nets, ZERO)
    gross = sum((t.gross_pnl for t in closed), ZERO)
    total_costs = gross - net
    gain = sum(wins, ZERO)
    loss = -sum(losses, ZERO)
    funnel: dict[str, int] = {}
    for opp in opportunities:
        funnel[opp.outcome] = funnel.get(opp.outcome, 0) + 1
    regimes: dict[str, list[Decimal]] = {}
    for trade in closed:
        regimes.setdefault(trade.regime, []).append(trade.net_pnl)
    by_regime = tuple(
        RegimeMetrics(
            regime=name,
            trade_count=len(values),
            net_pnl=sum(values, ZERO),
            expectancy=sum(values, ZERO) / len(values),
            win_rate=Decimal(sum(1 for v in values if v > 0)) / len(values),
        )
        for name, values in sorted(regimes.items())
    )
    ci_low: Decimal | None = None
    ci_high: Decimal | None = None
    if criteria is not None and count >= 2:
        ci_low, ci_high = _bootstrap_mean_ci(nets, criteria)
    net_complete = not any(
        t.costs.commission_unknown or t.costs.swap_unknown_rollovers for t in closed + open_trades
    )
    return ReplayMetrics(
        closed_trades=count,
        open_censored=len(open_trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=Decimal(len(wins)) / count if count else ZERO,
        gross_pnl=gross,
        total_costs=total_costs,
        net_pnl=net,
        expectancy=net / count if count else ZERO,
        profit_factor=None if loss == 0 else gain / loss,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        final_equity=final_equity,
        expectancy_ci_low=ci_low,
        expectancy_ci_high=ci_high,
        net_complete=net_complete,
        by_regime=by_regime,
        opportunity_funnel=tuple(sorted(funnel.items())),
    )


def _bootstrap_mean_ci(
    values: list[Decimal], criteria: EvidenceCriteria
) -> tuple[Decimal, Decimal]:
    """Deterministic percentile bootstrap of the mean (IID approximation; see limitations)."""
    rng = random.Random(criteria.bootstrap_seed)
    n = len(values)
    means = sorted(
        sum((values[rng.randrange(n)] for _ in range(n)), ZERO) / n
        for _ in range(criteria.bootstrap_resamples)
    )
    alpha = (1 - criteria.confidence_level) / 2
    lo = int((alpha * len(means)).to_integral_value(rounding=ROUND_DOWN))
    hi = min(len(means) - 1, len(means) - 1 - lo)
    return means[lo], means[hi]


def _verdict(
    metrics: ReplayMetrics, auth: DataAuthorization, criteria: EvidenceCriteria | None
) -> tuple[Verdict, tuple[str, ...]]:
    reasons: list[str] = []
    if auth.data_use != "development":
        reasons.append("synthetic_data_is_not_market_evidence")
    if criteria is None:
        reasons.append("evidence_criteria_not_registered")
    if not metrics.net_complete:
        reasons.append("unknown_costs_block_net_claim")
    if reasons:
        return "BLOCKED", tuple(reasons)
    assert criteria is not None
    if metrics.closed_trades < criteria.min_closed_trades:
        return "INSUFFICIENT_EVIDENCE", ("closed_trades_below_preregistered_minimum",)
    assert metrics.expectancy_ci_low is not None and metrics.expectancy_ci_high is not None
    if metrics.expectancy_ci_high <= 0:
        return "NO_EDGE", ("net_expectancy_ci_entirely_nonpositive",)
    pf_ok = metrics.profit_factor is None or metrics.profit_factor >= criteria.min_profit_factor
    if metrics.expectancy_ci_low > 0 and pf_ok:
        return "SUPPORTS_EDGE", ("net_expectancy_ci_above_zero", "profit_factor_meets_minimum")
    return "INSUFFICIENT_EVIDENCE", ("net_expectancy_ci_includes_zero_or_pf_below_minimum",)
