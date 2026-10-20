"""Chronological coverage and tick-quality report.

Holdout custody: the report carries counts, timestamps and coverage only. It contains no price
or spread distributions, so producing it for a locked window does not reveal market behaviour.
Thresholds are caller-supplied; none are invented here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexora.market_history.models import HistoryError, NormalizedTick

QUALITY_VERSION = "nexora.tick-quality/1"
MAX_GAP_LIST = 200


@dataclass(frozen=True, slots=True)
class QualityConfig:
    """`max_gap_ms` has no default: what counts as a data gap is a Quant decision (OPEN)."""

    max_gap_ms: int

    def __post_init__(self) -> None:
        if self.max_gap_ms <= 0:
            raise HistoryError("invalid_max_gap")


def _day(utc_msc: int) -> str:
    return datetime.fromtimestamp(utc_msc // 1000, tz=UTC).strftime("%Y-%m-%d")


def quality_report(ticks: Iterable[NormalizedTick], config: QualityConfig) -> dict[str, Any]:
    total = non_monotonic = same_ts_diff_quote = exact_dups = 0
    crossed = nonpositive = zero_spread = 0
    first: int | None = None
    last: int | None = None
    prev: NormalizedTick | None = None
    gaps: list[list[int]] = []
    gap_count = 0
    per_day: dict[str, int] = {}
    for t in ticks:
        total += 1
        first = t.utc_msc if first is None else min(first, t.utc_msc)
        last = t.utc_msc if last is None else max(last, t.utc_msc)
        d = _day(t.utc_msc)
        per_day[d] = per_day.get(d, 0) + 1
        if t.bid <= 0 or t.ask <= 0:
            nonpositive += 1
        if t.ask < t.bid:
            crossed += 1
        if t.ask == t.bid:
            zero_spread += 1
        if prev is not None:
            if t.utc_msc < prev.utc_msc:
                non_monotonic += 1
            elif t.utc_msc == prev.utc_msc:
                if (t.bid, t.ask) == (prev.bid, prev.ask):
                    exact_dups += 1
                else:
                    same_ts_diff_quote += 1
            elif t.utc_msc - prev.utc_msc > config.max_gap_ms:
                gap_count += 1
                if len(gaps) < MAX_GAP_LIST:
                    gaps.append([prev.utc_msc, t.utc_msc])
        prev = t
    return {
        "version": QUALITY_VERSION,
        "config": {"max_gap_ms": config.max_gap_ms},
        "tick_count": total,
        "first_utc_msc": first,
        "last_utc_msc": last,
        "non_monotonic_count": non_monotonic,
        "exact_duplicate_count": exact_dups,
        "same_timestamp_different_quote_count": same_ts_diff_quote,
        "crossed_quote_count": crossed,
        "nonpositive_price_count": nonpositive,
        "zero_spread_count": zero_spread,
        "gap_count": gap_count,
        "gaps_utc_msc": gaps,
        "gaps_truncated": gap_count > len(gaps),
        "ticks_per_utc_day": dict(sorted(per_day.items())),
        "distribution_statistics_included": False,
        "note": "counts and coverage only; no price/spread distributions (holdout custody)",
    }


def hard_defects(report: dict[str, Any]) -> list[str]:
    """Defects that make a dataset unusable as-is; gaps are reported, not judged, here."""
    out = []
    for key in ("non_monotonic_count", "crossed_quote_count", "nonpositive_price_count"):
        if report[key]:
            out.append(key)
    return out
