# FIX2 API startup recovery

status: in_review
translation_needed: false
base_commit: 81854be

User requests urgent restoration of local web/feed. Root AGENTS.md applies.
Inputs: docs/requirements.md, docs/architecture.md, docs/research-runtime.md;
packages/nexora/{storage,artifacts}.py, research/{runtime,pipeline}.py;
apps/api/nexora_api/{main,research,quotes}.py; tests/test_readiness_regressions.py,
tests/test_postgres_journal.py; skills/{testing,postgres}/SKILL.md.

Observed: API blocked in SQLiteJournal.read -> _verified -> canonical_hash before
runtime replay. Existing journal is 3.12 GB with 14,796 active-scope events.
Use bounded verified iteration during recovery, preserving all events, order,
identity/hash verification and engine/paper semantics. No reset, pruning, migration,
feed-parameter changes, orders, merge, tags or push. Work isolated from main.
Validate corruption rejection, iterator boundaries, restart equivalence and suite.
Self-review; independent review pending.

## Status reconciliation (2026-09-27, `main` `8437cdc`)

- The bounded verified journal iteration landed in commit `c902721` and was merged to `main` through PR #22 (merge `f5f388f`, 2026-09-21). Status `in_progress` → `in_review`.
- Review evidence found in the task record and PR: self-review only; no independent review is recorded. Status stays `in_review` because AGENTS.md §17 requires required-review evidence for `done`. This is merged work, not active work; do not re-implement it.
- Startup recovery was later extended by Startup Recovery V1 (PR #32, ADR-022) and PERF-1 (PR #35, ADR-029).
- Index: [roadmap status ledger](../docs/roadmap.md#status-ledger-2026-09-27).
