"""Mocks. No real terminal, no MetaTrader5 import, no network."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tests.windows_integration_readiness.vectors import DOCUMENTED_TICK_DESCR, FORBIDDEN_MT5_NAMES

# Every tripwire touch is appended here as well as raised, so a broad `except` (even
# `except BaseException`) in code under test cannot hide it: the harness conftest fails any test
# that leaves an entry behind.
TRIPWIRE_LOG: list[str] = []

# Keyword names whose VALUES are never recorded (only the fact that they were passed).
CREDENTIAL_KWARGS = frozenset({"login", "password", "server", "path", "timeout", "portable"})


class TripwireTouched(BaseException):  # noqa: N818 - BaseException so `except Exception` misses it
    """Raised when a forbidden terminal name is touched. Not an Exception subclass."""


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
    touched. EVERY forbidden name is a tripwire: touching one is logged in TRIPWIRE_LOG and raises
    TripwireTouched (a BaseException). No method here transmits anything.

    `copy_ticks_range` honours the requested half-open window [a, b) on `time_msc` (a and b are raw
    milliseconds as ints) and can follow a per-call `script` to model terminal failures:
    "ok", "none" (returns None), "truncate:N" (only the first N rows), "repeat_prev" (returns the
    previous call's rows again, a repeated page), "overlap:MS" (window widened by MS on both sides).
    """

    COPY_TICKS_ALL = -1
    TRIPWIRES = frozenset(FORBIDDEN_MT5_NAMES)

    def __init__(
        self,
        rows: Sequence[Any] | None = None,
        *,
        init_ok: bool = True,
        script: Sequence[str] = (),
    ) -> None:
        object.__setattr__(self, "_log", [])
        object.__setattr__(self, "_rows", rows)
        object.__setattr__(self, "_init_ok", init_ok)
        object.__setattr__(self, "_script", list(script))
        object.__setattr__(self, "_prev", None)
        object.__setattr__(self, "calls", [])

    @property
    def accessed(self) -> list[str]:
        return list(self._log)

    def __getattribute__(self, name: str) -> Any:
        if name in FakeMt5Module.TRIPWIRES:
            TRIPWIRE_LOG.append(name)
            object.__getattribute__(self, "_log").append(name)
            raise TripwireTouched(f"tripwire touched: {name}")
        if not name.startswith("_") and name not in ("accessed", "calls", "TRIPWIRES"):
            object.__getattribute__(self, "_log").append(name)
        return object.__getattribute__(self, name)

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
        self.calls.append(("copy_ticks_range", (symbol, a, b), ()))
        step = self._script.pop(0) if self._script else "ok"
        if step == "none" or self._rows is None:
            return None
        if step == "repeat_prev" and self._prev is not None:
            return list(self._prev)
        lo, hi = a, b
        if step.startswith("overlap:"):
            pad = int(step.split(":", 1)[1])
            lo, hi = a - pad, b + pad
        if isinstance(lo, int) and isinstance(hi, int):
            rows = [r for r in self._rows if lo <= r["time_msc"] < hi]
        else:  # unbounded call (layout tests): the whole fixture
            rows = list(self._rows)
        if step.startswith("truncate:"):
            rows = rows[: int(step.split(":", 1)[1])]
        object.__setattr__(self, "_prev", rows)
        return rows


class RecordingModuleProxy:
    """Wrap a module and record each attribute name read through it (`names`) and each call made
    through it (`calls`: name, positional argument types, keyword names). Credential-like keyword
    VALUES are never recorded; `credential_kwargs_seen` says whether any were passed.

    Intended for the mock half. Wrapping the real MetaTrader5 module on the Windows node is NOT a
    supported step: it would need unreviewed operator code (DEP-03, checklist)."""

    def __init__(self, module: Any) -> None:
        object.__setattr__(self, "_target", module)
        object.__setattr__(self, "names", [])
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "credential_kwargs_seen", [])

    def __getattr__(self, name: str) -> Any:
        self.names.append(name)
        value = getattr(object.__getattribute__(self, "_target"), name)
        if not callable(value):
            return value
        calls, creds = self.calls, self.credential_kwargs_seen

        def recorded(*args: Any, **kwargs: Any) -> Any:
            calls.append((name, tuple(type(a).__name__ for a in args), tuple(sorted(kwargs))))
            creds.extend(sorted(set(kwargs) & CREDENTIAL_KWARGS))
            return value(*args, **kwargs)

        return recorded

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("proxy is immutable")
