"""Market-data normalization policy and stream state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from nexora.market_data.models import (
    MarketBar,
    MarketDataError,
    MarketDataPolicy,
    MarketTick,
    NormalizedPriceEvent,
    PriceSource,
    ensure_utc,
    quantize_decimal,
    require_nonnegative_decimal,
    require_positive_decimal,
    select_price,
)

type StreamKey = tuple[str, str]


@dataclass(slots=True)
class StreamState:
    last_sequence: int | None = None
    last_event_time: datetime | None = None
    seen_identity_keys: set[str] = field(default_factory=set, repr=False)


def _identity_key(source: str, symbol: str, sequence: int, price_source: PriceSource,
                  event_id: str | None, kind: str) -> str:
    stable_event_id = event_id or f"sequence:{sequence}"
    return f"{source}:{symbol}:{kind}:{price_source}:{stable_event_id}"


def _normalize_prices_for_tick(
    tick: MarketTick,
) -> tuple[datetime, datetime, tuple[Decimal, Decimal, Decimal | None]]:
    event_time = ensure_utc(tick.event_time)
    received_at = ensure_utc(tick.received_at)
    bid = quantize_decimal(require_positive_decimal(tick.bid, "bid"), tick.digits)
    ask = quantize_decimal(require_positive_decimal(tick.ask, "ask"), tick.digits)
    if ask < bid:
        raise MarketDataError("crossed_market")
    last = None
    if tick.last is not None:
        last = quantize_decimal(require_positive_decimal(tick.last, "last"), tick.digits)
    if tick.volume is not None:
        require_nonnegative_decimal(tick.volume, "volume")
    return event_time, received_at, (bid, ask, last)


def _normalize_prices_for_bar(
    bar: MarketBar,
) -> tuple[datetime, datetime, tuple[Decimal, Decimal, Decimal, Decimal]]:
    event_time = ensure_utc(bar.event_time)
    received_at = ensure_utc(bar.received_at)
    open_price = quantize_decimal(require_positive_decimal(bar.open_price, "open"), bar.digits)
    high = quantize_decimal(require_positive_decimal(bar.high, "high"), bar.digits)
    low = quantize_decimal(require_positive_decimal(bar.low, "low"), bar.digits)
    close = quantize_decimal(require_positive_decimal(bar.close, "close"), bar.digits)
    if high < low:
        raise MarketDataError("crossed_range")
    if not (low <= open_price <= high and low <= close <= high):
        raise MarketDataError("invalid_bar_range")
    if bar.volume is not None:
        require_nonnegative_decimal(bar.volume, "volume")
    return event_time, received_at, (open_price, high, low, close)


class MarketDataNormalizer:
    """Normalize raw market observations and retain stream state."""

    def __init__(self, policy: MarketDataPolicy) -> None:
        if not policy.units:
            raise MarketDataError("missing_units")
        self.policy = policy
        self._streams: dict[StreamKey, StreamState] = {}

    def state_for(self, source: str, symbol: str) -> StreamState:
        return self._streams.setdefault((source, symbol), StreamState())

    def normalize_tick(self, tick: MarketTick) -> NormalizedPriceEvent:
        event_time, received_at, values = _normalize_prices_for_tick(tick)
        bid, ask, last = values
        price_source = self.policy.tick_price_source
        price = select_price(price_source, bid=bid, ask=ask, last=last)
        return NormalizedPriceEvent(
            schema_version=1,
            identity_key=_identity_key(
                tick.source,
                tick.symbol,
                tick.sequence,
                price_source,
                tick.source_event_id,
                "tick",
            ),
            source=tick.source,
            symbol=tick.symbol,
            kind="tick",
            event_time=event_time,
            received_at=received_at,
            source_sequence=tick.sequence,
            source_order=tick.sequence,
            source_event_id=tick.source_event_id or f"{tick.source}:{tick.symbol}:{tick.sequence}",
            price_source=price_source,
            units=self.policy.units,
            precision=tick.digits,
            price=price,
            bid=bid,
            ask=ask,
            last=last,
        )

    def normalize_bar(self, bar: MarketBar) -> NormalizedPriceEvent:
        event_time, received_at, values = _normalize_prices_for_bar(bar)
        open_price, high, low, close = values
        price_source = self.policy.bar_price_source
        price = select_price(
            price_source,
            open_price=open_price,
            high=high,
            low=low,
            close=close,
        )
        return NormalizedPriceEvent(
            schema_version=1,
            identity_key=_identity_key(
                bar.source,
                bar.symbol,
                bar.sequence,
                price_source,
                bar.source_event_id,
                "bar",
            ),
            source=bar.source,
            symbol=bar.symbol,
            kind="bar",
            event_time=event_time,
            received_at=received_at,
            source_sequence=bar.sequence,
            source_order=bar.sequence,
            source_event_id=bar.source_event_id or f"{bar.source}:{bar.symbol}:{bar.sequence}",
            price_source=price_source,
            units=self.policy.units,
            precision=bar.digits,
            price=price,
            open_price=open_price,
            high=high,
            low=low,
            close=close,
        )

    def process_tick(self, tick: MarketTick) -> NormalizedPriceEvent:
        return self._mark_stream(self.normalize_tick(tick))

    def process_bar(self, bar: MarketBar) -> NormalizedPriceEvent:
        return self._mark_stream(self.normalize_bar(bar))

    def _mark_stream(self, event: NormalizedPriceEvent) -> NormalizedPriceEvent:
        stream = self.state_for(event.source, event.symbol)
        is_duplicate = event.identity_key in stream.seen_identity_keys
        is_out_of_order = False
        is_gap = False
        gap_from_sequence: int | None = None
        if stream.last_sequence is not None and event.source_sequence < stream.last_sequence:
            is_out_of_order = True
        if stream.last_event_time is not None and event.event_time < stream.last_event_time:
            is_out_of_order = True
        if stream.last_sequence is not None and event.source_sequence > stream.last_sequence + 1:
            is_gap = True
            gap_from_sequence = stream.last_sequence + 1
        if not is_duplicate:
            stream.seen_identity_keys.add(event.identity_key)
        if stream.last_sequence is None or event.source_sequence >= stream.last_sequence:
            stream.last_sequence = event.source_sequence
        if stream.last_event_time is None or event.event_time >= stream.last_event_time:
            stream.last_event_time = event.event_time
        return replace(
            event,
            is_duplicate=is_duplicate,
            is_out_of_order=is_out_of_order,
            is_gap=is_gap,
            gap_from_sequence=gap_from_sequence,
        )
