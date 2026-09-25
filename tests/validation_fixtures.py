"""Deterministic tick fixtures for replay validation tests (ADR-030).

Outcome definitions here are test fixtures only, never defaults (Q-V2–Q-V5 open).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from nexora.adaptive_box import AdaptiveBoxConfig
from nexora.artifacts import canonical_hash
from nexora.backtest.datasets import manifest_for
from nexora.backtest.models import DatasetManifest
from nexora.market_data.models import NormalizedPriceEvent
from nexora.market_regime import RegimeConfig
from nexora.pnf import PnfConfig
from nexora.research import PipelineConfig, ResolutionConfig
from nexora.signals import SignalConfig
from nexora.validation import (
    BaselineRule,
    OutcomeDefinition,
    SourceRevision,
    ValidationResult,
    ValidationSpec,
    run_validation,
)
from nexora.validation.models import SUBJECT_KINDS

START = datetime(2026, 3, 2, 9, tzinfo=UTC)
LATENCY = timedelta(milliseconds=150)
CLEAN_REVISION = SourceRevision(commit="a" * 40, dirty=False)


def pipeline_config() -> PipelineConfig:
    return PipelineConfig(
        version="valid-test-v1",
        resolutions=tuple(
            ResolutionConfig(
                name,
                PnfConfig("XAUUSD", D(size), 2, 1, "bid", f"{name}-v1"),
                AdaptiveBoxConfig(
                    "fixed",
                    D(size),
                    1,
                    f"{name}-v1",
                    atr_period=2,
                    min_box_size=D("0.1"),
                    max_box_size=D("5"),
                ),
            )
            for name, size in (("fast", "0.5"), ("medium", "1"), ("slow", "2"))
        ),
        structure_resolution="fast",
        # Test-only trend regime width so the fixture produces BUY/SELL decisions.
        regime=RegimeConfig("XAUUSD", 4, D("2"), D("20"), D("0.5"), "regime-v1"),
        signals=SignalConfig("XAUUSD", 1, 20, "signal-v1"),
        stale_after_events=100,
    )


def tick(
    index: int,
    price: D | str,
    *,
    seconds: float | None = None,
    latency: timedelta = LATENCY,
    is_gap: bool = False,
) -> NormalizedPriceEvent:
    value = D(price)
    at = START + timedelta(seconds=20 * index if seconds is None else seconds)
    return NormalizedPriceEvent(
        1,
        f"t:{index}",
        "synthetic-test",
        "XAUUSD",
        "tick",
        at,
        at + latency,
        index,
        index,
        f"t:{index}",
        "bid",
        "USD/oz",
        1,
        value,
        bid=value,
        ask=value + D("0.3"),
        is_gap=is_gap,
    )


def tick_events(count: int) -> tuple[NormalizedPriceEvent, ...]:
    """Zig-zag trend flipping every 40 ticks: reversals, pivots, patterns, lines, BUY/SELL."""
    pattern = (100, 104, 99, 106, 98, 108, 102, 110, 104, 112)
    events = []
    for i in range(1, count + 1):
        price = D(pattern[(i - 1) % 10] + 3 * ((i - 1) // 10)) + D(i % 7) / 10
        if (i // 40) % 2:
            price = D(300) - price
        events.append(tick(i, price))
    return tuple(events)


def dataset_for(events: tuple[NormalizedPriceEvent, ...]) -> DatasetManifest:
    return manifest_for(events, quality="complete")


def time_definition(seconds: int = 300) -> OutcomeDefinition:
    return OutcomeDefinition(
        definition_id=f"fixture-time-{seconds}s",
        version="fixture-v1",
        window_kind="time",
        window_length=seconds,
        reference="anchor_price",
        box_unit="observed_effective_box_size",
        gap_policy="label_censored",
        barrier_unit="boxes",
        favorable_barrier=D(3),
        adverse_barrier=D(3),
    )


def events_definition(count: int = 10) -> OutcomeDefinition:
    return OutcomeDefinition(
        definition_id=f"fixture-events-{count}",
        version="fixture-v1",
        window_kind="events",
        window_length=count,
        reference="anchor_price",
        box_unit="none",
        gap_policy="measure_through",
    )


def spec(cuts: tuple[int, ...] = (20, 50), *, baseline: bool = True) -> ValidationSpec:
    kinds = (
        SUBJECT_KINDS
        if baseline
        else tuple(k for k in SUBJECT_KINDS if k != "baseline.every_nth_event")
    )
    return ValidationSpec(
        subject_kinds=kinds,
        outcome_definitions=(time_definition(), events_definition()),
        causal_cut_points=cuts,
        baseline=BaselineRule(every_n_events=10) if baseline else None,
    )


def run(
    events: tuple[NormalizedPriceEvent, ...],
    validation_spec: ValidationSpec | None = None,
    *,
    revision: SourceRevision = CLEAN_REVISION,
    pipeline: PipelineConfig | None = None,
) -> ValidationResult:
    dataset = dataset_for(events)
    return run_validation(
        dataset=dataset,
        events=events,
        expected_dataset_hash=canonical_hash(dataset),
        pipeline=pipeline or pipeline_config(),
        spec=validation_spec or spec(),
        source_revision=revision,
    )
