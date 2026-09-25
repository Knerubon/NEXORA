"""ADR-014 verified dataset input boundary; every doubt fails closed (ADR-030 Decision 3a)."""

from __future__ import annotations

from pathlib import Path

from nexora.artifacts import canonical_hash
from nexora.backtest.datasets import load_dataset, verify_events
from nexora.backtest.models import DatasetManifest
from nexora.market_data.models import NormalizedPriceEvent
from nexora.research import PipelineConfig
from nexora.validation.models import ValidationInputError

ACCEPTED_QUALITY = frozenset({"complete", "partial"})


def verify_input(
    dataset: DatasetManifest,
    events: tuple[NormalizedPriceEvent, ...],
    *,
    expected_dataset_hash: str,
    pipeline: PipelineConfig,
) -> None:
    if canonical_hash(dataset) != expected_dataset_hash:
        raise ValidationInputError("dataset_hash_mismatch")
    if dataset.quality_status not in ACCEPTED_QUALITY:
        raise ValidationInputError("dataset_quality_unknown")
    try:
        verify_events(dataset, events)
    except ValueError as exc:
        raise ValidationInputError(str(exc)) from None
    # A bar's close has no defined knowledge time until a bar contract exists.
    if any(event.kind != "tick" for event in events):
        raise ValidationInputError("bar_knowledge_time_undefined")
    first = pipeline.resolutions[0].pnf
    if dataset.symbol != pipeline.signals.symbol:
        raise ValidationInputError("pipeline_symbol_mismatch")
    if dataset.price_source != first.price_source:
        raise ValidationInputError("pipeline_price_source_mismatch")
    if any(
        event.precision != resolution.pnf.price_precision
        for event in events
        for resolution in pipeline.resolutions
    ):
        raise ValidationInputError("pipeline_precision_mismatch")


def load_input(
    directory: Path, *, expected_dataset_hash: str, pipeline: PipelineConfig
) -> tuple[DatasetManifest, tuple[NormalizedPriceEvent, ...]]:
    """Load a stored dataset read-only; nothing under `directory` is written."""
    try:
        dataset, events = load_dataset(directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else "dataset_unreadable"
        raise ValidationInputError(code or "dataset_unreadable") from None
    verify_input(dataset, events, expected_dataset_hash=expected_dataset_hash, pipeline=pipeline)
    return dataset, events
