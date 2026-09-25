"""Stage C: descriptive aggregation only (ADR-030 Decision 9).

Counts, denominators and nearest-rank distribution summaries. No rates, intervals,
tests, scores, rankings or probabilities: those methods are open Quant decisions
(Q-V5) and are never chosen here. Every sealed record is re-verified first (G2).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from decimal import ROUND_CEILING, Decimal

from nexora.validation.labeling import definition_hash
from nexora.validation.models import (
    BaselineRule,
    CountItem,
    DistributionSummary,
    GroupMetrics,
    MetricsReport,
    ObservationRecord,
    OutcomeDefinition,
    OutcomeRecord,
    SealBroken,
)
from nexora.validation.sealing import verify_seals

BASELINE_GROUP = "baseline.every_nth_event"
DISTRIBUTION_FIELDS = (
    "mfe",
    "mae",
    "mfe_boxes",
    "mae_boxes",
    "up_excursion",
    "down_excursion",
    "endpoint_change",
    "endpoint_change_boxes",
    "time_to_mfe_seconds",
    "time_to_mae_seconds",
)
NOTES = (
    "descriptive_only_no_rates_scores_or_probabilities",
    "sampled_prices_only_excursions_are_lower_bounds",
    "distributions_use_complete_outcomes_only",
    "censored_outcomes_counted_never_dropped",
    "quant_decisions_q_v2_to_q_v5_open",
)


def summarize(
    observations: Sequence[ObservationRecord],
    outcomes: Sequence[OutcomeRecord],
    definitions: Sequence[OutcomeDefinition],
    *,
    baseline: BaselineRule | None,
) -> MetricsReport:
    verify_seals(observations)
    known = {o.record_hash for o in observations}
    hashes = {d.definition_id: definition_hash(d) for d in definitions}
    grouped: dict[tuple[str, str], list[OutcomeRecord]] = defaultdict(list)
    for outcome in outcomes:
        if (
            outcome.observation_hash not in known
            or hashes.get(outcome.definition_id) != outcome.definition_hash
        ):
            raise SealBroken("outcome_reference_broken")
        grouped[(outcome.subject_group, outcome.definition_id)].append(outcome)
    groups = tuple(
        _group(group, definition_id, hashes[definition_id], rows, baseline is not None)
        for (group, definition_id), rows in sorted(grouped.items())
    )
    return MetricsReport(
        schema_version=1,
        quantile_method="nearest_rank",
        baseline_status="configured" if baseline is not None else "not_configured",
        groups=groups,
        groups_evaluated=len(groups),
        notes=NOTES,
    )


def nearest_rank(ordered: Sequence[Decimal], fraction: Decimal) -> Decimal:
    rank = int((fraction * len(ordered)).to_integral_value(rounding=ROUND_CEILING))
    return ordered[min(max(rank, 1), len(ordered)) - 1]


def _group(
    group: str,
    definition_id: str,
    digest: str,
    rows: list[OutcomeRecord],
    baseline_configured: bool,
) -> GroupMetrics:
    complete = [r for r in rows if r.status == "COMPLETE"]
    distributions = []
    for name in DISTRIBUTION_FIELDS:
        values = sorted(v for r in complete if (v := getattr(r, name)) is not None)
        if values:
            distributions.append(
                DistributionSummary(
                    field=name,
                    count=len(values),
                    minimum=values[0],
                    p25=nearest_rank(values, Decimal("0.25")),
                    median=nearest_rank(values, Decimal("0.5")),
                    p75=nearest_rank(values, Decimal("0.75")),
                    maximum=values[-1],
                    mean=sum(values, Decimal(0)) / len(values),
                )
            )
    windows = sorted(
        (r.anchor_index, r.window_end_index) for r in complete if r.window_end_index is not None
    )
    overlapping, concurrent = _overlap(windows)
    invalidation = [r for r in complete if r.invalidation_status == "evaluated"]
    reversal = [r for r in complete if r.adverse_reversal_status == "evaluated"]
    baseline_group = None
    if baseline_configured and group != BASELINE_GROUP:
        baseline_group = BASELINE_GROUP
    return GroupMetrics(
        subject_group=group,
        definition_id=definition_id,
        definition_hash=digest,
        n_observations=len(rows),
        status_counts=_counts(r.status for r in rows),
        complete_count=len(complete),
        distributions=tuple(distributions),
        first_barrier_counts=_counts(r.first_barrier for r in complete),
        invalidation_evaluated=len(invalidation),
        invalidation_hits=sum(r.invalidation_index is not None for r in invalidation),
        adverse_reversal_evaluated=len(reversal),
        adverse_reversals=sum(r.adverse_reversal_index is not None for r in reversal),
        overlapping_pairs=overlapping,
        max_concurrent_windows=concurrent,
        baseline_group=baseline_group,
    )


def _counts(values: Iterable[str]) -> tuple[CountItem, ...]:
    counter = Counter(values)
    return tuple(CountItem(key, count) for key, count in sorted(counter.items()))


def _overlap(windows: list[tuple[int, int]]) -> tuple[int, int]:
    """Pairs of index windows [anchor, end] that intersect, and peak concurrency."""
    pairs = concurrent = 0
    open_ends: list[int] = []
    for start, end in windows:
        open_ends = [e for e in open_ends if e >= start]
        pairs += len(open_ends)
        open_ends.append(end)
        concurrent = max(concurrent, len(open_ends))
    return pairs, concurrent
