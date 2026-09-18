"""Backtest and reproducibility contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexora.signals import ResearchSignal

RunMode = Literal["baseline", "fixed_pnf", "adaptive_pnf"]
RunStatus = Literal["success", "failed", "partial"]


@dataclass(frozen=True, slots=True)
class DatasetPartition:
    name: str
    content_hash: str
    rows: int


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_id: str
    parent_dataset_id: str | None
    source: str
    symbol: str
    price_source: str
    units: str
    timezone: str
    range_start: datetime
    range_end: datetime
    schema_version: int
    normalizer_version: str
    order_policy: str
    quality_status: str
    quality_snapshot_ref: str
    partitions: tuple[DatasetPartition, ...]


@dataclass(frozen=True, slots=True)
class CostPolicy:
    spread: Decimal
    commission: Decimal
    slippage: Decimal
    currency: str
    version: str


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    entry_delay_seconds: int
    exit_delay_seconds: int
    sizing_assumption: Decimal
    stop_distance: Decimal
    version: str


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    mode: RunMode
    engine_version: str
    signal_version: str
    split: Literal["train", "eval"]
    seed: int | None
    cost_policy: CostPolicy
    execution_policy: ExecutionPolicy


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    signal_id: str
    side: Literal["long", "short"]
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    fees: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    hold_seconds: int
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    trade_count: int
    win_rate: Decimal
    expectancy: Decimal
    profit_factor: Decimal | None
    max_drawdown: Decimal
    false_entry_proxy: Decimal
    average_entry_delay_seconds: Decimal


@dataclass(frozen=True, slots=True)
class BacktestRun:
    run_id: str
    dataset_id: str
    status: RunStatus
    mode: RunMode
    started_at: datetime
    completed_at: datetime
    config_hash: str
    dataset_hash: str
    assumptions_hash: str
    environment: str
    signals: tuple[ResearchSignal, ...]
    trades: tuple[SimulatedTrade, ...]
    metrics: BacktestMetrics
    notes: tuple[str, ...]
