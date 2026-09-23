"""Contracts for the deterministic Entry Readiness permission filter (ADR-021)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nexora.signals.models import SignalAction
from nexora.trendline.models import TrendlineKind

EntryReadinessState = Literal["READY", "DEVELOPING", "NOT_READY", "BLOCKED"]
EntryBlockerCode = Literal[
    "aligned_trendline_broken",
    "aligned_trendline_retest_held",
]
PendingConfirmationCode = Literal["aligned_trendline_retest_pending"]


@dataclass(frozen=True, slots=True)
class EntryBlocker:
    code: EntryBlockerCode
    side: SignalAction
    trendline_kind: TrendlineKind
    line_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    code: PendingConfirmationCode
    side: SignalAction
    trendline_kind: TrendlineKind
    line_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class EntryReadinessSnapshot:
    schema_version: Literal[1]
    symbol: str
    state: EntryReadinessState
    signal_action: SignalAction
    blockers: tuple[EntryBlocker, ...]
    pending_confirmations: tuple[PendingConfirmation, ...]
    config_version: str
