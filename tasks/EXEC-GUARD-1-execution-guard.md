---
id: EXEC-GUARD-1
status: in_review
owner: DEV-ENTRY (execution pipeline)
base_sha: bc4cae2abb6539176c8d3cf3b7f0765ad1a6d1ec
branch: claude/exec-guard-v1
---

# EXEC-GUARD-1 - Execution Guard

Requirements: [requirements](../docs/requirements.md). Architecture: [architecture](../docs/architecture.md).
Contracts: [ADR-034](../docs/decisions/ADR-034-execution-contracts-v1.md), ADR-033 sections 11 and 21.

## Scope
Pure, deterministic, fail-closed `evaluate_execution_guard` in `packages/nexora/execution/guard.py`
(Authority -> Execution Guard -> ExecutionRequest). No I/O, broker, MT5, network, DB or journal code.
Duplicate-order checking is out of scope (idempotency store track); the request carries the
deterministic idempotency key only.

## Rules
- Authority must be AUTHORIZED and TRANSMITTABLE (typed fields; reason codes never parsed).
- AUTO and SHADOW never yield a request; ASSISTED OPEN requires confirmation.
- OPEN blocked unless reconciliation is SYNCHRONIZED; OPEN blocked by kill switch.
- Risk-reducing intents are not blocked by reconciliation or kill switch alone.
- Any missing/invalid input denies; request built only via `build_execution_request`.

## Open points for Architect
- Kill switch is treated as blocking OPEN only (no frozen contract defines more).
- No kill-switch contract exists; the guard takes a plain `bool`.

## Execution record
- Self-review; independent review pending.
- Tests: `tests/test_execution_guard.py` (see PR for command results).
- Runtime impact: none (module not imported by runtime/API/web; `execution/__init__` unchanged).
