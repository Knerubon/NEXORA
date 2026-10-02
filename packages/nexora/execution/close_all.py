"""Manual CLOSE ALL ownership boundary (ADR-034 section 8).

"CLOSE ALL" means close every NEXORA-owned/authorized position — never every
position in the broker account. Ownership is established *only* from durable,
NEXORA-local ``PositionRecord`` state (ADR-033 section 15); a broker position
listing is never consulted here and this module makes no broker call of any
kind. It builds ``TradeIntent`` objects for a manual close-all request; it does
not decide *when*/*whether* each one is ultimately authorized or transmitted —
that remains ``ExistingPositionAuthority``/the future Execution Guard's job.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from nexora.autonomous_contracts import ManualOrigin, TradeIntent, TradeIntentKind, TradeState
from nexora.position.models import PositionRecord

# The only states in which a durable PositionRecord represents a live,
# NEXORA-managed exposure. CLOSED/EXIT_PENDING/EMERGENCY are deliberately
# excluded: EXIT_PENDING/CLOSED already have zero quantity, and EMERGENCY is
# not assumed safe to blindly re-close here.
_OWNED_OPEN_STATES = frozenset({TradeState.OPEN, TradeState.MANAGING})


def owned_open_positions(
    positions: Iterable[PositionRecord], *, symbol: str | None = None
) -> tuple[PositionRecord, ...]:
    """Every durable ``PositionRecord`` already tracked as open/managing.

    This is the *only* source of "ownership" for CLOSE ALL — nothing here
    queries a broker position list, so a position NEXORA never opened or has
    already dropped from its own records can never appear in the result. An
    optional ``symbol`` scopes the result (e.g. the symbol currently viewed in
    the UI); omitting it closes every NEXORA-owned position across all
    symbols — still never "every position in the broker account", because a
    non-NEXORA position was never a member of ``positions`` to begin with.
    """

    return tuple(
        position
        for position in positions
        if position.state in _OWNED_OPEN_STATES and (symbol is None or position.symbol == symbol)
    )


def build_manual_close_all_intents(
    positions: Iterable[PositionRecord],
    *,
    operator_ref: str,
    manual_request_id_prefix: str,
    requested_at: datetime,
    symbol: str | None = None,
) -> tuple[TradeIntent, ...]:
    """Builds one CLOSE ``TradeIntent`` per NEXORA-owned open position
    (ADR-034 section 8), each with its own ``ManualOrigin`` scoped to exactly
    one ``position_id``.

    Each intent gets a distinct ``manual_request_id``/``proposal_id`` derived
    from ``manual_request_id_prefix`` + the position id, so a retry of the
    same CLOSE ALL batch re-derives the identical idempotency key per position
    (ADR-034 section 6) rather than colliding across positions or losing
    per-position dedup.
    """

    owned = owned_open_positions(positions, symbol=symbol)
    intents: list[TradeIntent] = []
    for position in owned:
        request_id = f"{manual_request_id_prefix}:{position.position_id}"
        origin = ManualOrigin(
            operator_ref=operator_ref,
            manual_request_id=request_id,
            requested_at=requested_at,
            position_id=position.position_id,
        )
        intents.append(
            TradeIntent(
                kind=TradeIntentKind.CLOSE,
                symbol=position.symbol,
                side=position.side,
                origin=origin,
                proposal_id=request_id,
            )
        )
    return tuple(intents)
