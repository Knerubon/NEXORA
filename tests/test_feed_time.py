from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from nexora_api.quotes import FeedError, Mt5Source, Quote, QuoteService, make_quote


def test_explicit_offset_preserves_raw_time_and_default_utc() -> None:
    now = datetime(2026, 9, 18, 10, tzinfo=UTC)
    raw = now + timedelta(hours=3)
    stamp = int(raw.timestamp() * 1000)
    unchanged = make_quote("XAUUSD-STD", 100, 101, 2, stamp, now)
    corrected = make_quote("XAUUSD-STD", 100, 101, 2, stamp, now, time_offset_seconds=10800)
    assert unchanged.event_time == raw
    assert corrected.event_time == now
    assert corrected.raw_event_time == raw
    assert corrected.time_offset_seconds == 10800


@pytest.mark.parametrize("age,status", [(0, "live"), (20, "stale"), (-20, "clock_skew")])
def test_corrected_feed_still_enforces_freshness(age: int, status: str) -> None:
    now = datetime(2026, 9, 18, 10, tzinfo=UTC)
    stamp = int((now + timedelta(hours=3, seconds=-age)).timestamp() * 1000)
    quote = make_quote("XAUUSD-STD", 100, 101, 2, stamp, now, time_offset_seconds=10800)

    class Source:
        def read(self) -> Quote:
            return quote

        def close(self) -> None:
            pass

    service = QuoteService(Source(), "XAUUSD-STD")
    delivered: list[Quote] = []
    service.on_quote = delivered.append
    service.poll(now)
    assert service.snapshot().status == status
    assert len(delivered) == (1 if status == "live" else 0)


def test_dynamic_mt5_symbol_selection_uses_visible_symbol() -> None:
    class FakeTick:
        bid = 100.0
        ask = 101.0
        time_msc = 1_700_000_000_000

    class FakeInfo:
        visible = True
        digits = 2

    class FakeMT5:
        def initialize(self, path: str, timeout: int = 5000) -> bool:
            return True

        def terminal_info(self) -> SimpleNamespace:
            return SimpleNamespace(connected=True)

        def symbols_get(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="EURUSD"), SimpleNamespace(name="XAUUSD")]

        def symbol_info(self, symbol: str) -> FakeInfo | None:
            if symbol == "XAUUSD":
                return FakeInfo()
            return None

        def symbol_info_tick(self, symbol: str) -> FakeTick | None:
            return FakeTick() if symbol == "XAUUSD" else None

        def shutdown(self) -> None:
            return None

    class FakePsutil:
        @staticmethod
        def process_iter(_: list[str]) -> list[SimpleNamespace]:
            return [SimpleNamespace(info={"exe": r"C:\Terminal\terminal64.exe"})]

    fake_module = FakeMT5()

    source = Mt5Source(r"C:\Terminal\terminal64.exe", None)
    with patch(
        "nexora_api.quotes.importlib.import_module",
        side_effect=lambda name: FakePsutil if name == "psutil" else fake_module,
    ):
        quote = source.read()

    assert quote.symbol == "XAUUSD"
    assert quote.bid == "100.00"
    assert quote.ask == "101.00"


def test_invalid_offset_fails_closed() -> None:
    with pytest.raises(ValueError, match="invalid_time_offset"):
        Mt5Source(None, None, time_offset_seconds=999999)
    with pytest.raises(FeedError, match="invalid_time_offset"):
        make_quote("X", 1, 2, 2, 1000, datetime.now(UTC), time_offset_seconds=999999)
