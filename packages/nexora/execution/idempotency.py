"""Idempotency / duplicate-order-safety contract (ADR-034 section 6).

Defines the deterministic identity rules so the same logical ``TradeIntent``
can never produce two broker orders because of retry, reconnect, API restart,
UI retry, or an UNKNOWN acknowledgement. No persistence here — a durable dedup
store is explicitly future work (ADR-034 section 6); this module only freezes
the key a future store would use, so every caller derives the identical key
for the identical logical command.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from nexora.autonomous_contracts import EntryOrigin, ManualOrigin, PositionOrigin, TradeIntent
from nexora.execution.models import (
    ExecutionContractError,
    ExecutionRequest,
    PriceConstraint,
    ProtectionRequest,
)


def trade_intent_identity(intent: TradeIntent) -> str:
    """Stable identity for a logical ``TradeIntent``.

    Reuses ADR-033's ``proposal_id`` verbatim — no new identity concept is
    introduced for the intent itself.
    """

    return intent.proposal_id


def execution_request_idempotency_key(intent: TradeIntent) -> str:
    """Deterministic idempotency key for the ``ExecutionRequest`` a given
    ``TradeIntent`` produces.

    Retrying, reconnecting, restarting the API process, or an operator
    re-clicking the Manual UI for the *same* logical intent must derive the
    *same* key, so a future durable dedup store can recognize and refuse a
    second broker order for it. Keyed on ``kind`` as well as identity, because
    a REDUCE and a CLOSE against the very same proposal are different logical
    executions, not duplicates of each other.
    """

    return f"exec:{intent.kind.value}:{trade_intent_identity(intent)}"


def _origin_ref(intent: TradeIntent) -> str:
    origin = intent.origin
    if isinstance(origin, EntryOrigin):
        return origin.entry_readiness_ref
    if isinstance(origin, PositionOrigin):
        return origin.exit_decision_ref
    if isinstance(origin, ManualOrigin):
        return origin.manual_request_id
    raise ExecutionContractError("unknown_trade_intent_origin")  # pragma: no cover


def build_execution_request(
    intent: TradeIntent,
    *,
    request_id: str,
    instrument_id: str,
    created_at: datetime,
    quantity: Decimal | None = None,
    price_constraint: PriceConstraint | None = None,
    protection: ProtectionRequest | None = None,
    position_ref: str | None = None,
) -> ExecutionRequest:
    """Builds an ``ExecutionRequest`` whose ``idempotency_key``/``origin_ref``
    are always correctly derived from ``intent`` — never a free-form value a
    caller could accidentally desync from the originating ``TradeIntent``
    (ADR-034 section 6). Prefer this over constructing ``ExecutionRequest``
    directly from application code.
    """

    return ExecutionRequest(
        request_id=request_id,
        idempotency_key=execution_request_idempotency_key(intent),
        intent_proposal_id=intent.proposal_id,
        origin_ref=_origin_ref(intent),
        instrument_id=instrument_id,
        side=intent.side,
        action=intent.kind,
        quantity=quantity,
        price_constraint=price_constraint,
        protection=protection,
        position_ref=position_ref,
        created_at=created_at,
    )
