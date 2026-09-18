"""Contracts for confirmed pivots and candidate S/R levels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class ConfirmedPivot:
    kind: Literal["high", "low"]
    price: Decimal
    occurrence_time: datetime
    confirmation_time: datetime
    source_transition_id: str
    config_version: str


@dataclass(frozen=True, slots=True)
class CandidateLevel:
    side: Literal["support", "resistance"]
    price: Decimal
    status: Literal["candidate", "confirmed", "invalidated", "unavailable"]
    source_pivot_id: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int
    pivots: tuple[ConfirmedPivot, ...]
    levels: tuple[CandidateLevel, ...]

