"""Stage B: future outcome labeling of sealed observations (ADR-030 Decision 8).

Labeling may read events after the anchor; it never modifies a sealed observation and
never reaches engine input. Samples follow the EX1 eligibility rule unchanged (E5):
index > k, event_time > t0 and received_at > t0. Excursions use sampled prices only,
so they are lower bounds; there is no intrabar path inference.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from nexora.artifacts import canonical_hash
from nexora.market_data.models import NormalizedPriceEvent
from nexora.validation.models import (
    BoxUnitStatus,
    FirstBarrier,
    LabelStatus,
    ObservationRecord,
    OutcomeDefinition,
    OutcomeRecord,
    OutcomeStatus,
    SealBroken,
)

_REVERSAL_PREFIX = "pnf.transition:reversal:"


def definition_hash(definition: OutcomeDefinition) -> str:
    return canonical_hash(definition)


def seconds_between(start: datetime, end: datetime) -> Decimal:
    return Decimal((end - start) // timedelta(microseconds=1)).scaleb(-6)


def label(
    observations: Sequence[ObservationRecord],
    events: Sequence[NormalizedPriceEvent],
    definitions: Sequence[OutcomeDefinition],
    *,
    pnf_captured: bool,
) -> tuple[OutcomeRecord, ...]:
    reversals = tuple(
        (o.anchor_index, o.subject_group.removeprefix(_REVERSAL_PREFIX))
        for o in observations
        if o.subject_group.startswith(_REVERSAL_PREFIX)
    )
    hashes = tuple((d, definition_hash(d)) for d in definitions)
    outcomes: list[OutcomeRecord] = []
    for observation in observations:
        index = observation.anchor_index
        if not 1 <= index <= len(events) or (
            events[index - 1].identity_key != observation.anchor_event_id
        ):
            raise SealBroken("observation_anchor_mismatch")
        for definition, digest in hashes:
            outcomes.append(
                _label_one(observation, definition, digest, events, reversals, pnf_captured)
            )
    return tuple(outcomes)


def _window(
    observation: ObservationRecord,
    definition: OutcomeDefinition,
    events: Sequence[NormalizedPriceEvent],
) -> tuple[list[int], bool, int | None]:
    """Indices (1-based) inside the window, whether the window is closed, and its end index."""
    k, n = observation.anchor_index, len(events)
    if definition.window_kind == "events":
        end = min(k + definition.window_length, n)
        return (
            list(range(k + 1, end + 1)),
            k + definition.window_length <= n,
            (end if end > k else None),
        )
    due = observation.t0 + timedelta(seconds=definition.window_length)
    inside: list[int] = []
    closed = False
    for j in range(k + 1, n + 1):
        if events[j - 1].event_time > due:
            closed = True
            break
        inside.append(j)
    return inside, closed, (inside[-1] if inside else None)


def _label_one(
    observation: ObservationRecord,
    definition: OutcomeDefinition,
    digest: str,
    events: Sequence[NormalizedPriceEvent],
    reversals: tuple[tuple[int, str], ...],
    pnf_captured: bool,
) -> OutcomeRecord:
    t0 = observation.t0
    inside, closed, window_end = _window(observation, definition, events)
    due = t0 + timedelta(seconds=definition.window_length)
    samples = [
        (j, events[j - 1])
        for j in inside
        if events[j - 1].event_time > t0
        and events[j - 1].received_at > t0
        and (definition.window_kind == "events" or events[j - 1].received_at <= due)
    ]
    gap = any(events[j - 1].is_gap for j in inside)
    status: OutcomeStatus
    if not closed:
        status = "CENSORED_END_OF_DATA"
    elif gap and definition.gap_policy == "label_censored":
        status = "CENSORED_GAP"
    elif not samples:
        status = "NO_SAMPLES"
    else:
        status = "COMPLETE"

    unit_status: BoxUnitStatus
    unit: Decimal | None
    if definition.box_unit == "none":
        unit_status, unit = "not_requested", None
    elif definition.box_unit == "fixed_price_unit":
        unit_status, unit = "available", definition.fixed_price_unit
    else:
        unit = observation.observed_effective_box_size
        unit_status = "available" if unit is not None else "unavailable"

    reference = observation.reference_price
    sign = {"long": Decimal(1), "short": Decimal(-1)}.get(observation.direction)
    prices = [event.price for _, event in samples]
    up = max(Decimal(0), max(prices) - reference) if prices else None
    down = max(Decimal(0), reference - min(prices)) if prices else None
    mfe = mae = time_mfe = time_mae = None
    if sign is not None and samples:
        moves = [sign * (event.price - reference) for _, event in samples]
        best, worst = max(moves), min(moves)
        mfe, mae = max(Decimal(0), best), max(Decimal(0), -worst)
        if mfe > 0:
            time_mfe = seconds_between(t0, samples[moves.index(best)][1].event_time)
        if mae > 0:
            time_mae = seconds_between(t0, samples[moves.index(worst)][1].event_time)
    endpoint_index = samples[-1][0] if samples else None
    endpoint_price = samples[-1][1].price if samples else None
    endpoint_change = None
    if endpoint_price is not None:
        endpoint_change = (sign or Decimal(1)) * (endpoint_price - reference)

    first: FirstBarrier = "not_applicable"
    first_index = first_time = overshoot = None
    fav = _distance(definition.favorable_barrier, definition, unit)
    adv = _distance(definition.adverse_barrier, definition, unit)
    if sign is not None and (fav is not None or adv is not None):
        first = "none"
        for j, event in samples:
            move = sign * (event.price - reference)
            if fav is not None and move >= fav:
                first, overshoot = "favorable", move - fav
            elif adv is not None and -move >= adv:
                first, overshoot = "adverse", -move - adv
            else:
                continue
            first_index, first_time = j, seconds_between(t0, event.event_time)
            break

    invalidation_status: LabelStatus = "not_applicable"
    invalidation_index = None
    stop = observation.invalidation_price
    if sign is not None and stop is not None:
        invalidation_status = "evaluated"
        invalidation_index = next(
            (
                j
                for j, event in samples
                if (event.price <= stop if sign > 0 else event.price >= stop)
            ),
            None,
        )

    reversal_status: LabelStatus = "not_applicable"
    reversal_index = None
    if sign is not None:
        if not pnf_captured:
            reversal_status = "not_captured"
        else:
            reversal_status = "evaluated"
            against = "O" if sign > 0 else "X"
            reversal_index = next(
                (
                    idx
                    for idx, direction in reversals
                    if direction == against
                    and observation.anchor_index < idx
                    and window_end is not None
                    and idx <= window_end
                ),
                None,
            )

    times = [observation.anchor_event_time, *(event.event_time for _, event in samples)]
    max_gap = (
        max(seconds_between(a, b) for a, b in zip(times, times[1:], strict=False))
        if samples
        else None
    )
    return OutcomeRecord(
        schema_version=1,
        outcome_id=canonical_hash((observation.record_hash, digest)),
        observation_hash=observation.record_hash,
        definition_id=definition.definition_id,
        definition_hash=digest,
        subject_group=observation.subject_group,
        direction=observation.direction,
        status=status,
        anchor_index=observation.anchor_index,
        window_end_index=window_end,
        sample_count=len(samples),
        sample_first_index=samples[0][0] if samples else None,
        sample_last_index=endpoint_index,
        gap_observed=gap,
        max_sample_gap_seconds=max_gap,
        reference_price=reference,
        box_unit_status=unit_status,
        box_unit_price=unit,
        up_excursion=up,
        down_excursion=down,
        mfe=mfe,
        mae=mae,
        mfe_boxes=_boxes(mfe, unit),
        mae_boxes=_boxes(mae, unit),
        time_to_mfe_seconds=time_mfe,
        time_to_mae_seconds=time_mae,
        endpoint_index=endpoint_index,
        endpoint_price=endpoint_price,
        endpoint_change=endpoint_change,
        endpoint_change_boxes=_boxes(endpoint_change, unit),
        first_barrier=first,
        first_barrier_index=first_index,
        time_to_first_barrier_seconds=first_time,
        barrier_overshoot=overshoot,
        invalidation_status=invalidation_status,
        invalidation_index=invalidation_index,
        adverse_reversal_status=reversal_status,
        adverse_reversal_index=reversal_index,
        sampled_only=True,
    )


def _distance(
    barrier: Decimal | None, definition: OutcomeDefinition, unit: Decimal | None
) -> Decimal | None:
    if barrier is None:
        return None
    if definition.barrier_unit == "price":
        return barrier
    return barrier * unit if unit is not None else None


def _boxes(value: Decimal | None, unit: Decimal | None) -> Decimal | None:
    return value / unit if value is not None and unit is not None else None
