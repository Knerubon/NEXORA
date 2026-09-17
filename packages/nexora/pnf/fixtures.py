"""Synthetic fixtures for P&F golden tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexora.market_data.models import NormalizedPriceEvent


def _event(sequence: int, price: str, *, symbol: str = "XAUUSD") -> NormalizedPriceEvent:
    event_time = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=sequence)
    return NormalizedPriceEvent(
        schema_version=1,
        identity_key=f"{symbol}:ask:{sequence}",
        source="MT5",
        symbol=symbol,
        kind="tick",
        event_time=event_time,
        received_at=event_time + timedelta(milliseconds=100),
        source_sequence=sequence,
        source_order=sequence,
        source_event_id=f"evt-{symbol}-{sequence}",
        price_source="ask",
        units="USD/oz",
        precision=1,
        price=Decimal(price),
        bid=Decimal(price) - Decimal("0.1"),
        ask=Decimal(price),
    )


def flat_fixture() -> tuple[NormalizedPriceEvent, ...]:
    return (
        _event(1, "100.0"),
        _event(2, "100.4"),
        _event(3, "100.8"),
    )


def monotonic_rise_fall_fixture() -> tuple[NormalizedPriceEvent, ...]:
    return (
        _event(1, "100.0"),
        _event(2, "101.1"),
        _event(3, "102.2"),
        _event(4, "103.2"),
        _event(5, "102.8"),
        _event(6, "101.0"),
    )


def exact_threshold_fixture() -> tuple[NormalizedPriceEvent, ...]:
    return (
        _event(1, "100.0"),
        _event(2, "101.0"),
        _event(3, "102.0"),
    )


def reversal_boundary_fixture() -> tuple[NormalizedPriceEvent, ...]:
    return (
        _event(1, "100.0"),
        _event(2, "103.0"),
        _event(3, "101.0"),
    )


def multi_box_gap_fixture() -> tuple[NormalizedPriceEvent, ...]:
    return (
        _event(1, "100.0"),
        _event(2, "104.0"),
        _event(3, "97.0"),
    )
