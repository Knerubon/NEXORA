"""Execution Contract Freeze V1 (ADR-034).

Pure, deterministic, broker-agnostic contracts for the execution pipeline
that will eventually sit between Risk/Authority and a real broker adapter:

    Manual UI / Future AUTO -> TradeIntent -> Risk -> Authority ->
    Execution Guard -> ExecutionRequest -> Broker Adapter -> ExecutionResult ->
    Reconciliation -> Position Supervisor / Journal

No I/O, no broker calls, no MT5 import, no network/monitoring wiring, no
execution path. AUTO remains unavailable; broker execution remains disabled;
Manual UI remains execution-locked (AGENTS.md section 0/9; ADR-033 section 21).
See docs/decisions/ADR-034-execution-contracts-v1.md for the full rationale and
the explicit list of what remains PROVISIONAL or BLOCKED.
"""

from nexora.execution.close_all import build_manual_close_all_intents, owned_open_positions
from nexora.execution.idempotency import (
    build_execution_request,
    execution_request_idempotency_key,
    trade_intent_identity,
)
from nexora.execution.models import (
    ExecutionContractError,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    PriceConstraint,
    ProtectionRequest,
    is_safe_to_retry_without_reconciliation,
)
from nexora.execution.reconciliation import (
    ReconciliationFinding,
    ReconciliationRecord,
    ReconciliationStatus,
)
from nexora.execution.reconciliation import blocks_new_trade as reconciliation_blocks_new_trade

__all__ = [
    "ExecutionContractError",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "PriceConstraint",
    "ProtectionRequest",
    "ReconciliationFinding",
    "ReconciliationRecord",
    "ReconciliationStatus",
    "build_execution_request",
    "build_manual_close_all_intents",
    "execution_request_idempotency_key",
    "is_safe_to_retry_without_reconciliation",
    "owned_open_positions",
    "reconciliation_blocks_new_trade",
    "trade_intent_identity",
]
