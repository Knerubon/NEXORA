"""Contracts for causal P&F trendlines (ADR-020)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

TrendlineKind = Literal["bullish_support", "bearish_resistance"]
TrendlineLifecycleState = Literal[
    "forming",
    "active",
    "broken",
    "retesting",
    "retest_held",
    "retest_failed",
    "replaced",
]
RetestOutcome = Literal["held", "failed", "none"]


@dataclass(frozen=True, slots=True)
class TrendlineAnchor:
    pivot_kind: Literal["high", "low"]
    price: Decimal
    column_id: int
    source_pivot_id: str
    event_time: datetime


@dataclass(frozen=True, slots=True)
class TrendlineLine:
    line_id: str
    kind: TrendlineKind
    state: TrendlineLifecycleState
    anchor_a: TrendlineAnchor
    anchor_b: TrendlineAnchor
    slope_price_per_column: Decimal
    projected_price_at_latest_column: Decimal
    touch_columns: tuple[int, ...]
    break_column: int | None
    break_transition_id: str | None
    retest_column: int | None
    retest_resolved_column: int | None
    retest_outcome: RetestOutcome
    replaced_by_line_id: str | None
    age_columns: int
    config_version: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrendlineSnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int
    active_bullish: TrendlineLine | None
    active_bearish: TrendlineLine | None
    history: tuple[TrendlineLine, ...]
