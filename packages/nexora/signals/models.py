"""Contracts for explainable research signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

SignalSide = Literal["long", "short"]
SignalAction = Literal["BUY", "SELL", "WAIT"]
EvidencePolarity = Literal["bullish", "bearish", "neutral"]
PatternDirection = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True, slots=True)
class SignalWeights:
    pnf_reversal: int = 20
    structure: int = 25
    support_resistance: int = 20
    matrix: int = 20
    regime: int = 15
    pattern_confirmation: int = 10
    pattern_conflict_penalty: int = 15


@dataclass(frozen=True, slots=True)
class PriceRange:
    low: Decimal
    high: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class SignalTarget:
    name: Literal["TP1", "TP2"]
    price: Decimal
    method: str


@dataclass(frozen=True, slots=True)
class PatternEvidence:
    pattern_type: str
    direction: PatternDirection
    start_time: datetime
    confirmation_time: datetime
    price_low: Decimal
    price_high: Decimal
    evidence_code: str
    source_data_reference: str
    algorithm_version: str
    relation: Literal["confirmation", "conflict", "neutral"]


@dataclass(frozen=True, slots=True)
class SignalEvidence:
    component: Literal["pnf", "structure", "support_resistance", "matrix", "regime", "pattern"]
    code: str
    points: int
    polarity: EvidencePolarity
    reason: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SignalDecision:
    action: SignalAction
    score: int
    entry_zone: PriceRange | None
    invalidation_price: Decimal | None
    invalidation_reason: str | None
    targets: tuple[SignalTarget, ...]
    risk_reward: Decimal | None
    patterns: tuple[PatternEvidence, ...]
    positive_evidence: tuple[SignalEvidence, ...]
    negative_evidence: tuple[SignalEvidence, ...]
    future_conditions: tuple[str, ...]
    config_version: str
    engine_version: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SignalConfig:
    symbol: str
    cooldown_events: int
    expiry_events: int
    version: str
    weights: SignalWeights = SignalWeights()
    wait_score_max: int = 49
    action_score_min: int = 65
    action_gap_min: int = 8
    pattern_price_tolerance: Decimal = Decimal("0.8")
    entry_zone_half_width: Decimal = Decimal("0.5")
    target_rr_tp1: Decimal = Decimal("1.5")
    target_rr_tp2: Decimal = Decimal("2.5")


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
    decision: SignalDecision | None = None


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int
    latest: ResearchSignal | None
    history: tuple[ResearchSignal, ...]
    decision: SignalDecision
