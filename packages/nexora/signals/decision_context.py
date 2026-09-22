"""Deterministic Decision Context (Decision Clarity + Bias V1).

Purely derived, read-only, and never persisted or journaled: computed on demand
from an existing SignalDecision and MatrixSnapshot. Does not participate in the
Signal Engine's decision rules, storage, replay, or the Experience Engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nexora.matrix.models import MatrixSnapshot
from nexora.signals.models import SignalDecision

Bias = Literal["BULLISH", "BULLISH_LEAN", "MIXED", "BEARISH_LEAN", "BEARISH", "UNAVAILABLE"]
DecisionState = Literal["DEVELOPING", "DIRECTION_CONFIRMED", "UNAVAILABLE"]


@dataclass(frozen=True, slots=True)
class DecisionAlignment:
    aligned: int
    total: int


@dataclass(frozen=True, slots=True)
class DecisionContext:
    schema_version: Literal[1]
    bias: Bias
    state: DecisionState
    alignment: DecisionAlignment
    reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]


def derive_decision_context(
    *, decision: SignalDecision | None, matrix: MatrixSnapshot | None
) -> DecisionContext:
    """Pure and deterministic: the same (decision, matrix) always yields the same context."""
    if decision is None or matrix is None:
        return DecisionContext(
            schema_version=1,
            bias="UNAVAILABLE",
            state="UNAVAILABLE",
            alignment=DecisionAlignment(aligned=0, total=0),
            reasons=(),
            waiting_for=(),
        )
    bias, aligned = _bias(matrix)
    return DecisionContext(
        schema_version=1,
        bias=bias,
        state=_state(bias=bias, action=decision.action),
        alignment=DecisionAlignment(aligned=aligned, total=len(matrix.resolutions)),
        reasons=_reasons(decision),
        waiting_for=decision.future_conditions if decision.action == "WAIT" else (),
    )


def _bias(matrix: MatrixSnapshot) -> tuple[Bias, int]:
    """Derived from raw per-resolution directions, not the coarser alignment enum.

    ``MatrixSnapshot.alignment`` reports "unavailable" both when there is no
    directional evidence at all and when some resolutions are still warming up
    while others already agree; collapsing both to UNAVAILABLE would hide real,
    honestly-supportable lean evidence. UNAVAILABLE here is reserved for the
    case with zero known directions.
    """
    total = len(matrix.resolutions)
    bullish = sum(1 for r in matrix.resolutions if r.direction == "X")
    bearish = sum(1 for r in matrix.resolutions if r.direction == "O")
    if total == 0 or (bullish + bearish) == 0:
        return "UNAVAILABLE", 0
    if bullish == total:
        return "BULLISH", bullish
    if bearish == total:
        return "BEARISH", bearish
    if bullish > bearish:
        return "BULLISH_LEAN", bullish
    if bearish > bullish:
        return "BEARISH_LEAN", bearish
    return "MIXED", bullish


def _state(*, bias: Bias, action: str) -> DecisionState:
    """Only states provable from a single snapshot are implemented.

    DIRECTION_CONFIRMED means only that the Matrix direction agrees with the
    Signal action - not a fully confirmed trade setup. Structure, Pattern,
    Trendline and temporal Signal Stability are not part of this state.

    ACTIVE/WEAKENING/INVALIDATED require tracking a specific trade's lifecycle
    across snapshots, which does not exist yet; that is deferred to a future
    Signal Stability task rather than fabricated here.
    """
    if bias == "BULLISH" and action == "BUY":
        return "DIRECTION_CONFIRMED"
    if bias == "BEARISH" and action == "SELL":
        return "DIRECTION_CONFIRMED"
    if bias in ("MIXED", "UNAVAILABLE"):
        return "UNAVAILABLE"
    return "DEVELOPING"


def _reasons(decision: SignalDecision) -> tuple[str, ...]:
    if decision.action != "WAIT":
        return ()
    reasons = tuple(item.reason for item in decision.negative_evidence)
    if reasons:
        return reasons
    if decision.positive_evidence:
        return (
            "Evidence gap between BUY and SELL points has not reached the "
            "actionable threshold.",
        )
    return ("No supporting evidence has been produced yet.",)
