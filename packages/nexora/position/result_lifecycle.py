"""Pure result-driven position lifecycle helper (ADR-035 section 3.11, PR-8).

``apply_execution_result(position, request, result)`` computes the next
``PositionRecord`` from a *persisted* ``ExecutionResult`` alone. It is the ONE
path for every close origin (manual, CLOSE ALL and algorithmic): it neither
constructs nor accepts an ``ExitDecision``. ``ExitDecision`` stays evidence of
an intent; its pure ``apply_exit_decision`` output is never applied to a
durable store (ADR-035 3.11, INV-23, INV-29).

PURE: no I/O, no persistence, no store, no clock, no broker. It mutates
nothing (records are frozen) and returns a new record, or the *same* position
object when nothing is applied. Calling it does not mean anything was
persisted; a future pipeline step owns persistence and call sites.

Apply table (position must be ``OPEN``/``MANAGING``):

- CLOSE + FILLED: quantity 0 / ``EXIT_PENDING`` then ``CLOSED`` (returns the
  ``CLOSED`` record).
- REDUCE + FILLED: quantity reduced by the filled quantity, state
  ``MANAGING`` (a reduction leaving zero is refused).
- PARTIALLY_FILLED / ACCEPTED / UNKNOWN of any action, MODIFY_PROTECTION of any
  status, and a clean zero-fill REJECTED CLOSE/REDUCE: position returned
  UNCHANGED (OPEN-7 / later policy; REJECTED = zero fill is an ADR-silent
  detail of this PR).
- Anything else fails closed with ``PositionInputError`` (sanitized code).

OPEN-19 (UNRESOLVED, deliberately not addressed here): this helper has no
memory. Re-applying a FILLED CLOSE fails the OPEN/MANAGING precondition, but
re-applying a FILLED REDUCE to an already-reduced position reduces AGAIN.
Making repeated application safe (applied-result markers, result_id registry,
etc.) is a policy decision this module does not invent.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from nexora.autonomous_contracts import (
    TRADE_STATE_TRANSITIONS,
    TradeIntentKind,
    TradeState,
)
from nexora.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus
from nexora.position.models import PositionInputError, PositionRecord
from nexora.position.supervisor import mark_closed

_APPLICABLE_STATES = (TradeState.OPEN, TradeState.MANAGING)


def _require_transition_allowed(current: TradeState, target: TradeState) -> None:
    if target != current and target not in TRADE_STATE_TRANSITIONS.get(current, frozenset()):
        raise PositionInputError("illegal_position_state_transition")


def _validate_consistency(
    position: PositionRecord, request: ExecutionRequest, result: ExecutionResult
) -> None:
    # ADR-035 s4.3: a result with action None is a legacy result; the pipeline
    # rejects it, so it can never drive lifecycle application.
    if result.action is None:
        raise PositionInputError("result_action_required")
    # ADR-035 s3.11: "creating a position" is not a lifecycle application of an
    # existing OPEN/MANAGING position; the table defines no OPEN row.
    if request.action is TradeIntentKind.OPEN or result.action is TradeIntentKind.OPEN:
        raise PositionInputError("open_result_not_applicable")
    # ADR-035 s4.3: action must agree between request and result.
    if result.action is not request.action:
        raise PositionInputError("result_action_mismatch")
    if result.request_ref != request.request_id:
        raise PositionInputError("result_request_mismatch")
    # ADR-035 s4.3: nexora_position_ref attributes the result to a position.
    if request.position_ref != position.position_id:
        raise PositionInputError("request_position_mismatch")
    if (
        result.nexora_position_ref is not None
        and result.nexora_position_ref != request.position_ref
    ):
        raise PositionInputError("result_position_mismatch")
    # ADR-035 s4.2: the position's side must equal the intent's.
    if request.side != position.side:
        raise PositionInputError("request_side_mismatch")
    # ADR-035 s4.3: requested_quantity must agree with the request.
    if request.quantity is not None and result.requested_quantity != request.quantity:
        raise PositionInputError("result_requested_quantity_mismatch")


def apply_execution_result(
    position: PositionRecord, request: ExecutionRequest, result: ExecutionResult
) -> PositionRecord:
    """Returns the next ``PositionRecord`` for a persisted result (see module doc)."""

    _validate_consistency(position, request, result)
    # ADR-035 s3.11: the position must be OPEN/MANAGING. EXIT_PENDING, CLOSED and
    # EMERGENCY (guarded recovery only) fail closed.
    if position.state not in _APPLICABLE_STATES:
        raise PositionInputError("position_not_open_or_managing")

    action = result.action
    if action is TradeIntentKind.MODIFY_PROTECTION or result.status is not ExecutionStatus.FILLED:
        # Nothing applied: OPEN-7 / later policy (and zero-fill REJECTED changes nothing).
        return position

    filled = result.filled_quantity
    if action is TradeIntentKind.CLOSE:
        # ADR-035 s3.11 "filled == resolved quantity" and s4.2 CLOSE invariant
        # (resolved quantity equals local quantity); s4.3 requested = resolved quantity.
        if filled != result.requested_quantity or filled != position.quantity:
            raise PositionInputError("close_fill_quantity_mismatch")
        _require_transition_allowed(position.state, TradeState.EXIT_PENDING)
        exit_pending = replace(position, quantity=Decimal("0"), state=TradeState.EXIT_PENDING)
        return mark_closed(exit_pending)

    # REDUCE (OPEN and MODIFY_PROTECTION were handled above).
    if filled <= 0 or filled >= position.quantity:
        # ADR-035 s4.2 (0 < requested < position quantity) and s3.11 (zero residual refused).
        raise PositionInputError("reduce_fill_quantity_invalid")
    _require_transition_allowed(position.state, TradeState.MANAGING)
    return replace(position, quantity=position.quantity - filled, state=TradeState.MANAGING)
