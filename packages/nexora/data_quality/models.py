"""Contracts for the deterministic Data Quality Guard (ADR-036).

Pure domain: no I/O, no wall clock, no broker, no UI/API/DB imports. Callers pass
``evaluated_at`` explicitly so historical replay and live paths share one code path.

Every contract here validates itself strictly at construction. A value that violates an
invariant cannot exist, so a forged or malformed ``QualityVerdict`` or configuration can
never be mistaken for a permissive one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

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
# Additive in hardening round 1 (price / OHLC trust boundary).
INVALID_PRICE = "invalid_price"
INVALID_OHLC = "invalid_ohlc"
INCOMPLETE_OHLC = "incomplete_ohlc"
PRICE_SOURCE_MISMATCH = "price_source_mismatch"
INVALID_PRECISION = "invalid_precision"
INVALID_EVENT_KIND = "invalid_event_kind"

# Sanity ceilings for thresholds. They exist so a typo or hostile value (for example
# ``max_quote_age_seconds=10**12``) cannot silently turn a check into a no-op. They are
# generous configuration-sanity bounds, not trading semantics; Quant owns the real values.
MAX_QUOTE_AGE_CEILING_SECONDS = 86_400
MAX_FUTURE_SKEW_CEILING_SECONDS = 3_600
MAX_LATENCY_CEILING_MS = 3_600_000
MAX_GAP_CEILING_SECONDS = 604_800
MIN_EVENTS_CEILING = 100_000

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+\-]{0,127}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _is_clean_text(value: str) -> bool:
    """Non-empty, trimmed, bounded, printable: no control characters or edge whitespace."""
    return (
        0 < len(value) <= 128 and value == value.strip() and all(ch.isprintable() for ch in value)
    )


def _strict_int(config: object, name: str, *, minimum: int, maximum: int) -> None:
    value = getattr(config, name)
    # ``type(...) is int`` deliberately rejects bool (an int subclass), numpy ints, floats
    # and numeric strings: a flag or text can never masquerade as a threshold.
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid_{name}")


def _strict_bool(config: object, name: str) -> None:
    if type(getattr(config, name)) is not bool:
        raise ValueError(f"invalid_{name}")


def _strict_cap(config: object, name: str, *, maximum: Decimal | None) -> None:
    cap = getattr(config, name)
    if cap is None:
        return
    # Only a real, finite, strictly positive Decimal is a cap. ``float``/``int``/``bool``/
    # ``str`` are rejected outright, never coerced.
    if type(cap) is not Decimal or not cap.is_finite() or cap <= 0:
        raise ValueError(f"invalid_{name}")
    if maximum is not None and cap > maximum:
        raise ValueError(f"invalid_{name}")


@dataclass(frozen=True, slots=True)
class QualityGuardConfig:
    """Explicit, versioned thresholds. Every threshold is required: no broker defaults.

    Validation is strict and fail-closed: an invalid configuration cannot be constructed,
    and the Guard re-validates it on construction, so no invalid configuration can ever
    disable a check or yield an ``ok`` verdict.
    """

    version: str
    max_quote_age_seconds: int
    max_future_skew_seconds: int
    max_latency_ms: int
    max_gap_seconds: int
    min_events: int
    require_bid_ask: bool
    allow_zero_spread: bool
    # ``None`` disables that spread cap explicitly; there is no hidden default. At least one
    # cap is required whenever bid/ask is required (see ``validate``).
    max_spread: Decimal | None
    max_spread_to_price: Decimal | None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Raise ``ValueError(<code>)`` unless every field and the combination are valid."""
        version = self.version
        if not isinstance(version, str) or not version:
            raise ValueError("missing_version")
        if not _VERSION.fullmatch(version):
            raise ValueError("invalid_version")
        _strict_int(self, "max_quote_age_seconds", minimum=1, maximum=MAX_QUOTE_AGE_CEILING_SECONDS)
        _strict_int(
            self, "max_future_skew_seconds", minimum=0, maximum=MAX_FUTURE_SKEW_CEILING_SECONDS
        )
        _strict_int(self, "max_latency_ms", minimum=1, maximum=MAX_LATENCY_CEILING_MS)
        _strict_int(self, "max_gap_seconds", minimum=1, maximum=MAX_GAP_CEILING_SECONDS)
        _strict_int(self, "min_events", minimum=1, maximum=MIN_EVENTS_CEILING)
        _strict_bool(self, "require_bid_ask")
        _strict_bool(self, "allow_zero_spread")
        _strict_cap(self, "max_spread", maximum=None)
        # A spread-to-price ratio above 1 means "spread larger than the price": nonsensical.
        _strict_cap(self, "max_spread_to_price", maximum=Decimal(1))
        has_cap = self.max_spread is not None or self.max_spread_to_price is not None
        if self.require_bid_ask and not has_cap:
            # Bid/ask is required but nothing bounds the spread: the spread check would be
            # a silent no-op. Configure a cap instead of relying on "no cap".
            raise ValueError("unbounded_spread")
        if not self.require_bid_ask and has_cap:
            # A cap on a quote that is allowed to be absent would never be evaluated.
            raise ValueError("contradictory_spread_cap_without_bid_ask")


@dataclass(frozen=True, slots=True)
class QualityExpectation:
    """Identity the caller (adapter/config) declares the data must carry."""

    source: str
    symbol: str
    units: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for value in (self.source, self.symbol, self.units):
            if not isinstance(value, str) or not value:
                raise ValueError("incomplete_expectation")
            if not _is_clean_text(value):
                raise ValueError("invalid_expectation")


@dataclass(frozen=True, slots=True)
class MarketDataSnapshot:
    """Normalized events in arrival order. Never mutated, repaired or interpolated."""

    events: tuple[NormalizedPriceEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple) or not all(
            isinstance(event, NormalizedPriceEvent) for event in self.events
        ):
            raise ValueError("invalid_snapshot")


class SequenceHistoryProvider(Protocol):
    """Contract only: the trusted source of the events that precede the evaluated one.

    The Guard is stateless (ADR-036 D2a). It can only judge ordering, sequence gaps,
    duplicates and staleness *inside the snapshot it is handed*. A caller that passes a
    one-event snapshot gets a verdict about that event alone and no continuity guarantee.
    Production composition must therefore build every snapshot from a provider that returns
    the contiguous, arrival-ordered history ending in the evaluated event, and must configure
    ``min_events`` accordingly. No implementation exists in this PR.
    """

    def history_ending_at(self, event: NormalizedPriceEvent) -> MarketDataSnapshot: ...


@dataclass(frozen=True, slots=True)
class QualityFinding:
    code: str
    severity: FindingSeverity
    event_key: str | None
    # Raw values the finding was derived from, as sorted (name, text) pairs.
    evidence: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    """Result of one Guard evaluation. Construction enforces the state/permission invariants.

    A verdict is *evidence*, never proof: ``decision_audit`` never accepts a caller-built
    verdict and instead obtains it from the Guard and re-verifies its binding to the exact
    evaluated event and snapshot hash.
    """

    schema_version: Literal[1]
    state: QualityState
    # True only for ``ok``. ``blocked``, ``unknown`` and any guard error mean NO NEW TRADE.
    new_trade_permitted: bool
    findings: tuple[QualityFinding, ...]
    evaluated_at: datetime | None
    event_keys: tuple[str, ...]
    snapshot_hash: str | None
    config_version: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("invalid_quality_verdict")
        if self.state not in ("ok", "blocked", "unknown"):
            raise ValueError("invalid_quality_verdict")
        if type(self.new_trade_permitted) is not bool:
            raise ValueError("invalid_quality_verdict")
        if not isinstance(self.findings, tuple) or not all(
            isinstance(f, QualityFinding)
            and isinstance(f.code, str)
            and f.code
            and f.severity in ("blocking", "unknown")
            for f in self.findings
        ):
            raise ValueError("invalid_quality_verdict")
        if not isinstance(self.event_keys, tuple) or not all(
            isinstance(k, str) and k for k in self.event_keys
        ):
            raise ValueError("invalid_quality_verdict")
        if not isinstance(self.config_version, str) or not self.config_version:
            raise ValueError("invalid_quality_verdict")
        if self.snapshot_hash is not None and (
            not isinstance(self.snapshot_hash, str) or not _SHA256_HEX.fullmatch(self.snapshot_hash)
        ):
            raise ValueError("invalid_quality_verdict")

        blocking = any(f.severity == "blocking" for f in self.findings)
        # The permission bit must be exactly "state is ok": it can never be set independently.
        if self.new_trade_permitted != (self.state == "ok"):
            raise ValueError("invalid_quality_verdict")
        if self.state == "ok":
            ok_evidence = (
                not self.findings
                and self.evaluated_at is not None
                and self.evaluated_at.tzinfo is not None
                and self.evaluated_at.utcoffset() is not None
                and self.snapshot_hash is not None
                and len(self.event_keys) > 0
                and len(set(self.event_keys)) == len(self.event_keys)
            )
            if not ok_evidence:
                raise ValueError("invalid_quality_verdict")
        elif self.state == "blocked":
            if not blocking:
                raise ValueError("invalid_quality_verdict")
        elif not self.findings or blocking:  # unknown
            raise ValueError("invalid_quality_verdict")
