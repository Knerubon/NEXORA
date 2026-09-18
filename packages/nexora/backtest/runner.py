"""Deterministic research backtest runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexora.backtest.hashing import canonical_hash
from nexora.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestRun,
    DatasetManifest,
    SimulatedTrade,
)
from nexora.signals import ResearchSignal


class BacktestInputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class BacktestRunner:
    """Computes reproducible runs from signal artifacts."""

    def run(
        self,
        *,
        dataset: DatasetManifest,
        config: BacktestConfig,
        expected_dataset_hash: str,
        signals: tuple[ResearchSignal, ...],
        started_at: datetime | None = None,
    ) -> BacktestRun:
        dataset_hash = canonical_hash(dataset)
        if expected_dataset_hash != dataset_hash:
            raise BacktestInputError("dataset_hash_mismatch")
        if dataset.quality_status == "unknown":
            raise BacktestInputError("dataset_quality_unknown")
        if config.execution_policy.sizing_assumption <= 0:
            raise BacktestInputError("invalid_sizing_assumption")
        if config.execution_policy.stop_distance <= 0:
            raise BacktestInputError("invalid_stop_distance")

        build_started = (started_at or datetime.now(UTC)).astimezone(UTC)
        trades = tuple(self._to_trade(signal, config) for signal in signals)
        metrics = _compute_metrics(trades)
        config_hash = canonical_hash(config)
        assumptions_hash = canonical_hash((config.cost_policy, config.execution_policy))
        run_id = f"{dataset.dataset_id}:{config.mode}:{config_hash[:12]}"
        return BacktestRun(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            status="success",
            mode=config.mode,
            started_at=build_started,
            completed_at=build_started,
            config_hash=config_hash,
            dataset_hash=dataset_hash,
            assumptions_hash=assumptions_hash,
            environment="python-3.13",
            signals=signals,
            trades=trades,
            metrics=metrics,
            notes=(
                "research_only",
                "no_live_execution",
                f"split={config.split}",
            ),
        )

    @staticmethod
    def _to_trade(signal: ResearchSignal, config: BacktestConfig) -> SimulatedTrade:
        delay = max(config.execution_policy.entry_delay_seconds, 0)
        hold = max(config.execution_policy.exit_delay_seconds, 1)
        entry_time = signal.decision_time + timedelta(seconds=delay)
        exit_time = entry_time + timedelta(seconds=hold)
        entry_price = Decimal("100.0")
        drift = Decimal("0.8") if signal.side == "long" else Decimal("-0.8")
        exit_price = entry_price + drift
        gross = (exit_price - entry_price) * config.execution_policy.sizing_assumption
        if signal.side == "short":
            gross = -gross
        fees = (
            config.cost_policy.spread
            + config.cost_policy.commission
            + config.cost_policy.slippage
        ) * config.execution_policy.sizing_assumption
        net = gross - fees
        return SimulatedTrade(
            signal_id=signal.signal_id,
            side=signal.side,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=entry_price,
            exit_price=exit_price,
            fees=fees,
            gross_pnl=gross,
            net_pnl=net,
            hold_seconds=hold,
            source_refs=signal.source_refs,
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
    avg_delay = sum((Decimal(trade.hold_seconds) for trade in trades), Decimal("0")) / Decimal(
        len(trades)
    )
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
