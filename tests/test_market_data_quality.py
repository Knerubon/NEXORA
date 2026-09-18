from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nexora.market_data import (
    MarketDataQualityMonitor,
    NormalizedPriceEvent,
    QualityConfig,
    QualitySnapshotStore,
)


def _event(
    *,
    sequence: int,
    event_time: datetime,
    received_at: datetime,
    is_duplicate: bool = False,
    is_out_of_order: bool = False,
    is_gap: bool = False,
) -> NormalizedPriceEvent:
    return NormalizedPriceEvent(
        schema_version=1,
        identity_key=f"evt-{sequence}-{event_time.isoformat()}",
        source="MT5",
        symbol="XAUUSD",
        kind="tick",
        event_time=event_time,
        received_at=received_at,
        source_sequence=sequence,
        source_order=sequence,
        source_event_id=f"event-{sequence}",
        price_source="ask",
        units="USD/oz",
        precision=1,
        price=Decimal("100.0"),
        bid=Decimal("99.9"),
        ask=Decimal("100.0"),
        is_duplicate=is_duplicate,
        is_out_of_order=is_out_of_order,
        is_gap=is_gap,
    )


def test_quality_monitor_tracks_gap_duplicate_and_out_of_order_counters() -> None:
    monitor = MarketDataQualityMonitor(QualityConfig(symbol="XAUUSD", session_start_hour_utc=0))
    base_time = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)

    monitor.observe_event(
        _event(
            sequence=1,
            event_time=base_time,
            received_at=base_time + timedelta(milliseconds=100),
        ),
        observed_at=base_time + timedelta(milliseconds=120),
    )
    monitor.observe_event(
        _event(
            sequence=3,
            event_time=base_time + timedelta(seconds=1),
            received_at=base_time + timedelta(seconds=1, milliseconds=80),
            is_gap=True,
        ),
        observed_at=base_time + timedelta(seconds=1, milliseconds=120),
    )
    snapshot = monitor.observe_event(
        _event(
            sequence=3,
            event_time=base_time + timedelta(seconds=1),
            received_at=base_time + timedelta(seconds=1, milliseconds=80),
            is_duplicate=True,
            is_out_of_order=True,
        ),
        observed_at=base_time + timedelta(seconds=1, milliseconds=140),
    )

    assert snapshot.counters.observed == 3
    assert snapshot.counters.gaps == 1
    assert snapshot.counters.duplicates == 1
    assert snapshot.counters.out_of_order == 1


def test_quality_monitor_flags_clock_skew_stale_and_market_closed() -> None:
    monitor = MarketDataQualityMonitor(
        QualityConfig(
            symbol="XAUUSD",
            freshness_threshold_seconds=10,
            clock_skew_threshold_seconds=5,
            session_start_hour_utc=8,
            session_end_hour_utc=17,
        )
    )
    day = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

    skew = monitor.observe_event(
        _event(
            sequence=1,
            event_time=day + timedelta(seconds=10),
            received_at=day + timedelta(seconds=10),
        ),
        observed_at=day,
    )
    assert skew.status == "clock_skew"

    stale = monitor.observe_event(
        _event(sequence=2, event_time=day, received_at=day),
        observed_at=day + timedelta(seconds=20),
    )
    assert stale.status == "stale"
    assert stale.code == "stale_event"

    closed = monitor.observe_event(
        _event(
            sequence=3,
            event_time=datetime(2026, 3, 2, 20, 0, tzinfo=UTC),
            received_at=datetime(2026, 3, 2, 20, 0, tzinfo=UTC),
        ),
        observed_at=datetime(2026, 3, 2, 20, 0, 2, tzinfo=UTC),
    )
    assert closed.status == "market_closed"
    assert closed.completeness == "unknown"


def test_quality_monitor_tracks_disconnect_and_reconnect() -> None:
    monitor = MarketDataQualityMonitor(QualityConfig(symbol="XAUUSD"))
    monitor.observe_transport(status="disconnected", code="terminal_not_running")
    snapshot = monitor.observe_transport(status="live", code="ok")

    assert snapshot.counters.disconnects == 1
    assert snapshot.counters.reconnects == 1


def test_quality_snapshot_store_replay_and_rebuild_are_deterministic() -> None:
    monitor = MarketDataQualityMonitor(QualityConfig(symbol="XAUUSD"))
    store = QualitySnapshotStore()
    first = monitor.observe_transport(status="disconnected", code="terminal_not_running")
    second = monitor.observe_transport(status="live", code="ok")
    store.append(first)
    store.append(second)

    assert store.replay() == store.rebuild()
    assert store.replay()[1]["status"] == "live"
