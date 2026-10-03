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
- Release is an optimistic append (journal `expected_count` = events read): any concurrent write
  to the key stream makes it fail, and `release_for_retry` returns True only if this call wrote it.
- Safety net: every state read re-checks ALL released generations; if a released generation's
  latest result (journal order) is not a clean zero-fill REJECTED (e.g. a stale writer appended
  UNKNOWN after the release), the key raises `DedupStoreCorruptError` and is never FIRST_CLAIM.

## Execution record

- Not wired into any execution path (none exists; broker execution remains blocked).
- Review round 1 (CHANGES_REQUESTED, MAJOR fail-open): two interleavings with two store instances on
  one SQLite file made an UNKNOWN-bearing key re-claimable (the earlier claim that the race was
  "bounded by journal first-writer-wins" was wrong). Fixed by the optimistic release plus the
  all-generations fail-closed check above; regression tests for both interleavings fail on the
  previous code (mutation-checked) and pass now.
- Tests use tmp_path SQLite only. dedup_store + execution_contracts + pattern_integration +
  pattern_engine + environment: 287 passed, 1 skipped. ruff check/format and mypy clean on owned files.
- Full suite not run (exceeds 10 min locally). PostgreSQL backend not exercised by these tests
  (follow-up); Journal contract is shared.
- self-review; independent review pending (delta review requested).
