"""Pure contract shapes frozen by ADR-033 (Autonomous Trading Contract Freeze V1).

No I/O, no broker calls, no runtime wiring. Nothing in this module is imported by
research/pipeline.py, research/runtime.py, checkpoint_state.py, apps/api, or apps/web.
It exists to document and validate the contract shapes themselves, not to implement
Position Supervisor, Risk migration, Execution, or Broker Adapter behavior.

See docs/decisions/ADR-033-autonomous-trading-contracts-v1.md for the full rationale,
authority model, and the explicit list of what remains PROVISIONAL or BLOCKED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class TradingMode(StrEnum):
    SHADOW = "SHADOW"
    ASSISTED = "ASSISTED"
    AUTO = "AUTO"


class TradeState(StrEnum):
    SETUP = "SETUP"
    WAIT_ENTRY = "WAIT_ENTRY"
    ENTRY_PENDING = "ENTRY_PENDING"
    OPEN = "OPEN"
    MANAGING = "MANAGING"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EMERGENCY = "EMERGENCY"


# ADR-033 section E / instruction E: terminal states have no outgoing transitions.
TRADE_STATE_TRANSITIONS: dict[TradeState, frozenset[TradeState]] = {
    TradeState.SETUP: frozenset({TradeState.WAIT_ENTRY, TradeState.BLOCKED, TradeState.CANCELLED}),
    TradeState.WAIT_ENTRY: frozenset(
        {TradeState.ENTRY_PENDING, TradeState.BLOCKED, TradeState.REJECTED, TradeState.CANCELLED}
    ),
    TradeState.ENTRY_PENDING: frozenset(
        {TradeState.OPEN, TradeState.REJECTED, TradeState.EMERGENCY}
    ),
    TradeState.OPEN: frozenset(
        {TradeState.MANAGING, TradeState.EXIT_PENDING, TradeState.EMERGENCY}
    ),
    TradeState.MANAGING: frozenset({TradeState.EXIT_PENDING, TradeState.EMERGENCY}),
    TradeState.EXIT_PENDING: frozenset({TradeState.CLOSED, TradeState.EMERGENCY}),
    TradeState.CLOSED: frozenset(),
    TradeState.BLOCKED: frozenset(),
    TradeState.REJECTED: frozenset(),
    TradeState.CANCELLED: frozenset(),
    TradeState.EMERGENCY: frozenset(),
}


class TradeIntentKind(StrEnum):
    """ADR-033 section 10. OPEN can increase exposure; the other three never may."""

    OPEN = "OPEN"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    MODIFY_PROTECTION = "MODIFY_PROTECTION"


# Kinds whose effect must never increase net exposure (ADR-033 section 10/11 invariant).
RISK_REDUCING_KINDS = frozenset(
    {TradeIntentKind.REDUCE, TradeIntentKind.CLOSE, TradeIntentKind.MODIFY_PROTECTION}
)


@dataclass(frozen=True, slots=True)
class EntryOrigin:
    """Origin for TradeIntentKind.OPEN. References, not raw broker-shaped data."""

    signal_decision_ref: str
    entry_readiness_ref: str
    ai_analysis_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.signal_decision_ref.strip() or not self.entry_readiness_ref.strip():
            raise ValueError("missing_entry_origin_reference")


@dataclass(frozen=True, slots=True)
class PositionOrigin:
    """Origin for REDUCE/CLOSE/MODIFY_PROTECTION. Never a synthetic ResearchSignal."""

    position_id: str
    exit_decision_ref: str

    def __post_init__(self) -> None:
        if not self.position_id.strip() or not self.exit_decision_ref.strip():
            raise ValueError("missing_position_origin_reference")


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """ADR-033 section 10. A pure contract shape: no sizing, no broker call, no I/O."""

    kind: TradeIntentKind
    symbol: str
    side: Literal["long", "short"]
    origin: EntryOrigin | PositionOrigin
    proposal_id: str

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.proposal_id.strip():
            raise ValueError("missing_trade_intent_identity")
        if self.kind is TradeIntentKind.OPEN and not isinstance(self.origin, EntryOrigin):
            raise ValueError("open_requires_entry_origin")
        if self.kind in RISK_REDUCING_KINDS and not isinstance(self.origin, PositionOrigin):
            raise ValueError("risk_reducing_kind_requires_position_origin")


def is_risk_reducing(kind: TradeIntentKind) -> bool:
    """True for every kind that must never be allowed to increase net exposure."""

    return kind in RISK_REDUCING_KINDS
