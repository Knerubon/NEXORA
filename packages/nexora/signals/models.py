"""Contracts for explainable research signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

SignalSide = Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class SignalConfig:
    symbol: str
    cooldown_events: int
    expiry_events: int
    version: str


@dataclass(frozen=True, slots=True)
class ResearchSignal:
    signal_id: str
    symbol: str
    side: SignalSide
    sequence: int
    occurrence_time: datetime
    confirmation_time: datetime
    decision_time: datetime
    reasons: tuple[str, ...]
    reason_codes: tuple[str, ...]
    source_refs: tuple[str, ...]
    config_version: str
    engine_versions: tuple[str, ...]
    status: Literal["active", "expired"]


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int
    latest: ResearchSignal | None
    history: tuple[ResearchSignal, ...]

