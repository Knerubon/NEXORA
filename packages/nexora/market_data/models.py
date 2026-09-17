"""Core market-data models and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

SchemaVersion = Literal[1]
PriceSource = Literal["bid", "ask", "last", "open", "high", "low", "close", "mid"]
ObservationKind = Literal["tick", "bar"]


class MarketDataError(ValueError):
    """Sanitized market-data validation error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError("timezone_required")
    return value.astimezone(UTC)


def require_positive_decimal(value: Decimal | str | float | int, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketDataError(f"invalid_{field_name}") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise MarketDataError(f"invalid_{field_name}")
    return decimal_value


def require_nonnegative_decimal(value: Decimal | str | float | int, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketDataError(f"invalid_{field_name}") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise MarketDataError(f"invalid_{field_name}")
    return decimal_value


def quantize_decimal(value: Decimal, precision: int) -> Decimal:
    if precision < 0 or precision > 10:
        raise MarketDataError("invalid_precision")
    quantum = Decimal(1).scaleb(-precision)
    return value.quantize(quantum)


def select_price(source: PriceSource, *, bid: Decimal | None = None,
                 ask: Decimal | None = None, last: Decimal | None = None,
                 open_price: Decimal | None = None, high: Decimal | None = None,
                 low: Decimal | None = None, close: Decimal | None = None) -> Decimal:
    if source == "bid":
        if bid is None:
            raise MarketDataError("missing_bid")
        return bid
    if source == "ask":
        if ask is None:
            raise MarketDataError("missing_ask")
        return ask
    if source == "last":
        if last is None:
            raise MarketDataError("missing_last")
        return last
    if source == "open":
        if open_price is None:
            raise MarketDataError("missing_open")
        return open_price
    if source == "high":
        if high is None:
            raise MarketDataError("missing_high")
        return high
    if source == "low":
        if low is None:
            raise MarketDataError("missing_low")
        return low
    if source == "close":
        if close is None:
            raise MarketDataError("missing_close")
        return close
    if source == "mid":
        if bid is None or ask is None:
            raise MarketDataError("missing_mid")
        return (bid + ask) / 2
    raise MarketDataError("unsupported_price_source")


@dataclass(frozen=True, slots=True)
class MarketTick:
    source: str
    symbol: str
    event_time: datetime
    received_at: datetime
    sequence: int
    digits: int
    bid: Decimal
    ask: Decimal
    last: Decimal | None = None
    volume: Decimal | None = None
    source_event_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise MarketDataError("missing_source")
        if not self.symbol:
            raise MarketDataError("missing_symbol")
        if self.sequence < 0:
            raise MarketDataError("invalid_sequence")
        if self.digits < 0 or self.digits > 10:
            raise MarketDataError("invalid_precision")


@dataclass(frozen=True, slots=True)
class MarketBar:
    source: str
    symbol: str
    event_time: datetime
    received_at: datetime
    sequence: int
    digits: int
    open_price: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    source_event_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise MarketDataError("missing_source")
        if not self.symbol:
            raise MarketDataError("missing_symbol")
        if self.sequence < 0:
            raise MarketDataError("invalid_sequence")
        if self.digits < 0 or self.digits > 10:
            raise MarketDataError("invalid_precision")


@dataclass(frozen=True, slots=True)
class NormalizedPriceEvent:
    schema_version: SchemaVersion
    identity_key: str
    source: str
    symbol: str
    kind: ObservationKind
    event_time: datetime
    received_at: datetime
    source_sequence: int
    source_order: int
    source_event_id: str
    price_source: PriceSource
    units: str
    precision: int
    price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    open_price: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    is_duplicate: bool = False
    is_out_of_order: bool = False
    is_gap: bool = False
    gap_from_sequence: int | None = None

    def semantic_key(self) -> tuple[str, ...]:
        return (
            self.schema_version.__str__(),
            self.identity_key,
            self.source,
            self.symbol,
            self.kind,
            self.event_time.isoformat(),
            self.received_at.isoformat(),
            str(self.source_sequence),
            str(self.source_order),
            self.source_event_id,
            self.price_source,
            self.units,
            str(self.precision),
            format(self.price, "f"),
        )


@dataclass(frozen=True, slots=True)
class MarketDataPolicy:
    tick_price_source: PriceSource
    bar_price_source: PriceSource
    units: str

