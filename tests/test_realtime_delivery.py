"""Quote delivery is independent of slow research and repeated market ticks."""

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nexora.storage import SQLiteJournal
from nexora_api.main import create_app
from nexora_api.quotes import Quote, QuoteService
from nexora_api.research import observe_quote


class LiveSource:
    def __init__(self) -> None:
        self.calls = 0

    def read(self) -> Quote:
        self.calls += 1
        now = datetime.now(UTC)
        return Quote(
            symbol="XAUUSD",
            bid=str(2000 + self.calls),
            ask=str(2001 + self.calls),
            spread="1",
            digits=0,
            event_time=now,
            received_at=now,
        )

    def close(self) -> None:
        pass


def test_single_reader_keeps_latest_quote_during_blocked_observer() -> None:
    source = LiveSource()
    feed = QuoteService(source, "XAUUSD")
    entered, release = threading.Event(), threading.Event()

    def slow_observer(quote: Quote) -> None:
        entered.set()
        release.wait(5)

    feed.on_quote = slow_observer
    feed.start()
    reader = feed.thread
    feed.start()
    try:
        assert reader is feed.thread
        assert entered.wait(2)
        initial = feed.snapshot()
        time.sleep(0.65)
        current = feed.snapshot()
        assert current.sequence >= initial.sequence + 2
        assert current.quote and initial.quote
        assert current.quote.event_time > initial.quote.event_time
        assert current.quote.bid == str(2000 + source.calls)
    finally:
        release.set()
        feed.stop()
    assert reader is not None and not reader.is_alive()
    assert feed.observer_thread is not None and not feed.observer_thread.is_alive()


def test_ws_delivers_within_one_second_while_state_publisher_is_blocked() -> None:
    feed = QuoteService(LiveSource(), "XAUUSD")
    journal = SQLiteJournal(":memory:")
    app = create_app(feed, journal=journal)
    entered, release = threading.Event(), threading.Event()
    with TestClient(app, base_url="http://localhost") as client:
        original = app.state.backtest.store.list_runs

        def blocked() -> Any:
            entered.set()
            release.wait(5)
            return original()

        # Patch the store, since the service dataclass is slotted.
        app.state.backtest.store.list_runs = blocked
        try:
            with client.websocket_connect(
                "/ws/events", headers={"origin": "http://localhost:3000"}
            ) as ws:
                assert ws.receive_json()["event_type"] == "state_snapshot"
                assert entered.wait(2)
                received: list[tuple[float, dict[str, Any]]] = []
                for _ in range(12):
                    event = ws.receive_json()
                    if event["event_type"] == "quote_snapshot":
                        received.append((time.monotonic(), event))
                assert len(received) >= 10
                assert all(b[0] - a[0] < 1 for a, b in zip(received, received[1:], strict=False))
                assert received[-1][1]["sequence"] > received[0][1]["sequence"]
                assert app.state.active_sockets == 1
                last = received[-1][1]["payload"]
                assert last["quote"]["bid"] == str(2000 + last["sequence"])
        finally:
            release.set()
    journal.close()


def test_invalid_chronology_never_enters_engine_recovery() -> None:
    class Runtime:
        def events(self) -> Any:
            raise AssertionError("future quote must be rejected before engine access")

    quote = LiveSource().read()
    quote.event_time += timedelta(milliseconds=200)
    runtime: Any = Runtime()
    observe_quote(runtime, quote)
    assert not hasattr(runtime, "error")


@pytest.mark.parametrize("future_ms", [0, 300])
def test_repeated_quote_is_not_a_new_research_event_and_still_becomes_stale(
    future_ms: int,
) -> None:
    quote = LiveSource().read()
    quote.event_time += timedelta(milliseconds=future_ms)

    class RepeatedSource:
        def read(self) -> Quote:
            return quote.model_copy(update={"received_at": datetime.now(UTC)})

        def close(self) -> None:
            pass

    feed = QuoteService(RepeatedSource(), "XAUUSD")
    delivered: list[Quote] = []
    feed.on_quote = delivered.append
    feed.start()
    try:
        time.sleep(1.25)
        assert len(delivered) == 1
        assert feed.snapshot().sequence >= 5
        assert feed.snapshot().quote is not None
        assert feed.snapshot().quote.event_time == quote.event_time  # type: ignore[union-attr]
    finally:
        feed.stop()
    feed.poll(now=quote.event_time + timedelta(seconds=11))
    assert feed.snapshot().status == "stale"
    assert len(delivered) == 1
