"""Strict validation of holdout/protected ranges. Malformed input must refuse, never skip."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nexora.market_history.models import HistoryError

_BOUND = 2**62


def validate_protected_ranges(ranges: Any) -> tuple[tuple[int, int], ...]:
    """Return sorted (lo, hi) pairs of exact ints with lo < hi, or raise `invalid_protected_range`.

    `None`, strings, mappings, generators, bools, floats, empty or inverted ranges are all refused.
    An empty sequence is the explicit statement "nothing is protected" and is accepted."""
    if not isinstance(ranges, (list, tuple)):
        raise HistoryError("invalid_protected_range", "ranges must be an explicit list or tuple")
    out: list[tuple[int, int]] = []
    for i, entry in enumerate(ranges):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise HistoryError("invalid_protected_range", f"entry {i} must be [lo, hi]")
        lo, hi = entry
        if type(lo) is not int or type(hi) is not int:
            raise HistoryError("invalid_protected_range", f"entry {i} bounds must be ints")
        if not (-_BOUND < lo < hi < _BOUND):
            raise HistoryError("invalid_protected_range", f"entry {i} must satisfy lo < hi")
        out.append((lo, hi))
    return tuple(sorted(out))


def validate_margin(margin_ms: Any) -> int:
    if type(margin_ms) is not int or margin_ms < 0 or margin_ms > _BOUND:
        raise HistoryError("invalid_protected_margin")
    return margin_ms


def overlaps(lo: int, hi: int, ranges: Sequence[tuple[int, int]], margin_ms: int) -> bool:
    """Window UTC instants [lo, hi] (inclusive) widened by margin against [p_lo, p_hi)."""
    return any(lo - margin_ms < p_hi and p_lo <= hi + margin_ms for p_lo, p_hi in ranges)
