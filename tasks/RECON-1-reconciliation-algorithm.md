---
task: RECON-1
status: in_review
depends_on: ["docs/decisions/ADR-034-execution-contracts-v1.md (accepted)"]
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-034-execution-contracts-v1.md"]
translation_needed: false
---

# RECON-1 — Pure reconciliation classifier

base_commit: bc4cae2abb6539176c8d3cf3b7f0765ad1a6d1ec
branch: claude/recon-v1
worktree: D:\NEXORA\NEXORA-RECON

## Scope

ADR-034 section 7 / section 11 item 4: a pure classifier comparing durable local
`PositionRecord`s against a broker-agnostic snapshot. Classification only: no broker query,
no I/O, no mutation, no remediation. Uses only the frozen findings; status is derived by the
frozen `ReconciliationRecord` table. No frozen contract changed; `execution/__init__.py`
exports are left to the integrator.

## Deliverables

- `packages/nexora/execution/reconciler.py`: `BrokerPositionSnapshot`, `BrokerSnapshot`,
  `classify_reconciliation`, `aggregate_reconciliation_status`, `ReconcilerInputError`.
- `tests/test_execution_reconciler.py`.

## Decisions recorded (not contract changes)

- Local `PositionRecord.symbol` is treated as the canonical `instrument_id`.
- Every non-`CLOSED` local state is expected on the broker (fail closed; EXIT_PENDING with
  broker missing is reported as `LOCAL_OPEN_BROKER_MISSING`).
- Matching is by the snapshot's optional `nexora_position_ref`; unattributed or unknown-ref
  broker positions are broker-only and never claimed as NEXORA-owned.
- Instrument/side conflict on a matched ref reports both `LOCAL_OPEN_BROKER_MISSING` and
  `BROKER_POSITION_LOCAL_MISSING`.
- No snapshot yields no comparison records; aggregate of nothing is UNKNOWN. Flat/flat yields
  an explicit MATCH. Aggregate precedence UNKNOWN > UNSYNCHRONIZED > SYNCHRONIZED.
- Duplicate local ids / broker refs raise `ReconcilerInputError`.

## Execution record

Role: developer (self-review; independent review pending). See PR for test evidence.
