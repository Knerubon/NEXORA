---
id: PR1-adr035-contract-foundation
status: in_review
owner: DEV (developer role; self-review; independent review pending)
base_sha: c5f00f243dfdf685456509c67d5894242c34b122
branch: claude/pr1-execution-contracts-v1
---

# PR-1 - ADR-035 contracts foundation

Requirements: [requirements](../docs/requirements.md). Architecture: [architecture](../docs/architecture.md).
Contracts: [ADR-035](../docs/decisions/ADR-035-execution-integration-safety-amendment.md) sections 4.1, 4.2, 4.3, 4.5, 5.2, 4.7.

## Step-0 allowed file list (derived from ADR-035 s4.7 + s4.1/4.2/4.3/4.5/5.2)
- `packages/nexora/execution/models.py`
- `packages/nexora/execution/idempotency.py`
- `packages/nexora/autonomous/broker_capabilities.py`
- `packages/nexora/execution/dedup_store.py` (deserialization-only minimum)
- `tests/test_execution_contracts_adr035.py` (new)
- `tasks/PR1-adr035-contract-foundation.md` (this record)
- Not touched: guard.py, reconciler.py, reconciliation.py, broker_adapter.py, close_all*, position/*, authority.py,
  autonomous_contracts.py, execution/__init__.py, ADRs/docs, apps/*, AGENTS.md. No existing test needed migration.

## Delivered
- `ExecutionRequest.new_position_ref` with per-action rules; `build_execution_request` passthrough; key unchanged;
  `derive_new_position_ref` (`pos:<proposal_id>`, FORMAT PENDING ARCHITECT APPROVAL).
- `ResolvedExecution` (type-level invariants only; no signal fields).
- `ExecutionResult`: `requested_quantity: Decimal | None`, `action`, `nexora_position_ref`, per-action invariants.
- `ExecutionInstrumentBinding` type and `ExecutionInstrumentResolver` Protocol signature (no behavior).
- `BrokerCapabilities.volume_step_anchor` + shared `validate_volume`; reason codes pinned equal to broker_adapter.
- `dedup_store.deserialize_result` round-trips the widened result.

## Not delivered (excluded)
OPEN-6 vocabulary, OPEN-9 legacy source, OPEN-15, guard changes (PR-2), dedup behavior (PR-5), adapter/reconciler
adoption (PR-7), `execution/__init__.py` exports (PR-4).

## Notes for reviewer
- Runtime-evidence invariants (CLOSE qty == local == broker; REDUCE < position qty; position OPEN/MANAGING) are pipeline duties.
- ADR silent: whether `volume_step_anchor` must be <= `volume_min`/<= `volume_max`; only finiteness validated.
- Non-finite quantity in `validate_volume` returns `volume_not_multiple_of_step` (fail closed; ADR silent on code).

## Execution record
Self-review; independent review pending. See PR body for commands and results.
