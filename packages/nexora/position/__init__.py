"""Position Supervisor Phase 1 (ADR-033 section 15).

Pure domain models and deterministic lifecycle mechanics for an open
position. No I/O, no RiskEngine/PaperSimulator wiring, no broker execution,
no PROD/runtime/API wiring. See docs/decisions/ADR-033-autonomous-trading-contracts-v1.md.
"""

from nexora.position.models import (
    ExitDecision,
    PositionInputError,
    PositionRecord,
    PositionSide,
    ProtectionLevels,
    open_position_from_signal,
)
from nexora.position.supervisor import (
    apply_exit_decision,
    build_trade_intent,
    mark_closed,
)

__all__ = [
    "ExitDecision",
    "PositionInputError",
    "PositionRecord",
    "PositionSide",
    "ProtectionLevels",
    "open_position_from_signal",
    "apply_exit_decision",
    "build_trade_intent",
    "mark_closed",
]
