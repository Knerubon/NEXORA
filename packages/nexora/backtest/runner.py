"""Causal market-event backtesting; never invent execution prices."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from nexora.artifacts import canonical_hash
from nexora.backtest.datasets import verify_events
from nexora.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestRun,
    DatasetManifest,
    SimulatedTrade,
)
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import ResearchPipeline
from nexora.signals import ResearchSignal


class BacktestInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class BacktestRunner:
    def run(
        self,
        *,
        dataset: DatasetManifest,
        config: BacktestConfig,
        expected_dataset_hash: str,
        events: tuple[NormalizedPriceEvent, ...] = (),
        signals: tuple[ResearchSignal, ...] | None = None,
        started_at: datetime | None = None,
    ) -> BacktestRun:
        dataset_hash = canonical_hash(dataset)
        if expected_dataset_hash != dataset_hash:
            raise BacktestInputError("dataset_hash_mismatch")
        if dataset.quality_status not in {"complete", "partial"}:
            raise BacktestInputError("dataset_quality_unknown")
        try:
            verify_events(dataset, events)
        except ValueError as exc:
            raise BacktestInputError(str(exc)) from exc
        policy = config.execution_policy
        if (
            not policy.sizing_assumption.is_finite()
            or policy.sizing_assumption <= 0
            or not policy.stop_distance.is_finite()
            or policy.stop_distance <= 0
            or policy.entry_delay_seconds < 0
            or policy.exit_delay_seconds < 1
        ):
            raise BacktestInputError("invalid_execution_policy")
        if any(
            not cost.is_finite() or cost < 0
            for cost in (
                config.cost_policy.spread,
                config.cost_policy.commission,
                config.cost_policy.slippage,
            )
        ):
            raise BacktestInputError("invalid_cost_policy")
        research = signals if signals is not None else self.generate_signals(events, config)
        seen: set[str] = set()
        for signal in research:
            if (
                signal.signal_id in seen
                or signal.symbol != dataset.symbol
                or signal.side not in {"long", "short"}
                or signal.occurrence_time > signal.confirmation_time
                or signal.confirmation_time > signal.decision_time
                or not dataset.range_start <= signal.decision_time <= dataset.range_end
            ):
                raise BacktestInputError("invalid_signal_provenance")
            seen.add(signal.signal_id)
        research = tuple(sorted(research, key=lambda s: (s.decision_time, s.signal_id)))
        trades = tuple(
            trade
            for signal in research
            if (trade := self._to_trade(signal, config, events)) is not None
        )
        config_hash = canonical_hash(config)
        run_id = "run:" + canonical_hash(("research-execution-v2", dataset, config, research))
        build_started = started_at or dataset.range_end
        return BacktestRun(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            status="partial" if len(trades) != len(research) else "success",
            mode=config.mode,
            started_at=build_started,
            completed_at=build_started,
            config_hash=config_hash,
            dataset_hash=dataset_hash,
            assumptions_hash=canonical_hash((config.cost_policy, policy)),
            environment="python-3.13",
            signals=research,
            trades=trades,
            metrics=_compute_metrics(trades),
            notes=(
                "research_only",
                "no_live_execution",
                "actual_event_prices",
                "implementation=research-execution-v2",
                f"split={config.split}",
                f"unfilled={len(research) - len(trades)}",
                "provided_signals" if signals is not None else "shared_pipeline",
            ),
        )

    @staticmethod
    def generate_signals(
        events: tuple[NormalizedPriceEvent, ...], config: BacktestConfig
    ) -> tuple[ResearchSignal, ...]:
        if config.mode == "baseline":
            if config.baseline_lookback < 1 or any(
                e.kind != "bar" or e.price_source != "close" or e.close != e.price for e in events
            ):
                raise BacktestInputError("baseline_requires_completed_bars")
            result = []
            for index in range(config.baseline_lookback, len(events)):
                event, past = events[index], events[index - config.baseline_lookback]
                if event.price == past.price:
                    continue
                side: Literal["long", "short"] = "long" if event.price > past.price else "short"
                result.append(
                    ResearchSignal(
                        signal_id=f"baseline:{event.identity_key}:{side}",
                        symbol=event.symbol,
                        side=side,
                        sequence=index,
                        occurrence_time=event.event_time,
                        confirmation_time=event.received_at,
                        decision_time=event.received_at,
                        reasons=("Completed-bar close momentum",),
                        reason_codes=("close_momentum",),
                        source_refs=(past.identity_key, event.identity_key),
                        config_version=config.signal_version,
                        engine_versions=(config.engine_version,),
                        status="active",
                    )
                )
            return tuple(result)
        if config.pipeline is None:
            raise BacktestInputError("pipeline_config_required")
        mode: Literal["fixed", "atr"] = "fixed" if config.mode == "fixed_pnf" else "atr"
        pipeline_config = replace(
            config.pipeline,
            resolutions=tuple(
                replace(r, sizing=replace(r.sizing, mode=mode)) for r in config.pipeline.resolutions
            ),
        )
        engine = ResearchPipeline(pipeline_config)
        issued: dict[str, ResearchSignal] = {}
        for event in events:
            engine.process(event)
            # Preserve the decision-time artifact; later expiry must not rewrite it.
            for signal in engine.research_signals():
                if signal.status == "active" and signal.decision_time == event.received_at:
                    issued.setdefault(signal.signal_id, signal)
        return tuple(issued.values())

    @staticmethod
    def _to_trade(
        signal: ResearchSignal, config: BacktestConfig, events: tuple[NormalizedPriceEvent, ...]
    ) -> SimulatedTrade | None:
        target = signal.decision_time + timedelta(
            seconds=config.execution_policy.entry_delay_seconds
        )
        entry = next((e for e in events if e.event_time >= target), None)
        if entry is None:
            return None
        exit_target = entry.event_time + timedelta(
            seconds=config.execution_policy.exit_delay_seconds
        )
        exit_event = next((e for e in events if e.event_time >= exit_target), None)
        if exit_event is None:
            return None
        gross = (exit_event.price - entry.price) * config.execution_policy.sizing_assumption
        if signal.side == "short":
            gross = -gross
        fees = (
            config.cost_policy.spread + config.cost_policy.commission + config.cost_policy.slippage
        ) * config.execution_policy.sizing_assumption
        return SimulatedTrade(
            signal_id=signal.signal_id,
            side=signal.side,
            entry_time=entry.event_time,
            exit_time=exit_event.event_time,
            entry_price=entry.price,
            exit_price=exit_event.price,
            fees=fees,
            gross_pnl=gross,
            net_pnl=gross - fees,
            hold_seconds=int((exit_event.event_time - entry.event_time).total_seconds()),
            source_refs=(*signal.source_refs, entry.identity_key, exit_event.identity_key),
            decision_time=signal.decision_time,
        )


def _compute_metrics(trades: tuple[SimulatedTrade, ...]) -> BacktestMetrics:
    if not trades:
        return BacktestMetrics(
            trade_count=0,
            win_rate=Decimal("0"),
            expectancy=Decimal("0"),
            profit_factor=None,
            max_drawdown=Decimal("0"),
            false_entry_proxy=Decimal("0"),
            average_entry_delay_seconds=Decimal("0"),
        )
    wins = tuple(trade for trade in trades if trade.net_pnl > 0)
    losses = tuple(trade for trade in trades if trade.net_pnl < 0)
    total_net = sum((trade.net_pnl for trade in trades), Decimal("0"))
    total_loss = sum((-trade.net_pnl for trade in losses), Decimal("0"))
    total_win = sum((trade.net_pnl for trade in wins), Decimal("0"))
    win_rate = Decimal(len(wins)) / Decimal(len(trades))
    expectancy = total_net / Decimal(len(trades))
    profit_factor = None if total_loss == 0 else total_win / total_loss
    false_entry_proxy = Decimal(len(losses)) / Decimal(len(trades))
    avg_delay = sum(
        (
            Decimal(
                str((trade.entry_time - (trade.decision_time or trade.entry_time)).total_seconds())
            )
            for trade in trades
        ),
        Decimal("0"),
    ) / Decimal(len(trades))
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for trade in trades:
        equity += trade.net_pnl
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return BacktestMetrics(
        trade_count=len(trades),
        win_rate=win_rate,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        false_entry_proxy=false_entry_proxy,
        average_entry_delay_seconds=avg_delay,
    )
