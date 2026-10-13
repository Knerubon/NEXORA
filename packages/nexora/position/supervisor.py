"""Position Supervisor Phase 1 — pure deterministic lifecycle core (ADR-033 section 15).

This module does NOT decide *when* to tighten, partial-close, or close a
position. Break-even, profit-lock, trailing distance, P&F-vs-structure
trailing precedence, partial-close ratios, and time-exit policy are open
Quant decisions (ADR-033 section 22) and are deliberately not implemented
here. Phase 1 implements only the deterministic mechanics that apply once an
``ExitDecision`` already exists (produced elsewhere, by a future Quant-
approved rule or supplied directly by a caller/test): lifecycle state
transitions, partial-close quantity bookkeeping, and ``TradeIntent``
generation.

No I/O. No ``RiskEngine``/``PaperSimulator`` wiring — ``RiskEngine.
evaluate_reduction()`` is explicitly out of scope for this phase (ADR-033
section 12). No broker execution. Never converts an exit into a
``ResearchSignal`` and never invents a ``signal_id``.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, NoReturn

from nexora.autonomous_contracts import (
    TRADE_STATE_TRANSITIONS,
    PositionOrigin,
    TradeIntent,
    TradeIntentKind,
    TradeState,
)
from nexora.position.models import ExitDecision, PositionInputError, PositionRecord

if TYPE_CHECKING:
    from datetime import datetime

RECOVERY_AUTHORIZATION_NOT_RESOLVED = "recovery_authorization_not_resolved"


class RecoveryAuthorizationNotResolvedError(PositionInputError):
    """EMERGENCY recovery is HARD-DENIED (ADR-035 section 6, OPEN-20 unresolved)."""

    def __init__(self) -> None:
        super().__init__(RECOVERY_AUTHORIZATION_NOT_RESOLVED)


_EXIT_ACTION_TO_INTENT_KIND: dict[str, TradeIntentKind] = {
    "TIGHTEN": TradeIntentKind.MODIFY_PROTECTION,
    "PARTIAL_CLOSE": TradeIntentKind.REDUCE,
    "CLOSE": TradeIntentKind.CLOSE,
}


def _require_transition(current: TradeState, target: TradeState) -> None:
    if target == current:
        return
    allowed = TRADE_STATE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise PositionInputError("illegal_position_state_transition")


def _require_position_match(position: PositionRecord, decision: ExitDecision) -> None:
    if decision.position_id != position.position_id:
        raise PositionInputError("exit_decision_position_mismatch")


def apply_exit_decision(position: PositionRecord, decision: ExitDecision) -> PositionRecord:
    """Applies lifecycle mechanics for a decision already made elsewhere.

    - ``HOLD``: no-op, position returned unchanged.
    - ``TIGHTEN``: protection update only, no quantity/state change. The new
      stop may only tighten, never widen (enforced here, not by ``ExitDecision``
      itself, since "tighter" is relative to the current protection level).
    - ``PARTIAL_CLOSE``: reduces quantity. Frozen rule (ADR-033 section 15):
      a residual quantity > 0 keeps/advances the position to ``MANAGING``
      only, never further. A reduction that would leave zero residual is
      rejected — use ``CLOSE`` for a full exit, not ``PARTIAL_CLOSE``.
    - ``CLOSE``: quantity goes to zero and the position advances to
      ``EXIT_PENDING`` only — never directly to ``CLOSED`` (see ``mark_closed``).
    """

    _require_position_match(position, decision)

    if decision.action == "HOLD":
        return position

    if decision.action == "TIGHTEN":
        if position.state not in (TradeState.OPEN, TradeState.MANAGING):
            raise PositionInputError("tighten_requires_open_or_managing_position")
        assert decision.new_stop_price is not None  # enforced by ExitDecision.__post_init__
        _require_tightening_only(position, decision.new_stop_price)
        return replace(
            position, protection=replace(position.protection, stop_price=decision.new_stop_price)
        )

    if decision.action == "PARTIAL_CLOSE":
        assert decision.reduce_quantity is not None  # enforced by ExitDecision.__post_init__
        remaining = position.quantity - decision.reduce_quantity
        if remaining <= 0:
            raise PositionInputError("partial_close_must_leave_residual_quantity")
        _require_transition(position.state, TradeState.MANAGING)
        return replace(position, quantity=remaining, state=TradeState.MANAGING)

    if decision.action == "CLOSE":
        _require_transition(position.state, TradeState.EXIT_PENDING)
        return replace(position, quantity=Decimal("0"), state=TradeState.EXIT_PENDING)

    raise PositionInputError("unknown_exit_action")


def _require_tightening_only(position: PositionRecord, new_stop_price: Decimal) -> None:
    if not new_stop_price.is_finite() or new_stop_price <= 0:
        raise PositionInputError("invalid_new_stop_price")
    current_stop = position.protection.stop_price
    tightened = (
        new_stop_price > current_stop if position.side == "long" else new_stop_price < current_stop
    )
    if not tightened:
        raise PositionInputError("protection_must_only_tighten")


def mark_closed(position: PositionRecord) -> PositionRecord:
    """Finalizes a fully-exited position.

    Only a position already in ``EXIT_PENDING`` with zero quantity may reach
    ``CLOSED`` (ADR-033 section 15). Not wired to any broker/execution
    confirmation in Phase 1 — a future phase drives this from
    ``ExecutionResult``, not from this function being called speculatively.
    """

    if position.quantity != 0:
        raise PositionInputError("cannot_close_nonzero_quantity")
    _require_transition(position.state, TradeState.CLOSED)
    return replace(position, state=TradeState.CLOSED)


def recover_from_emergency(
    position: PositionRecord,
    evidence: object,
    *,
    operator_ref: str,
    recovered_at: datetime,
) -> NoReturn:
    """HARD-DENY stub for the EMERGENCY recovery transition (ADR-035 section 6, INV-30).

    ``position/supervisor.py`` owns the authorization-enabled recovery
    transition; ``execution/recovery.py`` only plans. Plan != authorize !=
    apply. OPEN-20 (authorization model) and OPEN-1 (freshness bound) are
    unresolved, so this function ALWAYS raises
    ``RecoveryAuthorizationNotResolvedError`` and NEVER returns a
    ``PositionRecord`` (the ADR's ``-> PositionRecord`` is realised as
    ``NoReturn``). Every argument is ignored: ``operator_ref`` is audit
    identity only, a ``RecoveryPlan`` (or its ``authorized``/``derived_target``)
    is no proof of authorization, and there is no flag, override, default,
    timeout or test-only path. It does no I/O, reads no clock, and never
    mutates ``position``. ``TRADE_STATE_TRANSITIONS[EMERGENCY]`` is unchanged.
    """

    del position, evidence, operator_ref, recovered_at
    raise RecoveryAuthorizationNotResolvedError()


def build_trade_intent(
    position: PositionRecord, decision: ExitDecision, *, proposal_id: str
) -> TradeIntent:
    """Generates the ``TradeIntent`` for a non-``HOLD`` ``ExitDecision``.

    Origin is always ``PositionOrigin`` referencing the ``ExitDecision`` by
    id — never a synthetic/fake ``ResearchSignal`` or invented ``signal_id``
    (ADR-033 sections 10/15).
    """

    _require_position_match(position, decision)
    kind = _EXIT_ACTION_TO_INTENT_KIND.get(decision.action)
    if kind is None:
        raise PositionInputError("hold_has_no_trade_intent")
    origin = PositionOrigin(
        position_id=position.position_id, exit_decision_ref=decision.decision_id
    )
    return TradeIntent(
        kind=kind,
        symbol=position.symbol,
        side=position.side,
        origin=origin,
        proposal_id=proposal_id,
    )
