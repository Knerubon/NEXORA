"""Investigation helper for the broker-clock offset. It MEASURES and REPORTS; it never applies.

Stored historical ticks carry only the broker clock, so they cannot reveal their own offset.
Evidence must come from paired observations taken while connected: (raw broker time_msc,
independent UTC reference time at capture). Capture latency adds positive noise, so differences
are snapped to a caller-chosen grid before comparison. Whether an offset is constant across DST
changes or effective-date boundaries is exactly what this report surfaces; it does not decide.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from typing import Any

from nexora.market_history.models import HistoryError


@dataclass(frozen=True, slots=True)
class OffsetSample:
    raw_msc: int
    reference_utc_msc: int


def summarize_offsets(
    samples: list[OffsetSample], *, snap_seconds: int, min_samples_per_day: int
) -> dict[str, Any]:
    if snap_seconds <= 0 or min_samples_per_day <= 0:
        raise HistoryError("invalid_offset_analysis_parameters")
    by_day: dict[str, list[float]] = defaultdict(list)
    for s in samples:
        day = datetime.fromtimestamp(s.reference_utc_msc // 1000, tz=UTC).strftime("%Y-%m-%d")
        by_day[day].append((s.raw_msc - s.reference_utc_msc) / 1000.0)
    days: dict[str, Any] = {}
    snapped_values: set[int] = set()
    for day, diffs in sorted(by_day.items()):
        if len(diffs) < min_samples_per_day:
            days[day] = {"samples": len(diffs), "status": "insufficient_samples"}
            continue
        snapped = round(median(diffs) / snap_seconds) * snap_seconds
        snapped_values.add(snapped)
        days[day] = {
            "samples": len(diffs),
            "min_s": min(diffs),
            "median_s": median(diffs),
            "max_s": max(diffs),
            "snapped_offset_s": snapped,
        }
    if not snapped_values:
        verdict = "insufficient_samples"
    elif len(snapped_values) == 1:
        verdict = "single_offset_observed_in_sample_window_only"
    else:
        verdict = "offset_varies_investigate_dst_or_effective_dates"
    return {
        "snap_seconds": snap_seconds,
        "per_utc_day": days,
        "distinct_snapped_offsets_s": sorted(snapped_values),
        "verdict": verdict,
        "applied": False,
        "caveat": (
            "Observed only for the sampled days. Not evidence for other dates, other symbols or "
            "other servers; a declared TimeConversion needs a documented evidence_ref."
        ),
    }
