"""Read-only tick sources. The MT5 provider can only reach an explicit allowlist of read calls."""

from __future__ import annotations

import importlib
import weakref
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from nexora.market_history.models import (
    CapabilityProfile,
    HistoryError,
    InstrumentSpec,
    MetaField,
    RawTick,
    SourceDescriptor,
)


class TransientSourceError(RuntimeError):
    """Retryable source failure (terminal busy, history not yet synchronised)."""


class HistoricalTickSource(Protocol):
    def descriptor(self) -> SourceDescriptor: ...
    def capability_profile(self) -> CapabilityProfile: ...
    def instrument(self, symbol: str) -> InstrumentSpec: ...
    def ticks(self, symbol: str, from_raw_msc: int, to_raw_msc: int) -> Sequence[RawTick]:
        """Ticks in the half-open RAW-clock window [from_raw_msc, to_raw_msc)."""
        ...


# The ONLY attributes of the MT5 module this package may touch. Anything trade-related
# (order_send, order_check, positions_*, orders_*, history_deals_*, login, ...) is refused.
ALLOWED_MT5_CALLS = frozenset(
    {
        "initialize",
        "shutdown",
        "last_error",
        "version",
        "terminal_info",
        "symbol_info",
        "copy_ticks_range",
        "COPY_TICKS_ALL",
    }
)

_TERMINAL_FIELDS = ("company", "name", "build", "connected", "community_connection")
_SYMBOL_FIELDS = (
    "digits",
    "point",
    "trade_tick_size",
    "trade_tick_value",
    "trade_tick_value_profit",
    "trade_tick_value_loss",
    "trade_contract_size",
    "volume_min",
    "volume_max",
    "volume_step",
    "currency_base",
    "currency_profit",
    "currency_margin",
    "swap_mode",
    "swap_long",
    "swap_short",
    "swap_rollover3days",
    "trade_calc_mode",
    "trade_stops_level",
    "trade_freeze_level",
)


_MODULES: weakref.WeakKeyDictionary[ReadOnlyMt5, Any] = weakref.WeakKeyDictionary()


class ReadOnlyMt5:
    """Attribute gate around an MT5-compatible module.

    The wrapped module is kept in a module-private registry, not on the instance, so the gate
    exposes no attribute that returns the raw module."""

    __slots__ = ("__weakref__",)

    def __init__(self, module: Any) -> None:
        _MODULES[self] = module

    def __getattr__(self, name: str) -> Any:
        if name not in ALLOWED_MT5_CALLS:
            raise HistoryError("mt5_call_not_allowlisted", name)
        target = getattr(_MODULES[self], name)
        if name == "initialize":
            # Attach to the already-running terminal only: no path/login/password/server.
            def initialize_no_credentials() -> Any:
                return target()

            return initialize_no_credentials
        return target

    def __setattr__(self, name: str, value: Any) -> None:
        raise HistoryError("read_only_gate_is_immutable")


def _field(name: str, obj: Any) -> MetaField:
    value = getattr(obj, name, None)
    if value is None:
        return MetaField(name, "unavailable")
    if isinstance(value, float):
        return MetaField(name, "known", str(Decimal(repr(value))))
    return MetaField(name, "known", str(value))


class Mt5ReadOnlyHistorySource:
    """MT5 history via an injected module (tests) or a lazily imported `MetaTrader5`.

    No credentials are accepted: `initialize()` attaches to the already-running, operator-logged-in
    terminal. WINDOWS-INTEGRATION only; operator-started."""

    def __init__(self, module: Any | None = None) -> None:
        raw = module if module is not None else importlib.import_module("MetaTrader5")
        self._mt5 = ReadOnlyMt5(raw)
        if not self._mt5.initialize():
            raise TransientSourceError(f"mt5_initialize_failed:{self._mt5.last_error()}")

    def close(self) -> None:
        self._mt5.shutdown()

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor("mt5-readonly-history", str(self._mt5.version()))

    def capability_profile(self) -> CapabilityProfile:
        info = self._mt5.terminal_info()
        if info is None:
            return CapabilityProfile(tuple(MetaField(n, "unavailable") for n in _TERMINAL_FIELDS))
        return CapabilityProfile(tuple(_field(n, info) for n in _TERMINAL_FIELDS))

    def instrument(self, symbol: str) -> InstrumentSpec:
        info = self._mt5.symbol_info(symbol)
        if info is None:
            raise HistoryError("symbol_not_found", symbol)
        fields = [_field(n, info) for n in _SYMBOL_FIELDS]
        # Commission is an account/contract-level attribute that symbol_info does not expose.
        fields.append(MetaField("commission", "unavailable"))
        return InstrumentSpec(symbol, tuple(fields))

    def ticks(self, symbol: str, from_raw_msc: int, to_raw_msc: int) -> Sequence[RawTick]:
        # The MT5 API treats datetimes as UTC epoch seconds; here the integers are RAW broker-clock
        # values, so they are passed through unchanged (no offset is applied at retrieval).
        start = datetime.fromtimestamp(from_raw_msc // 1000, tz=UTC)
        end = datetime.fromtimestamp(-(-to_raw_msc // 1000), tz=UTC)
        rows = self._mt5.copy_ticks_range(symbol, start, end, self._mt5.COPY_TICKS_ALL)
        if rows is None:
            raise TransientSourceError(f"copy_ticks_failed:{self._mt5.last_error()}")
        out: list[RawTick] = []
        for r in rows:
            names = getattr(getattr(r, "dtype", None), "names", None) or ()
            out.append(
                RawTick(
                    time_msc=int(r["time_msc"]),
                    bid=Decimal(repr(float(r["bid"]))),
                    ask=Decimal(repr(float(r["ask"]))),
                    flags=int(r["flags"]) if "flags" in names else None,
                )
            )
        return out
