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

from nexora.market_history.models import HistoryError, NormalizedTick, decimal_str

QUALITY_VERSION = "nexora.tick-quality/2"
LEGACY_QUALITY_VERSION = "nexora.tick-quality/1"  # adjacent-only duplicates; verified, not produced
MAX_GAP_LIST = 200
ABSENT = "ABSENT"
TICK_IDENTITY = (
    "(raw_msc, canonical bid, canonical ask, flags or ABSENT): exact decimal values as received; "
    "a repeat counts as an exact duplicate wherever it occurs in its chunk (chunks partition raw "
    "time, so no identity can recur across chunks)"
)


@dataclass(frozen=True, slots=True)
class QualityConfig:
    """`max_gap_ms` has no default: what counts as a data gap is a Quant decision (OPEN)."""

    max_gap_ms: int

    def __post_init__(self) -> None:
        if self.max_gap_ms <= 0:
            raise HistoryError("invalid_max_gap")


def _day(utc_msc: int) -> str:
    return datetime.fromtimestamp(utc_msc // 1000, tz=UTC).strftime("%Y-%m-%d")


def _quote_text(value: Any) -> str:
    return decimal_str(value) if value.is_finite() else str(value)


class QualityAccumulator:
    """Push-style form of `quality_report`, so a verifier can feed the SAME bytes it hashed, one
    chunk at a time, without reopening any file (N3).

    Duplicate semantics (version 2, N6/X4): a tick's identity is `TICK_IDENTITY`. Within a
    segment (one chunk, or the whole input of `quality_report`):
      * `exact_duplicate_count` = sum over identities of (occurrences - 1), adjacent or not;
      * `same_timestamp_different_quote_count` = sum over raw timestamps of (distinct (bid, ask)
        pairs - 1);
      * `same_quote_different_flags_count` = sum over (raw timestamp, bid, ask) of (distinct flag
        values - 1).
    These match the independent PR #95 oracle (ORACLE-SPEC section 3). None of them is a hard
    defect: whether duplicates block trust is a Quant decision (OPEN), not decided here.
    Memory is bounded by one segment. `version` = LEGACY_QUALITY_VERSION reproduces the version 1
    report (adjacent comparison only) so that older manifests can still be verified."""

    def __init__(self, config: QualityConfig, version: str = QUALITY_VERSION) -> None:
        if version not in (QUALITY_VERSION, LEGACY_QUALITY_VERSION):
            raise HistoryError("quality_version_unsupported", str(version)[:40])
        self.version = version
        self.config = config
        self.same_quote_diff_flags = 0
        self._ids: set[tuple[int, str, str, str]] = set()
        self._quotes: dict[int, set[tuple[str, str]]] = {}
        self._flags: dict[tuple[int, str, str], set[str]] = {}
        self.total = self.non_monotonic = self.same_ts_diff_quote = self.exact_dups = 0
        self.crossed = self.nonpositive = self.zero_spread = self.nonfinite = 0
        self.first: int | None = None
        self.last: int | None = None
        self.prev: NormalizedTick | None = None
        self.gaps: list[list[int]] = []
        self.gap_count = 0
        self.per_day: dict[str, int] = {}

    def add(self, t: NormalizedTick) -> None:
        if self.version == QUALITY_VERSION:
            self._identity(t)
        self.total += 1
        self.first = t.utc_msc if self.first is None else min(self.first, t.utc_msc)
        self.last = t.utc_msc if self.last is None else max(self.last, t.utc_msc)
        d = _day(t.utc_msc)
        self.per_day[d] = self.per_day.get(d, 0) + 1
        if not (t.bid.is_finite() and t.ask.is_finite()):
            self.nonfinite += 1  # counted; never compared (NaN ordering raises)
            return
        if t.bid <= 0 or t.ask <= 0:
            self.nonpositive += 1
        if t.ask < t.bid:
            self.crossed += 1
        if t.ask == t.bid:
            self.zero_spread += 1
        prev = self.prev
        if prev is not None:
            if t.utc_msc < prev.utc_msc:
                self.non_monotonic += 1
            elif t.utc_msc == prev.utc_msc:
                if self.version == QUALITY_VERSION:
                    pass  # counted over the whole segment by `_identity`
                elif (t.bid, t.ask) == (prev.bid, prev.ask):
                    self.exact_dups += 1
                else:
                    self.same_ts_diff_quote += 1
            elif t.utc_msc - prev.utc_msc > self.config.max_gap_ms:
                self.gap_count += 1
                if len(self.gaps) < MAX_GAP_LIST:
                    self.gaps.append([prev.utc_msc, t.utc_msc])
        self.prev = t

    def _identity(self, t: NormalizedTick) -> None:
        bid, ask = _quote_text(t.bid), _quote_text(t.ask)
        flags = ABSENT if t.flags is None else str(t.flags)
        ident = (t.raw_msc, bid, ask, flags)
        if ident in self._ids:
            self.exact_dups += 1
            return
        self._ids.add(ident)
        quotes = self._quotes.setdefault(t.raw_msc, set())
        if (bid, ask) not in quotes:
            self.same_ts_diff_quote += 1 if quotes else 0
            quotes.add((bid, ask))
        seen_flags = self._flags.setdefault((t.raw_msc, bid, ask), set())
        if flags not in seen_flags:
            self.same_quote_diff_flags += 1 if seen_flags else 0
            seen_flags.add(flags)

    def end_segment(self) -> None:
        """Called between chunks: identity state is per segment (bounded memory)."""
        self._ids.clear()
        self._quotes.clear()
        self._flags.clear()

    def report(self) -> dict[str, Any]:
        out = self._report()
        if self.version == QUALITY_VERSION:
            out["same_quote_different_flags_count"] = self.same_quote_diff_flags
            out["tick_identity"] = TICK_IDENTITY
        return out

    def _report(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "config": {"max_gap_ms": self.config.max_gap_ms},
            "tick_count": self.total,
            "first_utc_msc": self.first,
            "last_utc_msc": self.last,
            "non_monotonic_count": self.non_monotonic,
            "exact_duplicate_count": self.exact_dups,
            "same_timestamp_different_quote_count": self.same_ts_diff_quote,
            "crossed_quote_count": self.crossed,
            "nonfinite_quote_count": self.nonfinite,
            "nonpositive_price_count": self.nonpositive,
            "zero_spread_count": self.zero_spread,
            "gap_count": self.gap_count,
            "gaps_utc_msc": self.gaps,
            "gaps_truncated": self.gap_count > len(self.gaps),
            "ticks_per_utc_day": dict(sorted(self.per_day.items())),
            "distribution_statistics_included": False,
            "note": "counts and coverage only; no price/spread distributions (holdout custody)",
        }


def quality_report(ticks: Iterable[NormalizedTick], config: QualityConfig) -> dict[str, Any]:
    acc = QualityAccumulator(config)
    for t in ticks:
        acc.add(t)
    return acc.report()


def hard_defects(report: dict[str, Any]) -> list[str]:
    """Defects that make a dataset unusable as-is; gaps are reported, not judged, here."""
    out = []
    for key in (
        "non_monotonic_count",
        "crossed_quote_count",
        "nonpositive_price_count",
        "nonfinite_quote_count",
    ):
        if report[key]:
            out.append(key)
    return out
