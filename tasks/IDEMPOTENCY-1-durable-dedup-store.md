---
task: IDEMPOTENCY-1
status: in_review
depends_on: ["ADR-034 accepted (frozen idempotency key)"]
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-034-execution-contracts-v1.md"]
translation_needed: false
---

# IDEMPOTENCY-1 - Durable duplicate-order-safety store

base_commit: bc4cae2abb6539176c8d3cf3b7f0765ad1a6d1ec
branch: claude/idempotency-v1
worktree: D:\NEXORA\NEXORA-IDEMPOTENCY

## Scope

Implements the store deferred by ADR-034 sections 6/11. The frozen key
`execution_request_idempotency_key` is unchanged. New module
`packages/nexora/execution/dedup_store.py`: `ExecutionDedupStore` Protocol,
`InMemoryExecutionDedupStore`, and `JournalExecutionDedupStore` over the existing
`nexora.storage.Journal` (SQLite/PostgreSQL; append-only, hash-verified, first-writer-wins).
No migration or schema change: the journal table already exists.

## Semantics

- claim: FIRST_CLAIM for exactly one caller, DUPLICATE otherwise (also after restart).
- Only a clean zero-fill REJECTED (`is_safe_to_retry_without_reconciliation`) can be released;
  a release starts a new generation, whose own results govern further releases.
- UNKNOWN/ACCEPTED/PARTIALLY_FILLED/FILLED never reclaimable; claim without result stays claimed.
- Unreadable/inconsistent/unavailable storage raises `DedupStoreCorruptError` (fail closed).

## Execution record

- Not wired into any execution path (none exists; broker execution remains blocked).
- Tests use tmp_path SQLite only. `tests/test_execution_dedup_store.py` + existing
  `tests/test_execution_contracts_v1.py`: 95 passed. ruff check/format and mypy clean on owned files.
- Full suite not run (exceeded 10 min in this environment); no other file changed.
- self-review; independent review pending.
