"""Deterministic fixtures for backtest contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexora.backtest.datasets import manifest_for
from nexora.backtest.models import (
    BacktestConfig,
    CostPolicy,
    DatasetManifest,
    ExecutionPolicy,
    RunMode,
)
from nexora.market_data.models import NormalizedPriceEvent
from nexora.signals import ResearchSignal


def fixture_events() -> tuple[NormalizedPriceEvent, ...]:
    start = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    return tuple(
        NormalizedPriceEvent(
            schema_version=1,
            identity_key=f"test:{i}",
            source="synthetic-test",
            symbol="XAUUSD",
            kind="tick",
            event_time=start + timedelta(seconds=seconds),
            received_at=start + timedelta(seconds=seconds),
            source_sequence=i,
            source_order=i,
            source_event_id=f"test:{i}",
            price_source="ask",
            units="USD/oz",
            precision=1,
            price=Decimal(price),
            ask=Decimal(price),
        )
        for i, (seconds, price) in enumerate(
            ((0, "100"), (2, "101"), (7, "103"), (300, "106"), (302, "105"), (307, "102")), 1
        )
    )


def fixture_dataset_manifest() -> DatasetManifest:
    return manifest_for(fixture_events(), quality="complete")


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
