"""Contracts for trend/range/high-volatility regime states."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

RegimeLabel = Literal["trend", "range", "high_volatility", "unknown"]


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    symbol: str
    lookback: int
    trend_min_slope: Decimal
    high_volatility_min_width: Decimal
    hysteresis: Decimal
    version: str


@dataclass(frozen=True, slots=True)
class RegimeState:
    label: RegimeLabel
    reason: str
    effective_time: datetime
    source_ref: str | None
    config_version: str


@dataclass(frozen=True, slots=True)
class RegimeSnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int
    state: RegimeState

