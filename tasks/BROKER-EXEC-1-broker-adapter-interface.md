---
task: BROKER-EXEC-1
status: in_review
depends_on: ["ADR-034 accepted (main bc4cae2)"]
agents: ["developer"]
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-034-execution-contracts-v1.md"]
translation_needed: false
---

# BROKER-EXEC-1 — BrokerExecutionAdapter interface + simulated adapter

base_commit: bc4cae2abb6539176c8d3cf3b7f0765ad1a6d1ec
branch: claude/broker-exec-v1
worktree: D:\NEXORA\NEXORA-BROKER-EXEC

## Scope (deliberately non-transmitting)

- `packages/nexora/execution/broker_adapter.py`: `BrokerExecutionAdapter` Protocol and `SimulatedBrokerAdapter`.
- No MT5 adapter, no broker/network import, no wiring to apps/api, UI, Execution Guard or Risk.
- Real adapter binding stays blocked by governance (ADR-034 section 11; AGENTS.md section 0).

## Execution record

- Role: developer; self-review; independent review pending.
- Tests: `tests/test_execution_broker_adapter.py` (every ExecutionStatus, UNKNOWN-on-timeout, retry interplay, idempotent resubmit, volume min/max/step, simulation-only refusal, architecture no-broker-import / no order-transmission-call test).
- Known limitation: `ExecutionResult` requires a positive `requested_quantity`, so the simulated adapter refuses quantity-less requests (CLOSE without quantity, MODIFY_PROTECTION) with `BrokerAdapterError`. Representing them needs a frozen-contract change (ARCHITECT DECISION REQUIRED, not made here).
- `execution/__init__.py` is unchanged; the adapter is imported from `nexora.execution.broker_adapter`.
