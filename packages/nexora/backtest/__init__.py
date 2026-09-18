"""Reproducible backtest and lab contracts."""

from nexora.backtest.fixtures import fixture_config, fixture_dataset_manifest, fixture_signals
from nexora.backtest.hashing import canonical_hash, canonical_serialize
from nexora.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestRun,
    CostPolicy,
    DatasetManifest,
    DatasetPartition,
    ExecutionPolicy,
    RunMode,
    RunStatus,
    SimulatedTrade,
)
from nexora.backtest.repository import BacktestRunStore
from nexora.backtest.runner import BacktestInputError, BacktestRunner
from nexora.backtest.service import BacktestLabService

__all__ = [
    "BacktestConfig",
    "BacktestInputError",
    "BacktestLabService",
    "BacktestMetrics",
    "BacktestRun",
    "BacktestRunStore",
    "BacktestRunner",
    "CostPolicy",
    "DatasetManifest",
    "DatasetPartition",
    "ExecutionPolicy",
    "RunMode",
    "RunStatus",
    "SimulatedTrade",
    "canonical_hash",
    "canonical_serialize",
    "fixture_config",
    "fixture_dataset_manifest",
    "fixture_signals",
]
