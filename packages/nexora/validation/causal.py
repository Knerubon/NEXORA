"""Mandatory causal acceptance checks (ADR-030 G3/G4, Rin review 2026-09-25).

1. Prefix invariance: replay(full)[<=c] == replay(events[:c]).
2. Future mutation invariance: changing only events after c never changes an
   observation anchored at or before c.

Either failure invalidates the replay result. Mutations are deterministic, so the
check itself is reproducible; they produce inputs for this check only and never
become validation data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Literal

from nexora.market_data.models import NormalizedPriceEvent, quantize_decimal
from nexora.research import PipelineConfig
from nexora.validation.capture import capture
from nexora.validation.models import (
    BaselineRule,
    CausalReport,
    CutCheck,
    ObservationRecord,
    SubjectKind,
    ValidationInputError,
)

Mutation = Callable[[NormalizedPriceEvent, NormalizedPriceEvent], NormalizedPriceEvent]


def reflect_tail(pivot: NormalizedPriceEvent, event: NormalizedPriceEvent) -> NormalizedPriceEvent:
    """Mirror a later price around the cut price (reverses every later move)."""
    mirrored = pivot.price * 2 - event.price
    return with_price(event, mirrored if mirrored > 0 else pivot.price)


def flatten_tail(pivot: NormalizedPriceEvent, event: NormalizedPriceEvent) -> NormalizedPriceEvent:
    """Hold every later price at the cut price (removes every later move)."""
    return with_price(event, pivot.price)


def shock_tail(pivot: NormalizedPriceEvent, event: NormalizedPriceEvent) -> NormalizedPriceEvent:
    """Push every later price far above the cut price (forces later reversals)."""
    return with_price(event, event.price * 3 + pivot.price)


MUTATIONS: tuple[tuple[str, Mutation], ...] = (
    ("reflect_tail", reflect_tail),
    ("flatten_tail", flatten_tail),
    ("shock_tail", shock_tail),
)


def with_price(event: NormalizedPriceEvent, price: Decimal) -> NormalizedPriceEvent:
    """Shift every quoted price by the same delta, so spreads and ordering stay intact."""
    new = quantize_decimal(price, event.precision)
    delta = new - event.price

    def shifted(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        moved = value + delta
        return moved if moved > 0 else value

    return replace(
        event,
        price=new,
        bid=shifted(event.bid),
        ask=shifted(event.ask),
        last=shifted(event.last),
        open_price=shifted(event.open_price),
        high=shifted(event.high),
        low=shifted(event.low),
        close=shifted(event.close),
    )


def verify_causality(
    events: tuple[NormalizedPriceEvent, ...],
    full: tuple[ObservationRecord, ...],
    *,
    cut_points: tuple[int, ...],
    pipeline_config: PipelineConfig,
    subject_kinds: tuple[SubjectKind, ...],
    baseline: BaselineRule | None,
) -> CausalReport:
    if any(cut >= len(events) for cut in cut_points):
        raise ValidationInputError("causal_cut_point_out_of_range")
    checks: list[CutCheck] = []
    for cut in cut_points:
        expected = tuple(r.record_hash for r in full if r.anchor_index <= cut)
        prefix = capture(
            iter(events[:cut]),
            pipeline_config=pipeline_config,
            subject_kinds=subject_kinds,
            baseline=baseline,
        ).observations
        checks.append(_compare(cut, "prefix_invariance", "truncate", expected, prefix, True))
        pivot = events[cut - 1]
        for name, mutate in MUTATIONS:
            tail = tuple(mutate(pivot, event) for event in events[cut:])
            changed = tail != events[cut:]
            mutated = capture(
                iter((*events[:cut], *tail)),
                pipeline_config=pipeline_config,
                subject_kinds=subject_kinds,
                baseline=baseline,
            ).observations
            observed = tuple(r for r in mutated if r.anchor_index <= cut)
            checks.append(
                _compare(cut, "future_mutation_invariance", name, expected, observed, changed)
            )
    passed = all(check.passed for check in checks)
    return CausalReport(status="PASSED" if passed else "FAILED", checks=tuple(checks))


def _compare(
    cut: int,
    prop: Literal["prefix_invariance", "future_mutation_invariance"],
    mutation: str,
    expected: tuple[str, ...],
    observed: tuple[ObservationRecord, ...],
    changed: bool,
) -> CutCheck:
    actual = tuple(r.record_hash for r in observed)
    mismatch = next(
        (i for i, (a, b) in enumerate(zip(expected, actual, strict=False)) if a != b),
        None,
    )
    if mismatch is None and len(expected) != len(actual):
        mismatch = min(len(expected), len(actual))
    first_index = None
    if mismatch is not None:
        source = observed if mismatch < len(observed) else ()
        first_index = source[mismatch].anchor_index if source else cut
    return CutCheck(
        cut_index=cut,
        property=prop,
        mutation=mutation,
        observations_compared=len(expected),
        passed=mismatch is None,
        first_mismatch_index=first_index,
        mutation_changed_tail=changed,
    )
