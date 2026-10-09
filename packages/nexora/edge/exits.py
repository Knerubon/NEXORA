"""Research-only SL/TP-aware exit mode on completed OHLC bars.

This is a NEW mode beside the legacy fixed-delay exit in `nexora.backtest.runner`, which is
left unchanged. It is a configurable *research policy*, not the production strategy's exit
rules: TP1/TP2 choice, partial closes, stop movement and position sizing are unresolved
Quant decisions and are deliberately not encoded here.

Bar semantics (assumptions recorded in every result):
* A bar's `event_time` is its open time and `received_at` the time it became available.
* A signal decided at time T can only enter at the open of a bar with event_time >= T plus
  the entry delay. Nothing before that bar is read, and nothing after the exit is used.
* When one bar's range touches both the stop and the target and no tick data orders them,
  the outcome is AMBIGUOUS. The configured policy resolves it (conservative default:
  stop first) and the result keeps both alternatives for sensitivity analysis.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from nexora.artifacts import canonical_hash
from nexora.edge.costs import CostBreakdown, CostModel, compute_costs
from nexora.market_data.models import NormalizedPriceEvent
from nexora.signals import ResearchSignal

Side = Literal["long", "short"]
AmbiguityPolicy = Literal["stop_first", "target_first"]
ExitReason = Literal["stop", "stop_gap", "target", "time"]
ExitStatus = Literal["closed", "open_at_end", "no_entry", "rejected_invalid_plan"]

AMBIGUITY_ASSUMPTION = "same_bar_stop_and_target_order_unknown"


class ExitInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    version: str
    ambiguity: AmbiguityPolicy = "stop_first"
    # Close at the bar close after this many bars (entry bar counts as 1). None = no time stop.
    max_hold_bars: int | None = None
    entry_delay: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if self.max_hold_bars is not None and self.max_hold_bars < 1:
            raise ExitInputError("invalid_max_hold_bars")
        if self.entry_delay < timedelta(0):
            raise ExitInputError("negative_entry_delay")

    def hash_payload(self) -> tuple[str, str, int | None, int]:
        return (
            self.version,
            self.ambiguity,
            self.max_hold_bars,
            int(self.entry_delay.total_seconds()),
        )


@dataclass(frozen=True, slots=True)
class TradePlan:
    signal_id: str
    side: Side
    decision_time: datetime
    stop_price: Decimal
    target_price: Decimal
    source_refs: tuple[str, ...] = ()


def plan_from_signal(signal: ResearchSignal, *, target_name: Literal["TP1", "TP2"]) -> TradePlan:
    """Read stop and target from the signal's own decision. The caller must name the target;
    there is no default because which target the baseline uses is an open Quant decision."""
    decision = signal.decision
    if decision is None or decision.invalidation_price is None:
        raise ExitInputError("signal_missing_invalidation")
    targets = {t.name: t.price for t in decision.targets}
    if target_name not in targets:
        raise ExitInputError("signal_missing_target")
    return TradePlan(
        signal_id=signal.signal_id,
        side=signal.side,
        decision_time=signal.decision_time,
        stop_price=decision.invalidation_price,
        target_price=targets[target_name],
        source_refs=signal.source_refs,
    )


@dataclass(frozen=True, slots=True)
class ExitResult:
    signal_id: str
    side: Side
    status: ExitStatus
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_reason: ExitReason | None = None
    bars_held: int = 0
    r_multiple: Decimal | None = None
    ambiguous: bool = False
    assumptions: tuple[str, ...] = ()
    # Exit prices under each resolution of an ambiguous bar (empty when not ambiguous).
    alternative_exit_prices: tuple[tuple[str, Decimal], ...] = ()
    entry_spread: Decimal | None = None
    source_refs: tuple[str, ...] = ()

    def gross_per_unit(self, price: Decimal | None = None) -> Decimal:
        if self.entry_price is None or (price is None and self.exit_price is None):
            raise ExitInputError("trade_not_closed")
        exit_price = price if price is not None else self.exit_price
        assert exit_price is not None
        move = exit_price - self.entry_price
        return move if self.side == "long" else -move


def _validate_bars(bars: Sequence[NormalizedPriceEvent]) -> None:
    previous: datetime | None = None
    for bar in bars:
        if bar.kind != "bar" or None in (bar.open_price, bar.high, bar.low, bar.close):
            raise ExitInputError("bars_required")
        assert bar.open_price is not None and bar.high is not None
        assert bar.low is not None and bar.close is not None
        values = (bar.open_price, bar.high, bar.low, bar.close)
        if (
            not all(v.is_finite() and v > 0 for v in values)
            or bar.high < max(bar.open_price, bar.close, bar.low)
            or bar.low > min(bar.open_price, bar.close, bar.high)
        ):
            raise ExitInputError("invalid_ohlc")
        if bar.event_time.tzinfo is None or bar.received_at.tzinfo is None:
            raise ExitInputError("timezone_required")
        if previous is not None and bar.event_time <= previous:
            raise ExitInputError("bars_not_strictly_increasing")
        previous = bar.event_time


def _check_plan(plan: TradePlan) -> None:
    for price in (plan.stop_price, plan.target_price):
        if not price.is_finite() or price <= 0:
            raise ExitInputError("invalid_plan_price")
    if plan.decision_time.tzinfo is None:
        raise ExitInputError("timezone_required")


def simulate_exit(
    plan: TradePlan, bars: Sequence[NormalizedPriceEvent], policy: ExitPolicy
) -> ExitResult:
    """Resolve one trade plan against completed bars. Pure and deterministic."""
    _check_plan(plan)
    _validate_bars(bars)
    long = plan.side == "long"
    sign = 1 if long else -1
    target_time = plan.decision_time + policy.entry_delay
    entry_index = next((i for i, b in enumerate(bars) if b.event_time >= target_time), None)
    if entry_index is None:
        return ExitResult(plan.signal_id, plan.side, "no_entry", source_refs=plan.source_refs)

    entry_bar = bars[entry_index]
    entry = entry_bar.open_price
    assert entry is not None
    # Geometry must hold at the actual entry price, otherwise the plan is invalid, not traded.
    if not (
        (long and plan.stop_price < entry < plan.target_price)
        or (not long and plan.target_price < entry < plan.stop_price)
    ):
        return ExitResult(
            plan.signal_id,
            plan.side,
            "rejected_invalid_plan",
            entry_time=entry_bar.event_time,
            entry_price=entry,
            source_refs=plan.source_refs,
        )
    risk = abs(entry - plan.stop_price)
    spread = (
        entry_bar.ask - entry_bar.bid
        if entry_bar.ask is not None and entry_bar.bid is not None
        else None
    )

    def result(
        bar: NormalizedPriceEvent,
        index: int,
        price: Decimal,
        reason: ExitReason,
        *,
        ambiguous: bool = False,
        alternatives: tuple[tuple[str, Decimal], ...] = (),
        extra: tuple[str, ...] = (),
    ) -> ExitResult:
        assumptions = list(extra)
        if ambiguous:
            assumptions.append(f"{AMBIGUITY_ASSUMPTION}:{policy.ambiguity}")
        return ExitResult(
            signal_id=plan.signal_id,
            side=plan.side,
            status="closed",
            entry_time=entry_bar.event_time,
            entry_price=entry,
            exit_time=bar.received_at,
            exit_price=price,
            exit_reason=reason,
            bars_held=index - entry_index + 1,
            r_multiple=(price - entry) * sign / risk,
            ambiguous=ambiguous,
            assumptions=tuple(assumptions),
            alternative_exit_prices=alternatives,
            entry_spread=spread,
            source_refs=(*plan.source_refs, entry_bar.identity_key, bar.identity_key),
        )

    for index in range(entry_index, len(bars)):
        bar = bars[index]
        assert bar.open_price is not None and bar.high is not None
        assert bar.low is not None and bar.close is not None
        stop_hit = bar.low <= plan.stop_price if long else bar.high >= plan.stop_price
        target_hit = bar.high >= plan.target_price if long else bar.low <= plan.target_price
        is_entry_bar = index == entry_index
        if not is_entry_bar:
            # The open precedes every other print in the bar, so a gap resolves the order.
            gapped_stop = (
                bar.open_price <= plan.stop_price if long else bar.open_price >= plan.stop_price
            )
            if gapped_stop:
                return result(
                    bar,
                    index,
                    bar.open_price,
                    "stop_gap",
                    extra=("stop_filled_at_gap_open",),
                )
        if stop_hit and target_hit:
            stop_fill = plan.stop_price
            alternatives = (("stop_first", stop_fill), ("target_first", plan.target_price))
            if policy.ambiguity == "stop_first":
                return result(
                    bar, index, stop_fill, "stop", ambiguous=True, alternatives=alternatives
                )
            return result(
                bar, index, plan.target_price, "target", ambiguous=True, alternatives=alternatives
            )
        if stop_hit:
            return result(bar, index, plan.stop_price, "stop")
        if target_hit:
            # Filled at the target price even if the bar opened beyond it: no price improvement.
            return result(bar, index, plan.target_price, "target")
        if policy.max_hold_bars is not None and index - entry_index + 1 >= policy.max_hold_bars:
            return result(bar, index, bar.close, "time")

    return ExitResult(
        plan.signal_id,
        plan.side,
        "open_at_end",
        entry_time=entry_bar.event_time,
        entry_price=entry,
        bars_held=len(bars) - entry_index,
        entry_spread=spread,
        source_refs=plan.source_refs,
    )


@dataclass(frozen=True, slots=True)
class EdgeTrade:
    exit: ExitResult
    unit_size: Decimal
    gross_pnl: Decimal
    costs: CostBreakdown
    # Gross minus the KNOWN costs only. Equals true net PnL only when `net_complete`.
    net_pnl_known_costs: Decimal
    net_complete: bool
    # Gross and known-cost net under each ambiguous resolution (empty when unambiguous).
    sensitivity: tuple[tuple[str, Decimal, Decimal], ...] = ()


def to_edge_trade(result: ExitResult, cost_model: CostModel, *, unit_size: Decimal) -> EdgeTrade:
    """Apply costs to a closed trade. `unit_size` is a research unit multiplier, not a
    position-sizing rule."""
    if result.status != "closed" or result.exit_time is None or result.entry_time is None:
        raise ExitInputError("trade_not_closed")
    holding_days = Decimal(str((result.exit_time - result.entry_time).total_seconds())) / Decimal(
        86400
    )
    costs = compute_costs(
        cost_model,
        size=unit_size,
        holding_days=holding_days,
        observed_spread=result.entry_spread,
    )
    gross = result.gross_per_unit() * unit_size
    sensitivity = tuple(
        (
            label,
            result.gross_per_unit(price) * unit_size,
            result.gross_per_unit(price) * unit_size - costs.known_total,
        )
        for label, price in result.alternative_exit_prices
    )
    return EdgeTrade(
        exit=result,
        unit_size=unit_size,
        gross_pnl=gross,
        costs=costs,
        net_pnl_known_costs=gross - costs.known_total,
        net_complete=costs.complete,
        sensitivity=sensitivity,
    )


@dataclass(frozen=True, slots=True)
class EdgeExitRun:
    run_id: str
    exit_policy: ExitPolicy
    cost_model: CostModel
    unit_size: Decimal
    dataset_hash: str
    trades: tuple[EdgeTrade, ...]
    unresolved: tuple[ExitResult, ...]
    ambiguous_count: int
    notes: tuple[str, ...]


def run_exit_mode(
    plans: Sequence[TradePlan],
    bars: Sequence[NormalizedPriceEvent],
    *,
    policy: ExitPolicy,
    cost_model: CostModel,
    unit_size: Decimal,
    dataset_hash: str,
) -> EdgeExitRun:
    """Deterministic ledger for the SL/TP exit mode. Plans are processed in decision order."""
    if not unit_size.is_finite() or unit_size <= 0:
        raise ExitInputError("invalid_unit_size")
    ids = [p.signal_id for p in plans]
    if len(set(ids)) != len(ids):
        raise ExitInputError("duplicate_signal_id")
    ordered = sorted(plans, key=lambda p: (p.decision_time, p.signal_id))
    trades: list[EdgeTrade] = []
    unresolved: list[ExitResult] = []
    for plan in ordered:
        outcome = simulate_exit(plan, bars, policy)
        if outcome.status == "closed":
            trades.append(to_edge_trade(outcome, cost_model, unit_size=unit_size))
        else:
            unresolved.append(outcome)
    run_id = "edge-exit-run:" + canonical_hash(
        (
            policy.hash_payload(),
            cost_model,
            unit_size,
            dataset_hash,
            ordered,
            [t.exit for t in trades],
        )
    )
    return EdgeExitRun(
        run_id=run_id,
        exit_policy=policy,
        cost_model=cost_model,
        unit_size=unit_size,
        dataset_hash=dataset_hash,
        trades=tuple(trades),
        unresolved=tuple(unresolved),
        ambiguous_count=sum(1 for t in trades if t.exit.ambiguous),
        notes=(
            "research_only",
            "no_live_execution",
            "not_the_production_exit_rules",
            "legacy_time_delay_exit_unchanged",
            "tp_selection_and_sizing_are_open_quant_decisions",
        ),
    )
