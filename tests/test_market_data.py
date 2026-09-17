from __future__ import annotations

from datetime import UTC, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from nexora.market_data import (
    FakeMarketDataAdapter,
    MarketDataError,
    MarketDataNormalizer,
    MarketDataPolicy,
    MarketTick,
    Mt5ReadOnlyMarketDataAdapter,
    ReplayReader,
    SQLiteMarketDataRepository,
    canonical_bar_fixtures,
    canonical_tick_fixtures,
    gap_backfill_fixtures,
    invalid_tick_fixtures,
    semantic_fingerprint,
)


def _policy() -> MarketDataPolicy:
    return MarketDataPolicy(tick_price_source="ask", bar_price_source="close", units="USD/oz")


def test_normalizes_tick_and_bar_with_explicit_price_sources_and_utc() -> None:
    normalizer = MarketDataNormalizer(_policy())
    tick = canonical_tick_fixtures()[0]
    tick_event = normalizer.process_tick(tick)
    assert tick_event.price == tick.ask
    assert tick_event.precision == 3
    assert tick_event.units == "USD/oz"
    assert tick_event.event_time.tzinfo == UTC
    assert tick_event.event_time == tick.event_time.astimezone(UTC)
    assert tick_event.received_at == tick.received_at.astimezone(UTC)

    bar = canonical_bar_fixtures()[0]
    bar_event = normalizer.process_bar(bar)
    assert bar_event.price == bar.close
    assert bar_event.precision == 2
    assert bar_event.price_source == "close"
    assert bar_event.event_time.tzinfo == UTC


@pytest.mark.parametrize("tick", invalid_tick_fixtures())
def test_invalid_tick_prices_are_rejected(tick: object) -> None:
    normalizer = MarketDataNormalizer(_policy())
    with pytest.raises(MarketDataError):
        normalizer.process_tick(tick)  # type: ignore[arg-type]


def test_duplicate_out_of_order_and_gap_flags_are_marked() -> None:
    normalizer = MarketDataNormalizer(_policy())
    first, second = canonical_tick_fixtures()

    accepted = normalizer.process_tick(first)
    duplicate = normalizer.process_tick(first)
    out_of_order = normalizer.process_tick(
        type(first)(
            source=second.source,
            symbol=second.symbol,
            event_time=second.event_time - timedelta(seconds=2),
            received_at=second.received_at,
            sequence=0,
            digits=second.digits,
            bid=second.bid,
            ask=second.ask,
            last=second.last,
            source_event_id="tick-0",
        )
    )

    assert accepted.is_duplicate is False
    assert duplicate.is_duplicate is True
    assert out_of_order.is_out_of_order is True


def test_fake_adapter_reconnect_and_backfill_are_offline_and_ordered() -> None:
    live, backfill = gap_backfill_fixtures()
    adapter = FakeMarketDataAdapter(list(live), list(backfill))
    adapter.connect()
    first = adapter.read()
    second = adapter.read()
    adapter.disconnect()
    adapter.connect()
    recovered = adapter.backfill(3, 3)
    third = adapter.read()

    assert [
        observation.sequence
        for observation in (first, second, *recovered, third)
    ] == [1, 2, 3, 4]


def test_repository_round_trip_and_replay_are_deterministic() -> None:
    with TemporaryDirectory() as tmp:
        repo_path = Path(tmp) / "market-data.sqlite3"
        with SQLiteMarketDataRepository(repo_path) as repo:
            normalizer = MarketDataNormalizer(_policy())
            first, second = canonical_tick_fixtures()

            late_first = normalizer.process_tick(second)
            repo.record(second, late_first)

            early_first = normalizer.process_tick(first)
            repo.record(first, early_first)

            duplicate_first = normalizer.process_tick(first)
            repo.record(first, duplicate_first)

            assert repo.last_sequence("MT5", "XAUUSD") == 2
            assert len(list(repo.iter_raw(source="MT5", symbol="XAUUSD"))) == 3
            assert len(list(repo.iter_normalized(source="MT5", symbol="XAUUSD"))) == 2

        with SQLiteMarketDataRepository(repo_path) as reopened:
            replay = ReplayReader(reopened)
            fingerprints = replay.fingerprints(source="MT5", symbol="XAUUSD")
            assert fingerprints == sorted(fingerprints, key=lambda item: item[5])
            expected = [
                semantic_fingerprint(early_first),
                semantic_fingerprint(late_first),
            ]
            assert fingerprints == expected


def test_mt5_adapter_is_read_only_and_uses_only_quote_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _FakeProcess:
        def __init__(self, exe: str) -> None:
            self.info = {"exe": exe}

    class _FakePsutil:
        @staticmethod
        def process_iter(fields: list[str]) -> list[_FakeProcess]:
            return [_FakeProcess(r"C:\\Terminal\\terminal64.exe")]

    class _FakeTerminalInfo:
        connected = True

    class _FakeSymbolInfo:
        digits = 3
        visible = True

    class _FakeTick:
        bid = 2400.111
        ask = 2400.333
        last = 2400.222
        time_msc = 1_700_000_000_000

    class _FakeMT5:
        @staticmethod
        def initialize(path: str, timeout: int = 5000) -> bool:
            calls.append("initialize")
            return True

        @staticmethod
        def terminal_info() -> _FakeTerminalInfo:
            calls.append("terminal_info")
            return _FakeTerminalInfo()

        @staticmethod
        def symbol_info(symbol: str) -> _FakeSymbolInfo:
            calls.append("symbol_info")
            return _FakeSymbolInfo()

        @staticmethod
        def symbol_info_tick(symbol: str) -> _FakeTick:
            calls.append("symbol_info_tick")
            return _FakeTick()

        @staticmethod
        def shutdown() -> None:
            calls.append("shutdown")

        @staticmethod
        def order_send(*args: object, **kwargs: object) -> None:
            raise AssertionError("order_send must not be called")

        @staticmethod
        def order_check(*args: object, **kwargs: object) -> None:
            raise AssertionError("order_check must not be called")

    def fake_import_module(name: str) -> object:
        if name == "psutil":
            return _FakePsutil()
        if name == "MetaTrader5":
            return _FakeMT5()
        raise ImportError(name)

    import nexora.market_data.adapters as adapters_module

    monkeypatch.setattr(adapters_module, "_import_module", fake_import_module)

    adapter = Mt5ReadOnlyMarketDataAdapter(
        terminal_path=r"C:\\Terminal\\terminal64.exe", symbol="XAUUSD"
    )
    observation = adapter.read()
    adapter.disconnect()

    assert isinstance(observation, MarketTick)
    assert observation.symbol == "XAUUSD"
    assert observation.event_time.tzinfo == UTC
    assert observation.ask > observation.bid
    assert calls == ["initialize", "terminal_info", "symbol_info", "symbol_info_tick", "shutdown"]
