"""Explicit, ambient-independent Decimal arithmetic for the edge research toolkit.

Review finding D-2 (PR #79): cost and PnL arithmetic used the CALLER's ambient
`decimal` context, so the same inputs produced different net PnL under different precision or
rounding, while the run id (which hashed inputs only) stayed identical. A hash-only
reproduction check could therefore confirm different financial output.

Fix: every public edge entry point that does Decimal arithmetic runs inside a fresh copy of
`EDGE_CONTEXT`, whatever context the caller has set, and `arithmetic_descriptor()` is bound
into the run identity so a different arithmetic can never share a run id.

Open decision (register item Q-T12): the numeric values below equal CPython's DEFAULT context
(28 digits, round-half-even, standard traps). They are chosen so behaviour with an untouched
default context is unchanged, NOT because a Quant or Architect approved them. Changing them
changes every run id by design. Until Q-T12 is decided this is an engineering default.
"""

from __future__ import annotations

import decimal
import functools
from collections.abc import Callable
from typing import Any

ARITHMETIC_VERSION = "edge-decimal-context/1"

EDGE_CONTEXT = decimal.Context(
    prec=28,
    rounding=decimal.ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow],
)


def arithmetic_descriptor() -> tuple[str, int, str, int, int, tuple[str, ...]]:
    """Canonical, hashable description of the arithmetic actually in force.

    Read at call time from `EDGE_CONTEXT`, so any change to the context changes the descriptor
    (and therefore the run id) instead of silently changing results under the same identity."""
    ctx = EDGE_CONTEXT
    traps = tuple(sorted(signal.__name__ for signal, enabled in ctx.traps.items() if enabled))
    return (
        ARITHMETIC_VERSION,
        int(ctx.prec),
        str(ctx.rounding),
        int(ctx.Emin),
        int(ctx.Emax),
        traps,
    )


def deterministic_arithmetic[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Run `fn` under a private copy of `EDGE_CONTEXT`, ignoring the ambient context.

    A copy is used per call so flags raised inside never leak out and the shared template is
    never mutated."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with decimal.localcontext(EDGE_CONTEXT):
            return fn(*args, **kwargs)

    wrapper.__edge_deterministic__ = True  # type: ignore[attr-defined]
    return wrapper


def is_deterministic(fn: Any) -> bool:
    return bool(getattr(fn, "__edge_deterministic__", False))
