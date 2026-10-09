"""Contracts for the deterministic Data Quality Guard (ADR-036).

Pure domain: no I/O, no wall clock, no broker, no UI/API/DB imports. Callers pass
``evaluated_at`` explicitly so historical replay and live paths share one code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexora.market_data.models import NormalizedPriceEvent

QualityState = Literal["ok", "blocked", "unknown"]
FindingSeverity = Literal["blocking", "unknown"]

# Stable machine-readable reason codes. New codes are additive; existing ones never change.
MISSING_MARKET_DATA = "missing_market_data"
INCOMPLETE_SNAPSHOT = "incomplete_snapshot"
MISSING_BID_ASK = "missing_bid_ask"
DUPLICATE_RECORD = "duplicate_record"
IDENTITY_CONFLICT = "identity_conflict"
OUT_OF_ORDER_TIMESTAMP = "out_of_order_timestamp"
OUT_OF_ORDER_SEQUENCE = "out_of_order_sequence"
STALE_QUOTE = "stale_quote"
HIGH_LATENCY = "high_latency"
CLOCK_ANOMALY_FUTURE_EVENT = "clock_anomaly_future_event"
CLOCK_ANOMALY_RECEIVED_BEFORE_EVENT = "clock_anomaly_received_before_event"
TIMESTAMP_GAP = "timestamp_gap"
SEQUENCE_GAP = "sequence_gap"
INVALID_BID_ASK = "invalid_bid_ask"
NEGATIVE_SPREAD = "negative_spread"
ZERO_SPREAD = "zero_spread"
EXCESSIVE_SPREAD = "excessive_spread"
NON_FINITE_VALUE = "non_finite_value"
INVALID_NUMERIC_TYPE = "invalid_numeric_type"
IDENTITY_MISMATCH = "identity_mismatch"
UNSYNCHRONIZED_CLOCK = "unsynchronized_clock"
GUARD_INTERNAL_ERROR = "guard_internal_error"


@dataclass(frozen=True, slots=True)
class QualityGuardConfig:
    """Explicit, versioned thresholds. Every threshold is required: no broker defaults."""

    version: str
    max_quote_age_seconds: int
    max_future_skew_seconds: int
    max_latency_ms: int
    max_gap_seconds: int
    min_events: int
    require_bid_ask: bool
    allow_zero_spread: bool
    # ``None`` disables that spread cap explicitly; there is no hidden default.
    max_spread: Decimal | None
    max_spread_to_price: Decimal | None

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("missing_version")
        for name in (
            "max_quote_age_seconds",
            "max_future_skew_seconds",
            "max_latency_ms",
            "max_gap_seconds",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"invalid_{name}")
        if self.min_events < 1:
            raise ValueError("invalid_min_events")
        for name in ("max_spread", "max_spread_to_price"):
            cap = getattr(self, name)
            if cap is not None and (not cap.is_finite() or cap < 0):
                raise ValueError(f"invalid_{name}")


@dataclass(frozen=True, slots=True)
class QualityExpectation:
    """Identity the caller (adapter/config) declares the data must carry."""

    source: str
    symbol: str
    units: str

    def __post_init__(self) -> None:
        if not (self.source and self.symbol and self.units):
            raise ValueError("incomplete_expectation")


@dataclass(frozen=True, slots=True)
class MarketDataSnapshot:
    """Normalized events in arrival order. Never mutated, repaired or interpolated."""

    events: tuple[NormalizedPriceEvent, ...]


@dataclass(frozen=True, slots=True)
class QualityFinding:
    code: str
    severity: FindingSeverity
    event_key: str | None
    # Raw values the finding was derived from, as sorted (name, text) pairs.
    evidence: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    schema_version: Literal[1]
    state: QualityState
    # True only for ``ok``. ``blocked``, ``unknown`` and any guard error mean NO NEW TRADE.
    new_trade_permitted: bool
    findings: tuple[QualityFinding, ...]
    evaluated_at: datetime | None
    event_keys: tuple[str, ...]
    snapshot_hash: str | None
    config_version: str
