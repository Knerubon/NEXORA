"""Legacy pivot pattern units (ADR-024 Decision 2, ``legacy_pivot`` family).

Each unit is a time-boxed, parity-tested copy of one branch of
``SignalEngine._patterns`` (``packages/nexora/signals/engine.py``). Rules, windows,
price bounds, confirmation times, names and algorithm versions are reproduced exactly;
nothing here is a new trading rule. The legacy implementation remains the production
source until the Phase 3 source switch (ADR-024 Decision 11).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from nexora.artifacts import canonical_serialize
from nexora.features import PATTERN_ENGINE_UNITS
from nexora.patterns.models import PatternDirection, PatternResult
from nexora.structure.models import ConfirmedPivot

Window = tuple[ConfirmedPivot, ...]

_BEARISH_6 = ("high", "low", "high", "low", "high", "low")
_BULLISH_6 = ("low", "high", "low", "high", "low", "high")


def _num(value: Decimal) -> str:
    return str(canonical_serialize(value))


def _kinds(window: Window) -> tuple[str, ...]:
    return tuple(pivot.kind for pivot in window)


def _double_bottom(w: Window, tol: Decimal) -> bool:
    return _kinds(w) == ("low", "high", "low") and abs(w[0].price - w[2].price) <= tol


def _double_top(w: Window, tol: Decimal) -> bool:
    return _kinds(w) == ("high", "low", "high") and abs(w[0].price - w[2].price) <= tol


def _head_and_shoulders(w: Window, tol: Decimal) -> bool:
    a, b, c, d, e, f = w
    return (
        _kinds(w) == _BEARISH_6
        and c.price > max(a.price, e.price) + tol
        and abs(a.price - e.price) <= tol
        and f.price < min(b.price, d.price)
    )


def _inverse_head_and_shoulders(w: Window, tol: Decimal) -> bool:
    a, b, c, d, e, f = w
    return (
        _kinds(w) == _BULLISH_6
        and c.price < min(a.price, e.price) - tol
        and abs(a.price - e.price) <= tol
        and f.price > max(b.price, d.price)
    )


def _triangle_breakdown(w: Window, tol: Decimal) -> bool:
    # Legacy precedence: evaluated only when head-and-shoulders did not match. The H&S
    # predicate runs here as an exclusion guard even when the H&S unit is DISABLED.
    a, b, c, d, e, f = w
    return (
        _kinds(w) == _BEARISH_6
        and not _head_and_shoulders(w, tol)
        and a.price > c.price > e.price
        and b.price < d.price
        and f.price < b.price
    )


def _triangle_breakout(w: Window, tol: Decimal) -> bool:
    a, b, c, d, e, f = w
    return (
        _kinds(w) == _BULLISH_6
        and not _inverse_head_and_shoulders(w, tol)
        and a.price < c.price < e.price
        and b.price > d.price
        and f.price > b.price
    )


def _failed_breakout(w: Window, tol: Decimal) -> bool:
    a, b, c, d = w
    return _kinds(w) == ("high", "low", "high", "low") and c.price > a.price and d.price < b.price


def _failed_breakdown(w: Window, tol: Decimal) -> bool:
    a, b, c, d = w
    return _kinds(w) == ("low", "high", "low", "high") and c.price < a.price and d.price > b.price


def _double_bottom_bounds(w: Window) -> tuple[Decimal, Decimal]:
    return min(w[0].price, w[2].price), w[1].price


def _double_top_bounds(w: Window) -> tuple[Decimal, Decimal]:
    return w[1].price, max(w[0].price, w[2].price)


def _window_bounds(w: Window) -> tuple[Decimal, Decimal]:
    return min(p.price for p in w), max(p.price for p in w)


def _last_confirmation(w: Window) -> datetime:
    return w[-1].confirmation_time


def _max_confirmation(w: Window) -> datetime:
    return max(p.confirmation_time for p in w)


def _double_facts(w: Window, tol: Decimal) -> tuple[str, ...]:
    return (f"abs_p1_p3={_num(abs(w[0].price - w[2].price))}<=tol={_num(tol)}",)


def _hs_facts(w: Window, tol: Decimal) -> tuple[str, ...]:
    a, b, c, d, e, f = w
    return (
        f"head={_num(c.price)}",
        f"shoulders={_num(a.price)},{_num(e.price)}",
        f"neckline={_num(b.price)},{_num(d.price)}",
        f"break={_num(f.price)}",
        f"tol={_num(tol)}",
    )


def _sequence_facts(w: Window, tol: Decimal) -> tuple[str, ...]:
    return tuple(f"p{index}={_num(p.price)}" for index, p in enumerate(w, start=1))


@dataclass(frozen=True, slots=True)
class LegacyUnit:
    algorithm_id: str
    pattern_type: str
    direction: PatternDirection
    window_size: int
    algorithm_version: str
    roles: tuple[str, ...]
    matches: Callable[[Window, Decimal], bool]
    bounds: Callable[[Window], tuple[Decimal, Decimal]]
    confirmation: Callable[[Window], datetime]
    facts: Callable[[Window, Decimal], tuple[str, ...]]
    # Legacy `source_data_reference` uses only the last pivot for double top/bottom.
    legacy_reference_last_only: bool

    @property
    def evidence_code(self) -> str:
        return f"pattern_{self.pattern_type}"


_DOUBLE_ROLES = ("first", "middle", "second")
_HS_ROLES = (
    "left_shoulder",
    "neckline_left",
    "head",
    "neckline_right",
    "right_shoulder",
    "neckline_break",
)
_SIX = tuple(f"pivot_{index}" for index in range(1, 7))
_FOUR = tuple(f"pivot_{index}" for index in range(1, 5))

LEGACY_UNITS: tuple[LegacyUnit, ...] = (
    LegacyUnit(
        "legacy_pivot.double_bottom",
        "double_bottom",
        "bullish",
        3,
        "p8a-pattern-v1",
        _DOUBLE_ROLES,
        _double_bottom,
        _double_bottom_bounds,
        _last_confirmation,
        _double_facts,
        True,
    ),
    LegacyUnit(
        "legacy_pivot.double_top",
        "double_top",
        "bearish",
        3,
        "p8a-pattern-v1",
        _DOUBLE_ROLES,
        _double_top,
        _double_top_bounds,
        _last_confirmation,
        _double_facts,
        True,
    ),
    LegacyUnit(
        "legacy_pivot.head_and_shoulders",
        "head_and_shoulders",
        "bearish",
        6,
        "p8b-pattern-v2",
        _HS_ROLES,
        _head_and_shoulders,
        _window_bounds,
        _max_confirmation,
        _hs_facts,
        False,
    ),
    LegacyUnit(
        "legacy_pivot.inverse_head_and_shoulders",
        "inverse_head_and_shoulders",
        "bullish",
        6,
        "p8b-pattern-v2",
        _HS_ROLES,
        _inverse_head_and_shoulders,
        _window_bounds,
        _max_confirmation,
        _hs_facts,
        False,
    ),
    LegacyUnit(
        "legacy_pivot.triangle_breakdown",
        "triangle_breakdown",
        "bearish",
        6,
        "p8b-pattern-v2",
        _SIX,
        _triangle_breakdown,
        _window_bounds,
        _max_confirmation,
        _sequence_facts,
        False,
    ),
    LegacyUnit(
        "legacy_pivot.triangle_breakout",
        "triangle_breakout",
        "bullish",
        6,
        "p8b-pattern-v2",
        _SIX,
        _triangle_breakout,
        _window_bounds,
        _max_confirmation,
        _sequence_facts,
        False,
    ),
    LegacyUnit(
        "legacy_pivot.failed_breakout",
        "failed_breakout",
        "bearish",
        4,
        "p8b-pattern-v2",
        _FOUR,
        _failed_breakout,
        _window_bounds,
        _max_confirmation,
        _sequence_facts,
        False,
    ),
    LegacyUnit(
        "legacy_pivot.failed_breakdown",
        "failed_breakdown",
        "bullish",
        4,
        "p8b-pattern-v2",
        _FOUR,
        _failed_breakdown,
        _window_bounds,
        _max_confirmation,
        _sequence_facts,
        False,
    ),
)

if tuple(unit.algorithm_id for unit in LEGACY_UNITS) != PATTERN_ENGINE_UNITS:
    raise RuntimeError("legacy units diverge from the pattern_engine registry")

UNITS_BY_ID: dict[str, LegacyUnit] = {unit.algorithm_id: unit for unit in LEGACY_UNITS}
WINDOW_BOUND = max(unit.window_size for unit in LEGACY_UNITS)


def legacy_parity_key(
    result: PatternResult,
) -> tuple[str, str, datetime, datetime, Decimal, Decimal, str, str, str]:
    """Fields compared 1:1 with legacy ``PatternEvidence`` (ADR-024 Decision 10).

    ``relation`` is excluded: it depends on the Signal's dominant side.
    """
    unit = UNITS_BY_ID[result.algorithm_id]
    ids = [anchor.source_transition_id for anchor in result.anchors]
    reference = ids[-1] if unit.legacy_reference_last_only else "|".join(ids)
    return (
        result.pattern_type,
        result.direction,
        result.start_time,
        result.confirmation_time,
        result.price_low,
        result.price_high,
        result.evidence_code,
        result.algorithm_version,
        reference,
    )
