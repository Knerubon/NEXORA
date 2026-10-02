"""Autonomous Trading Core Phase 1 (ADR-033).

Pure, deterministic contracts and fail-closed authority gates. No I/O, no broker
calls, no network/monitoring wiring, no execution path. Nothing here is imported
by research/pipeline.py, research/runtime.py, checkpoint_state.py, apps/api or
apps/web. See docs/decisions/ADR-033-autonomous-trading-contracts-v1.md (status:
PROPOSED at the time this package was written) for the authority model, the
TradeIntent boundary, and the explicit list of what remains PROVISIONAL/BLOCKED.
"""

from nexora.autonomous.authority import (
    AuthorityDecision,
    AuthorityPolicyStatus,
    ExecutionTransmissibility,
    ExistingPositionAuthority,
    NewTradeAuthority,
    ProtectionChange,
    TradingConfig,
    authorize_trade_intent,
)
from nexora.autonomous.broker_capabilities import BrokerCapabilities
from nexora.autonomous.health import (
    HEALTH_AXES,
    Health,
    SystemHealthGate,
    SystemHealthSnapshot,
)
from nexora.autonomous.risk_migration import RiskReductionDecision, RiskReductionProposal

__all__ = [
    "HEALTH_AXES",
    "AuthorityDecision",
    "AuthorityPolicyStatus",
    "BrokerCapabilities",
    "ExecutionTransmissibility",
    "ExistingPositionAuthority",
    "Health",
    "NewTradeAuthority",
    "ProtectionChange",
    "RiskReductionDecision",
    "RiskReductionProposal",
    "SystemHealthGate",
    "SystemHealthSnapshot",
    "TradingConfig",
    "authorize_trade_intent",
]
