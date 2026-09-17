"""Synthetic market-data fixtures used by tests and replay samples."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from nexora.market_data.models import MarketBar, MarketTick


def canonical_tick_fixtures() -> tuple[MarketTick, ...]:
    local_time = datetime(2026, 1, 2, 9, 15, 0, tzinfo=timezone(timedelta(hours=7)))
    return (
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=local_time,
            received_at=local_time + timedelta(milliseconds=120),
            sequence=1,
            digits=3,
            bid=Decimal("2400.101"),
            ask=Decimal("2400.251"),
            last=Decimal("2400.176"),
            source_event_id="tick-1",
        ),
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=local_time + timedelta(seconds=1),
            received_at=local_time + timedelta(seconds=1, milliseconds=115),
            sequence=2,
            digits=3,
            bid=Decimal("2400.201"),
            ask=Decimal("2400.351"),
            last=Decimal("2400.276"),
            source_event_id="tick-2",
        ),
    )


def canonical_bar_fixtures() -> tuple[MarketBar, ...]:
    bar_time = datetime(2026, 1, 2, 2, 0, 0, tzinfo=UTC)
    return (
        MarketBar(
            source="MT5",
            symbol="XAUUSD",
            event_time=bar_time,
            received_at=bar_time + timedelta(seconds=2),
            sequence=10,
            digits=2,
            open_price=Decimal("2400.10"),
            high=Decimal("2401.30"),
            low=Decimal("2399.80"),
            close=Decimal("2401.05"),
            source_event_id="bar-10",
        ),
        MarketBar(
            source="MT5",
            symbol="XAUUSD",
            event_time=bar_time + timedelta(minutes=1),
            received_at=bar_time + timedelta(minutes=1, seconds=2),
            sequence=11,
            digits=2,
            open_price=Decimal("2401.05"),
            high=Decimal("2402.00"),
            low=Decimal("2400.90"),
            close=Decimal("2401.75"),
            source_event_id="bar-11",
        ),
    )


def invalid_tick_fixtures() -> tuple[MarketTick, ...]:
    base_time = datetime(2026, 1, 2, 9, 15, 0, tzinfo=UTC)
    return (
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=base_time,
            received_at=base_time,
            sequence=100,
            digits=3,
            bid=Decimal("NaN"),
            ask=Decimal("2400.100"),
            source_event_id="bad-nan",
        ),
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=base_time,
            received_at=base_time,
            sequence=101,
            digits=3,
            bid=Decimal("-1"),
            ask=Decimal("2400.100"),
            source_event_id="bad-negative",
        ),
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=base_time,
            received_at=base_time,
            sequence=102,
            digits=3,
            bid=Decimal("2400.250"),
            ask=Decimal("2400.100"),
            source_event_id="bad-crossed",
        ),
    )


def gap_backfill_fixtures() -> tuple[tuple[MarketTick, ...], tuple[MarketTick, ...]]:
    base_time = datetime(2026, 1, 2, 9, 20, 0, tzinfo=UTC)
    live = (
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=base_time,
            received_at=base_time,
            sequence=1,
            digits=3,
            bid=Decimal("2401.001"),
            ask=Decimal("2401.151"),
            source_event_id="gap-1",
        ),
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=base_time + timedelta(seconds=1),
            received_at=base_time + timedelta(seconds=1),
            sequence=2,
            digits=3,
            bid=Decimal("2401.101"),
            ask=Decimal("2401.251"),
            source_event_id="gap-2",
        ),
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=base_time + timedelta(seconds=3),
            received_at=base_time + timedelta(seconds=3),
            sequence=4,
            digits=3,
            bid=Decimal("2401.301"),
            ask=Decimal("2401.451"),
            source_event_id="gap-4",
        ),
    )
    backfill = (
        MarketTick(
            source="MT5",
            symbol="XAUUSD",
            event_time=base_time + timedelta(seconds=2),
            received_at=base_time + timedelta(seconds=2),
            sequence=3,
            digits=3,
            bid=Decimal("2401.201"),
            ask=Decimal("2401.351"),
            source_event_id="gap-3",
        ),
    )
    return live, backfill

