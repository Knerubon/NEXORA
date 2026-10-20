"""Mocks. No real terminal, no MetaTrader5 import, no network."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tests.windows_integration_readiness.vectors import DOCUMENTED_TICK_DESCR


class _Dtype:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names


class FakeTickRow:
    """Mimics one numpy structured row: row["field"] and row.dtype.names (the only access the
    extractor uses). Used when numpy is not installed (it is not in the locked cloud env)."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._v = values
        self.dtype = _Dtype(tuple(values))

    def __getitem__(self, key: str) -> Any:
        return self._v[key]


def make_fake_rows(
    ticks: Sequence[tuple[int, float, float, int]], *, drop_fields: tuple[str, ...] = ()
) -> list[FakeTickRow]:
    rows = []
    for t_msc, bid, ask, flags in ticks:
        full = {
            "time": t_msc // 1000,
            "bid": bid,
            "ask": ask,
            "last": 0.0,
            "volume": 0,
            "time_msc": t_msc,
            "flags": flags,
            "volume_real": 0.0,
        }
        assert tuple(full) == tuple(n for n, _ in DOCUMENTED_TICK_DESCR)
        rows.append(FakeTickRow({k: v for k, v in full.items() if k not in drop_fields}))
    return rows


class FakeMt5Module:
    """Fake terminal module. Records every attribute read, so a test can prove the set of names
    touched. Trade/account names exist on it as tripwires: touching one is recorded AND fails.
    No method here transmits anything."""

    COPY_TICKS_ALL = -1
    TRIPWIRES = frozenset(
        {
            "order_send",
            "order_check",
            "positions_get",
            "orders_get",
            "history_deals_get",
            "login",
            "account_info",
        }
    )

    def __init__(self, rows: Sequence[Any] | None = None, *, init_ok: bool = True) -> None:
        object.__setattr__(self, "_log", [])
        object.__setattr__(self, "_rows", rows)
        object.__setattr__(self, "_init_ok", init_ok)
        object.__setattr__(self, "calls", [])

    @property
    def accessed(self) -> list[str]:
        return list(self._log)

    def __getattribute__(self, name: str) -> Any:
        if not name.startswith("_") and name not in ("accessed", "calls", "TRIPWIRES"):
            object.__getattribute__(self, "_log").append(name)
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:  # only reached for unknown names
        if name in FakeMt5Module.TRIPWIRES:
            raise AssertionError(f"tripwire touched: {name}")
        raise AttributeError(name)

    def initialize(self, *args: Any, **kwargs: Any) -> bool:
        self.calls.append(("initialize", args, tuple(sorted(kwargs))))
        return bool(self._init_ok)

    def shutdown(self) -> None:
        self.calls.append(("shutdown", (), ()))

    def last_error(self) -> tuple[int, str]:
        return (1, "Success") if self._init_ok else (-10005, "IPC timeout")

    def version(self) -> tuple[int, int, str]:
        return (500, 0, "SYNTHETIC")

    def terminal_info(self) -> None:
        return None

    def symbol_info(self, symbol: str) -> None:
        return None

    def copy_ticks_range(self, symbol: str, a: Any, b: Any, flags: int) -> Sequence[Any] | None:
        self.calls.append(("copy_ticks_range", (symbol,), ()))
        rows: Sequence[Any] | None = self._rows
        return rows


class RecordingModuleProxy:
    """Wrap ANY module (including the real MetaTrader5 on the Windows node) and record each
    attribute name read through it. Used to turn 'read-only' from a claim into an observed set."""

    def __init__(self, module: Any) -> None:
        object.__setattr__(self, "_m", module)
        object.__setattr__(self, "names", [])

    def __getattr__(self, name: str) -> Any:
        self.names.append(name)
        return getattr(self._m, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("proxy is immutable")
