"""Deterministic fixtures for backtest contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nexora.backtest.models import (
    BacktestConfig,
    CostPolicy,
    DatasetManifest,
    DatasetPartition,
    ExecutionPolicy,
    RunMode,
)
from nexora.signals import ResearchSignal


def fixture_dataset_manifest() -> DatasetManifest:
    return DatasetManifest(
        dataset_id="dataset-xauusd-2026-03",
        parent_dataset_id=None,
        source="MT5",
        symbol="XAUUSD",
        price_source="ask",
        units="USD/oz",
        timezone="UTC",
        range_start=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        range_end=datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
        schema_version=1,
        normalizer_version="p2-v1",
        order_policy="event_time,received_at,source_sequence,source_order,source_event_id,identity_key",
        quality_status="complete",
        quality_snapshot_ref="dq1:sequence:22",
        partitions=(
            DatasetPartition(name="raw", content_hash="raw-hash-v1", rows=1200),
            DatasetPartition(name="normalized", content_hash="norm-hash-v1", rows=1100),
        ),
    )


def fixture_config(mode: RunMode) -> BacktestConfig:
    return BacktestConfig(
        mode=mode,
        engine_version="p10-v1",
        signal_version="p8-signal-v1",
        split="eval",
        seed=7,
        cost_policy=CostPolicy(
            spread=Decimal("0.10"),
            commission=Decimal("0.05"),
            slippage=Decimal("0.02"),
            currency="USD",
            version="cost-v1",
        ),
        execution_policy=ExecutionPolicy(
            entry_delay_seconds=2,
            exit_delay_seconds=5,
            sizing_assumption=Decimal("1.0"),
            stop_distance=Decimal("1.5"),
            version="exec-v1",
        ),
    )


def fixture_signals() -> tuple[ResearchSignal, ...]:
    first = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    return (
        ResearchSignal(
            signal_id="xau:1:long",
            symbol="XAUUSD",
            side="long",
            sequence=1,
            occurrence_time=first,
            confirmation_time=first,
            decision_time=first,
            reasons=("trend aligned",),
            reason_codes=("trend",),
            source_refs=("pivot-1", "regime-1"),
            config_version="p8-signal-v1",
            engine_versions=("p5-v1", "p7-v1"),
            status="active",
        ),
        ResearchSignal(
            signal_id="xau:2:short",
            symbol="XAUUSD",
            side="short",
            sequence=2,
            occurrence_time=first.replace(minute=5),
            confirmation_time=first.replace(minute=5),
            decision_time=first.replace(minute=5),
            reasons=("volatility mixed",),
            reason_codes=("volatility",),
            source_refs=("pivot-2", "regime-2"),
            config_version="p8-signal-v1",
            engine_versions=("p5-v1", "p7-v1"),
            status="active",
        ),
    )
