"""Read-only market-data adapters."""

from __future__ import annotations

import importlib
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from nexora.market_data.models import (
    MarketBar,
    MarketDataError,
    MarketTick,
    require_positive_decimal,
)

MarketObservation = MarketTick | MarketBar

_import_module = importlib.import_module


class _PsutilProcess(Protocol):
    info: dict[str, str | None]


class _PsutilModule(Protocol):
    def process_iter(self, fields: Sequence[str]) -> Sequence[_PsutilProcess]: ...


class _Mt5TerminalInfo(Protocol):
    connected: bool


class _Mt5SymbolInfo(Protocol):
    digits: int
    visible: bool


class _Mt5Tick(Protocol):
    bid: Decimal | float | int
    ask: Decimal | float | int
    last: Decimal | float | int | None
    time_msc: int


class _Mt5Module(Protocol):
    def initialize(self, path: str, timeout: int = ...) -> bool: ...

    def terminal_info(self) -> _Mt5TerminalInfo | None: ...

    def symbol_info(self, symbol: str) -> _Mt5SymbolInfo | None: ...

    def symbol_info_tick(self, symbol: str) -> _Mt5Tick | None: ...

    def shutdown(self) -> None: ...


@runtime_checkable
class MarketDataAdapter(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def read(self) -> MarketObservation: ...

    def backfill(
        self, start_sequence: int, end_sequence: int | None = None
    ) -> Sequence[MarketObservation]: ...


@dataclass(slots=True)
class FakeMarketDataAdapter:
    """Scripted adapter for offline tests and replay gaps."""

    observations: list[MarketObservation]
    backfill_observations: list[MarketObservation] | None = None
    connected: bool = False
    _cursor: int = 0

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def read(self) -> MarketObservation:
        if not self.connected:
            raise MarketDataError("disconnected")
        if self._cursor >= len(self.observations):
            raise MarketDataError("end_of_stream")
        observation = self.observations[self._cursor]
        self._cursor += 1
        return observation

    def backfill(
        self, start_sequence: int, end_sequence: int | None = None
    ) -> Sequence[MarketObservation]:
        source = self.backfill_observations or self.observations
        returned: list[MarketObservation] = []
        for observation in source:
            sequence = observation.sequence
            if sequence < start_sequence:
                continue
            if end_sequence is not None and sequence > end_sequence:
                continue
            returned.append(observation)
        return sorted(
            returned, key=lambda observation: observation.sequence
        )


@dataclass(slots=True)
class Mt5ReadOnlyMarketDataAdapter:
    """Read MT5 tick data without any order-capable operations."""

    terminal_path: str
    symbol: str
    connected: bool = False
    _psutil: _PsutilModule | None = field(default=None, init=False, repr=False)
    _mt5: _Mt5Module | None = field(default=None, init=False, repr=False)

    def connect(self) -> None:
        if not self.terminal_path or not self.symbol:
            raise MarketDataError("not_configured")
        try:
            psutil = _import_module("psutil")
            mt5 = _import_module("MetaTrader5")
        except ImportError as exc:
            raise MarketDataError("adapter_not_installed") from exc
        self._psutil = psutil
        self._mt5 = mt5
        if not self._terminal_is_running():
            raise MarketDataError("terminal_not_running")
        if not mt5.initialize(self.terminal_path, timeout=5000):
            raise MarketDataError("connection_failed")
        terminal = mt5.terminal_info()
        if terminal is None or not terminal.connected:
            self.disconnect()
            raise MarketDataError("terminal_disconnected")
        self.connected = True

    def disconnect(self) -> None:
        mt5 = self._mt5
        if mt5 is not None and self.connected:
            mt5.shutdown()
        self.connected = False

    def read(self) -> MarketObservation:
        if not self.connected:
            self.connect()
        mt5 = self._require_mt5()
        info = mt5.symbol_info(self.symbol)
        if info is None:
            raise MarketDataError("symbol_not_found")
        if not info.visible:
            raise MarketDataError("symbol_not_visible")
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            raise MarketDataError("quote_unavailable")
        event_time = datetime.fromtimestamp(tick.time_msc / 1000, UTC)
        received_at = datetime.now(UTC)
        bid = require_positive_decimal(tick.bid, "bid")
        ask = require_positive_decimal(tick.ask, "ask")
        last = None
        last_value = tick.last
        if last_value is not None:
            last = require_positive_decimal(last_value, "last")
        return MarketTick(
            source="MT5",
            symbol=self.symbol,
            event_time=event_time,
            received_at=received_at,
            sequence=int(tick.time_msc),
            digits=int(info.digits),
            bid=bid,
            ask=ask,
            last=last,
            source_event_id=f"mt5:{self.symbol}:{int(tick.time_msc)}",
        )

    def backfill(
        self, start_sequence: int, end_sequence: int | None = None
    ) -> Sequence[MarketObservation]:
        raise MarketDataError("backfill_unavailable")

    def _terminal_is_running(self) -> bool:
        psutil = self._psutil
        if psutil is None:
            return False
        target = os.path.normcase(os.path.abspath(self.terminal_path))
        for process in psutil.process_iter(["exe"]):
            exe = process.info.get("exe")
            if exe and os.path.normcase(os.path.abspath(exe)) == target:
                return True
        return False

    def _require_mt5(self) -> _Mt5Module:
        mt5 = self._mt5
        if mt5 is None:
            raise MarketDataError("adapter_not_ready")
        return mt5


__all__ = [
    "FakeMarketDataAdapter",
    "MarketDataAdapter",
    "MarketDataError",
    "MarketObservation",
    "Mt5ReadOnlyMarketDataAdapter",
]
