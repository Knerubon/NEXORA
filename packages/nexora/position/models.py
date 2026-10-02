"""Position lifecycle domain models — Position Supervisor Phase 1 (ADR-033 section 15).

Pure data and pure construction only: no I/O, no broker calls, no
RiskEngine/PaperSimulator wiring. Trailing, break-even, profit-lock, and
time-exit *trigger policy* values are open Quant decisions (ADR-033 section
22) and are deliberately not implemented or defaulted here (AGENTS.md
section 9: speculative defaults must not become specification). Phase 1
covers only: consuming initial SL/TP from an existing ``SignalDecision``,
and the deterministic lifecycle mechanics of an ``ExitDecision`` that was
produced elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from nexora.autonomous_contracts import TradeState
from nexora.signals.models import SignalDecision, SignalEvidence

PositionSide = Literal["long", "short"]
ExitAction = Literal["HOLD", "TIGHTEN", "PARTIAL_CLOSE", "CLOSE"]

# States a PositionRecord may legally occupy. Pre-open states (SETUP,
# WAIT_ENTRY, ENTRY_PENDING, BLOCKED, REJECTED, CANCELLED) belong to the
# entry/intent phase, not an already-opened position (ADR-033 TradeState).
_POSITION_LIFECYCLE_STATES = frozenset(
    {
        TradeState.OPEN,
        TradeState.MANAGING,
        TradeState.EXIT_PENDING,
        TradeState.CLOSED,
        TradeState.EMERGENCY,
    }
)


class PositionInputError(ValueError):
    """Sanitized position domain/input error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProtectionLevels:
    """Current protective levels for an open position.

    Initial values are consumed from ``SignalDecision`` at open time
    (ADR-033 section 15) and are never recomputed independently. ``stop_price``
    may only tighten over the position's life — enforced by the supervisor,
    not here — but the trigger/distance *policy* for when to tighten
    (break-even, trailing, profit-lock) is a Quant decision, not implemented
    in Phase 1.
    """

    stop_price: Decimal
    target_prices: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if not self.stop_price.is_finite() or self.stop_price <= 0:
            raise PositionInputError("invalid_stop_price")
        for target in self.target_prices:
            if not target.is_finite() or target <= 0:
                raise PositionInputError("invalid_target_price")


@dataclass(frozen=True, slots=True)
class PositionRecord:
    """Deterministic, replayable position state.

    ``quantity`` is always >= 0. Only a position whose quantity has gone
    fully to zero may progress ``EXIT_PENDING -> CLOSED`` (ADR-033 section 15
    partial-close lifecycle clarification); a successful partial close with
    residual quantity remaining advances/stays at ``MANAGING`` only.
    """

    position_id: str
    symbol: str
    side: PositionSide
    state: TradeState
    quantity: Decimal
    entry_price: Decimal
    protection: ProtectionLevels
    opened_at: datetime
    source_signal_decision_ref: str
    source_entry_readiness_ref: str

    def __post_init__(self) -> None:
        if not self.position_id.strip() or not self.symbol.strip():
            raise PositionInputError("missing_position_identity")
        if self.state not in _POSITION_LIFECYCLE_STATES:
            raise PositionInputError("invalid_position_lifecycle_state")
        if self.quantity < 0:
            raise PositionInputError("negative_quantity")
        if not self.entry_price.is_finite() or self.entry_price <= 0:
            raise PositionInputError("invalid_entry_price")
        if self.opened_at.tzinfo is None or self.opened_at.utcoffset() is None:
            raise PositionInputError("timezone_required")
        if (
            not self.source_signal_decision_ref.strip()
            or not self.source_entry_readiness_ref.strip()
        ):
            raise PositionInputError("missing_entry_provenance")


@dataclass(frozen=True, slots=True)
class ExitDecision:
    """ADR-033 section 15. Deliberately NOT a ResearchSignal.

    No ``signal_id``, ``side``, or ``decision_time`` masquerade — identity is
    ``decision_id``/``position_id`` and timing is ``decided_at``. ``evidence``
    reuses ``SignalEvidence`` exactly as frozen by the ADR (same evidence
    discipline as ``SignalDecision``); ``reduce_quantity`` and
    ``new_stop_price`` are additive fields (not part of the frozen action
    vocabulary) needed to make ``PARTIAL_CLOSE``/``TIGHTEN`` mechanically
    actionable.
    """

    decision_id: str
    position_id: str
    action: ExitAction
    evidence: tuple[SignalEvidence, ...]
    source_refs: tuple[str, ...]
    decided_at: datetime
    reduce_quantity: Decimal | None = None
    new_stop_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.decision_id.strip() or not self.position_id.strip():
            raise PositionInputError("missing_exit_decision_identity")
        if not self.evidence:
            raise PositionInputError("missing_exit_evidence")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise PositionInputError("timezone_required")

        if self.action == "PARTIAL_CLOSE":
            if self.reduce_quantity is None or self.reduce_quantity <= 0:
                raise PositionInputError("partial_close_requires_positive_reduce_quantity")
            if self.new_stop_price is not None:
                raise PositionInputError("new_stop_price_only_valid_for_tighten")
        elif self.action == "TIGHTEN":
            if self.new_stop_price is None:
                raise PositionInputError("tighten_requires_new_stop_price")
            if self.reduce_quantity is not None:
                raise PositionInputError("reduce_quantity_only_valid_for_partial_close")
        else:
            if self.reduce_quantity is not None:
                raise PositionInputError("reduce_quantity_only_valid_for_partial_close")
            if self.new_stop_price is not None:
                raise PositionInputError("new_stop_price_only_valid_for_tighten")


def open_position_from_signal(
    *,
    position_id: str,
    signal_decision: SignalDecision,
    signal_decision_ref: str,
    entry_readiness_ref: str,
    side: PositionSide,
    symbol: str,
    quantity: Decimal,
    entry_price: Decimal,
    opened_at: datetime,
) -> PositionRecord:
    """Builds the initial ``PositionRecord``, consuming SL/TP from ``SignalDecision`` only.

    Never recomputes invalidation/targets (ADR-033 section 15): if the signal
    carries neither, this is a hard error, not a silently invented default.
    """

    if signal_decision.invalidation_price is None:
        raise PositionInputError("signal_decision_missing_invalidation_price")
    if not signal_decision.targets:
        raise PositionInputError("signal_decision_missing_targets")
    if quantity <= 0:
        raise PositionInputError("invalid_opening_quantity")

    protection = ProtectionLevels(
        stop_price=signal_decision.invalidation_price,
        target_prices=tuple(target.price for target in signal_decision.targets),
    )
    return PositionRecord(
        position_id=position_id,
        symbol=symbol,
        side=side,
        state=TradeState.OPEN,
        quantity=quantity,
        entry_price=entry_price,
        protection=protection,
        opened_at=opened_at,
        source_signal_decision_ref=signal_decision_ref,
        source_entry_readiness_ref=entry_readiness_ref,
    )
