"""Ephemeral observation snapshots. Not an ingestion/replay engine."""

import importlib
import os
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol
from uuid import uuid4

from nexora.market_data import (
    MarketDataQualityMonitor,
    QualityConfig,
    QualitySnapshot,
    QualityStatus,
)
from pydantic import BaseModel

QuoteStatus = Literal[
    "waiting",
    "live",
    "stale",
    "clock_skew",
    "unavailable",
    "disconnected",
    "error",
]


class Quote(BaseModel):
    symbol: str
    bid: str
    ask: str
    spread: str
    digits: int
    event_time: datetime
    received_at: datetime


class Snapshot(BaseModel):
    schema_version: Literal[1] = 1
    stream_id: str
    sequence: int
    status: QuoteStatus
    code: str
    symbol: str | None
    source: Literal["MT5"] = "MT5"
    quote: Quote | None = None


class FeedError(Exception):
    def __init__(self, code: str, status: QuoteStatus = "unavailable") -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class QuoteSource(Protocol):
    def read(self) -> Quote: ...
    def close(self) -> None: ...


def make_quote(
    symbol: str, bid: float, ask: float, digits: int, time_msc: int, received_at: datetime
) -> Quote:
    """Validate the full quote before formatting; never turn invalid prices into live data."""
    if not 0 <= digits <= 10 or time_msc <= 0:
        raise FeedError("invalid_quote", "error")
    try:
        bid_price, ask_price = Decimal(str(bid)), Decimal(str(ask))
        if (
            not bid_price.is_finite()
            or not ask_price.is_finite()
            or bid_price <= 0
            or ask_price < bid_price
        ):
            raise FeedError("invalid_quote", "error")
        quantum = Decimal(1).scaleb(-digits)
        bid_price, ask_price = bid_price.quantize(quantum), ask_price.quantize(quantum)
        if bid_price <= 0:
            raise FeedError("invalid_quote", "error")
        event_time = datetime.fromtimestamp(time_msc / 1000, UTC)
    except (InvalidOperation, ValueError, OverflowError, OSError) as exc:
        raise FeedError("invalid_quote", "error") from exc
    return Quote(
        symbol=symbol,
        bid=str(bid_price),
        ask=str(ask_price),
        spread=str(ask_price - bid_price),
        digits=digits,
        event_time=event_time,
        received_at=received_at,
    )


class Mt5Source:
    """Use only the explicitly configured, already-running terminal and visible symbol."""

    def __init__(self, path: str | None, symbol: str | None) -> None:
        self.path, self.symbol = path, symbol
        self.module: Any = None
        self.initialized = False

    def read(self) -> Quote:
        if not self.path or not self.symbol:
            raise FeedError("not_configured")
        try:
            psutil = importlib.import_module("psutil")
            if self.module is None:
                self.module = importlib.import_module("MetaTrader5")
        except ImportError as exc:
            raise FeedError("adapter_not_installed") from exc
        target = os.path.normcase(os.path.abspath(self.path))
        running = any(
            process.info.get("exe")
            and os.path.normcase(os.path.abspath(process.info["exe"])) == target
            for process in psutil.process_iter(["exe"])
        )
        if not running:
            self.close()
            raise FeedError("terminal_not_running", "disconnected")
        if not self.initialized:
            self.initialized = bool(self.module.initialize(self.path, timeout=5000))
            if not self.initialized:
                raise FeedError("connection_failed", "disconnected")
        terminal = self.module.terminal_info()
        if terminal is None or not terminal.connected:
            self.close()
            raise FeedError("terminal_disconnected", "disconnected")
        info = self.module.symbol_info(self.symbol)
        if info is None:
            raise FeedError("symbol_not_found")
        if not info.visible:
            raise FeedError("symbol_not_visible")
        tick = self.module.symbol_info_tick(self.symbol)
        if tick is None:
            raise FeedError("quote_unavailable")
        return make_quote(
            self.symbol, tick.bid, tick.ask, info.digits, tick.time_msc, datetime.now(UTC)
        )

    def close(self) -> None:
        if self.module is not None and self.initialized:
            self.module.shutdown()
        self.initialized = False


class QuoteService:
    def __init__(self, source: QuoteSource, symbol: str | None) -> None:
        self.on_quote: Callable[[Quote], None] | None = None
        self.source = source
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        quality_symbol = symbol or "unconfigured"
        self.quality_monitor = MarketDataQualityMonitor(QualityConfig(symbol=quality_symbol))
        self.current = Snapshot(
            stream_id=str(uuid4()), sequence=0, status="waiting", code="connecting", symbol=symbol
        )
        self._history: deque[Snapshot] = deque([self.current], maxlen=240)

    def snapshot(self) -> Snapshot:
        with self.lock:
            return self.current.model_copy(deep=True)

    def quality_snapshot(self) -> QualitySnapshot:
        with self.lock:
            return self.quality_monitor.snapshot()

    def history(self, *, limit: int = 120) -> tuple[Snapshot, ...]:
        with self.lock:
            bounded = max(1, min(limit, len(self._history)))
            return tuple(item.model_copy(deep=True) for item in tuple(self._history)[-bounded:])

    def poll(self, now: datetime | None = None) -> None:
        observed_at = now or datetime.now(UTC)
        try:
            quote = self.source.read()
            age = (observed_at - quote.event_time).total_seconds()
            status: QuoteStatus = "live"
            if age > 10:
                status = "stale"
            elif age < -5:
                status = "clock_skew"
            code: str = "ok" if status == "live" else status
            quality_status = _to_quality_status(status)
        except FeedError as exc:
            quote, status, code = None, exc.status, exc.code
            quality_status = _to_quality_status(status)
        except Exception:
            # Do not log/serialize raw adapter exceptions (may contain local/account data).
            quote, status, code = None, "error", "adapter_error"
            quality_status = "error"
        with self.lock:
            self.quality_monitor.observe_transport(
                status=quality_status,
                code=code,
                observed_at=observed_at,
            )
            self.current = Snapshot(
                stream_id=self.current.stream_id,
                sequence=self.current.sequence + 1,
                status=status,
                code=code,
                symbol=self.current.symbol,
                quote=quote,
            )
            self._history.append(self.current)
        if quote is not None and status == "live" and self.on_quote is not None:
            self.on_quote(quote)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="nexora-quotes", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            while not self.stop_event.is_set():
                self.poll()
                self.stop_event.wait(1)
        finally:
            self.source.close()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=12)


def configured_service() -> QuoteService:
    symbol = os.environ.get("NEXORA_MT5_SYMBOL")
    return QuoteService(Mt5Source(os.environ.get("NEXORA_MT5_PATH"), symbol), symbol)


def _to_quality_status(status: QuoteStatus) -> QualityStatus:
    if status == "waiting":
        return "unknown"
    return status
