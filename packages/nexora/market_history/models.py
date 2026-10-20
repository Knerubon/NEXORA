"""Plain data models. Everything serialises to JSON primitives for canonical hashing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

FieldState = Literal["known", "unavailable", "unknown"]


class HistoryError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_MAX_DIGITS = 64
_MAX_EXPONENT = 64


def _parts(value: Decimal) -> tuple[int, int, int]:
    """(sign, integer digits, exponent) read from the Decimal's own tuple: exact, and independent
    of the ambient decimal context (unlike normalize(), quantize() and arithmetic)."""
    sign, digits, exp = value.as_tuple()
    if not isinstance(exp, int) or len(digits) > _MAX_DIGITS or abs(exp) > _MAX_EXPONENT:
        raise HistoryError("decimal_out_of_range")
    return sign, int("".join(map(str, digits))), exp


def decimal_str(value: Decimal) -> str:
    """Stable text for a FINITE Decimal: no exponent, no trailing zeros, '0' for zero.

    Pure integer/string work, so the result never depends on the caller's decimal context."""
    if not value.is_finite():
        raise HistoryError("non_finite_decimal")
    sign, n, exp = _parts(value)
    if n == 0:
        return "0"
    while n % 10 == 0:
        n //= 10
        exp += 1
    text = str(n)
    if exp >= 0:
        text += "0" * exp
    elif len(text) <= -exp:
        text = "0." + "0" * (-exp - len(text)) + text
    else:
        text = text[:exp] + "." + text[exp:]
    return ("-" if sign else "") + text


def exact_sub(a: Decimal, b: Decimal) -> Decimal:
    """a - b without rounding and without consulting the ambient context. Non-finite -> NaN."""
    if not (a.is_finite() and b.is_finite()):
        return Decimal("NaN")
    sa, na, ea = _parts(a)
    sb, nb, eb = _parts(b)
    exp = min(ea, eb)
    diff = (-na if sa else na) * 10 ** (ea - exp) - (-nb if sb else nb) * 10 ** (eb - exp)
    return Decimal((1 if diff < 0 else 0, tuple(int(c) for c in str(abs(diff))), exp))


@dataclass(frozen=True, slots=True)
class RawTick:
    """One tick exactly as the source reported it. `time_msc` is the RAW broker-clock epoch
    milliseconds; it is NOT UTC until a TimeConversion says so."""

    time_msc: int
    bid: Decimal
    ask: Decimal
    flags: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizedTick:
    utc_msc: int
    raw_msc: int
    bid: Decimal
    ask: Decimal
    spread: Decimal  # ask - bid, in price units as quoted


@dataclass(frozen=True, slots=True)
class MetaField:
    """One metadata fact. A fact the source cannot supply is 'unavailable', never zero."""

    name: str
    state: FieldState
    value: str | None = None

    def __post_init__(self) -> None:
        if (self.state == "known") != (self.value is not None):
            raise HistoryError("meta_field_state_value_mismatch", self.name)

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state, "value": self.value}


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Instrument facts as reported by the adapter (tick size, point, contract size, swaps...).

    Commission is normally NOT a symbol property; it must be recorded 'unavailable' unless the
    source really supplies it."""

    symbol: str
    fields: tuple[MetaField, ...]

    def to_json(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "fields": [f.to_json() for f in sorted_fields(self.fields)]}

    def get(self, name: str) -> MetaField:
        for f in self.fields:
            if f.name == name:
                return f
        return MetaField(name, "unknown")


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    """What the source/terminal reported about itself (no account identifiers)."""

    fields: tuple[MetaField, ...]

    def to_json(self) -> dict[str, Any]:
        return {"fields": [f.to_json() for f in sorted_fields(self.fields)]}


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    kind: str  # e.g. "mt5-readonly-history", "fixture"
    version: str
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "version": self.version, "notes": self.notes}


def sorted_fields(fields: tuple[MetaField, ...]) -> list[MetaField]:
    return sorted(fields, key=lambda f: f.name)
