"""NEXORA M30 buckets and sampled candles (ADR-026 Decision 2). Pure, no I/O.

A NEXORA M30 candle is built from sampled canonical events; it is not an MT5 M30 bar.
Bucketing uses the recorded UTC ``event_time`` as delivered upstream (Q-M7) and never
applies or infers a broker offset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from nexora.m30_bias.models import BUCKET, DURATION_SECONDS

if TYPE_CHECKING:
    from nexora.market_data.models import NormalizedPriceEvent

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROS_PER_BUCKET = DURATION_SECONDS * 1_000_000


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required")
    return value.astimezone(UTC)


def bucket_start(event_time: datetime) -> datetime:
    """``B_k = floor(epoch / 1800) * 1800`` in UTC, exact to the microsecond."""
    micros = (require_utc(event_time) - _EPOCH) // timedelta(microseconds=1)
    return _EPOCH + timedelta(microseconds=(micros // _MICROS_PER_BUCKET) * _MICROS_PER_BUCKET)


def bucket_end(start: datetime) -> datetime:
    return start + BUCKET


def seconds(delta: timedelta) -> Decimal:
    return Decimal(delta // timedelta(microseconds=1)) / Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Sample:
    """One canonical event as used by candles and outcome windows."""

    event_time: datetime
    received_at: datetime
    price: Decimal
    identity: str
    flagged_incomplete: bool  # is_gap or completeness == "partial"
    completeness_unknown: bool

    @classmethod
    def from_event(cls, event: NormalizedPriceEvent, completeness: str) -> Sample:
        return cls(
            event_time=require_utc(event.event_time),
            received_at=require_utc(event.received_at),
            price=event.price,
            identity=event.identity_key,
            flagged_incomplete=bool(event.is_gap) or completeness == "partial",
            completeness_unknown=completeness not in ("complete", "partial"),
        )


@dataclass(frozen=True, slots=True)
class M30Candle:
    bucket_start: datetime
    bucket_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    sample_count: int
    first_event_identity: str
    last_event_identity: str
    first_sample_time: datetime
    last_sample_time: datetime
    max_sample_gap_seconds: Decimal | None  # None with a single sample
    gap_or_incomplete_samples: int


def build_candle(samples: tuple[Sample, ...]) -> M30Candle:
    """Sampled OHLC of one bucket; all samples must share the bucket and be ordered."""
    if not samples:
        raise ValueError("empty_candle")
    start = bucket_start(samples[0].event_time)
    previous: Sample | None = None
    max_gap: Decimal | None = None
    for sample in samples:
        if bucket_start(sample.event_time) != start:
            raise ValueError("mixed_bucket_samples")
        if previous is not None:
            if sample.event_time < previous.event_time:
                raise ValueError("out_of_order_event")
            gap = seconds(sample.event_time - previous.event_time)
            max_gap = gap if max_gap is None or gap > max_gap else max_gap
        previous = sample
    prices = [s.price for s in samples]
    return M30Candle(
        bucket_start=start,
        bucket_end=bucket_end(start),
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
        sample_count=len(samples),
        first_event_identity=samples[0].identity,
        last_event_identity=samples[-1].identity,
        first_sample_time=samples[0].event_time,
        last_sample_time=samples[-1].event_time,
        max_sample_gap_seconds=max_gap,
        gap_or_incomplete_samples=sum(1 for s in samples if s.flagged_incomplete),
    )
