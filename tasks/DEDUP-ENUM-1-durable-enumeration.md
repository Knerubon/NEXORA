# DEDUP-ENUM-1 - Durable enumeration of unresolved dedup keys (PLAN ONLY)

Status: PROPOSAL FOR RIN. Nothing here is decided; no implementation exists or is authorized by this file.
Authority: ADR-035 s3.8 / W5 / OPEN-16 / OPEN-9 (docs/decisions/ADR-035-execution-integration-safety-amendment.md).

## Problem
- The `Journal` Protocol (`packages/nexora/storage.py:17-25`) has `append/read/iter_read/close` only. It cannot list streams,
  so the dedup store cannot answer "which keys are unresolved?" globally.
- `unresolved_among(keys)` (`execution/dedup_store.py`) is CALLER-SCOPED: keys the caller does not name are invisible. It
  MUST NEVER be used as global safety proof. It also reports only ATTEMPTED_NO_RESULT / ever-UNKNOWN, not RESULT_UNSAFE
  or QUARANTINED.
- The OPEN-16 forced gate ("any unresolved key blocks every action globally") therefore cannot be established today.

## Requirements
1. Backend-independent (SQLite and Postgres journals, in-memory store); no backend-specific SQL in the dedup layer.
2. Durable: survives restart and crash; an in-process cache is never the source of truth.
3. Enumerates every key currently in: RESULT_UNSAFE, QUARANTINED, ATTEMPTED_NO_RESULT, CLAIMED_NOT_ATTEMPTED
   (+ ever-UNKNOWN in the current generation, ADR s3.8). Aborted keys are included while still claimed (OPEN-14).
4. Deterministic (stable sorted order, no time/randomness).
5. Fail closed: enumeration I/O failure, an incomplete/unverifiable index, or any index/stream disagreement raises
   (`DedupStoreIOError` / `DedupStoreCorruptError`); it never returns a partial list as if complete. Callers deny.
6. A key can never be missing from the enumeration while its stream has a claim (no claim-without-index window).
7. Read side re-derives state per key through `inspect()` (authoritative), using the index only for discovery.

## Options
| # | Option | Pros | Cons / risk |
|---|---|---|---|
| A | Durable index stream (e.g. `execution-dedup-index`) holding one registration row per key, appended BEFORE the first `claim#0` | No storage.py change; backend-independent; append-only; reuses Journal first-writer-wins | Two streams cannot be written atomically: crash after index write and before claim leaves an index entry with no claim (harmless: `inspect` = UNCLAIMED, fail-safe). Order must be index first, claim second (never reverse). Index grows one row per key; read is O(keys) |
| B | Extend `Journal` Protocol with `list_streams(prefix)` | Exact, no duplicate bookkeeping, no ordering hazard | Touches `storage.py` (not owned by this track); SQLite and Postgres implementations plus every test double change; Protocol change affects all journal users |
| C | Separate registry (own table/file) | Can carry per-key summary for O(unresolved) reads | New persistence + migration; second source of truth that can disagree with the journal; highest review/ops cost; dual-write atomicity problem |
| D | Caller-maintained key set (status quo) | None | Not global proof; rejected by requirement 6 |

## Files / ownership
- A: `execution/dedup_store.py` only (+ new tests `tests/test_execution_dedup_enum*.py`). Owner: execution/dedup track (DEV).
  Needs a Rin/Architect decision on the index stream name and ordering rule; Security review (persistence).
- B: `packages/nexora/storage.py` (Journal Protocol, `SQLiteJournal`, `PostgresJournal`) plus test doubles
  (`FlakyJournal` in `tests/test_execution_dedup_attempt_abort.py`). storage.py is NOT owned by this track: needs
  storage/PERF-owner coordination and an ADR note (shared contract, AGENTS.md s3).
- C: new module + migration; Architect ADR required.
- All options: `ExecutionDedupStore` Protocol gains one accessor (name/result type to be frozen by Rin, e.g.
  `enumerate_unresolved() -> tuple[DedupKeyStatus, ...]`); consumers: PR-4 OPEN-16 gate, health `reconciliation` axis (INV-12).

## Tests required (any option)
- Enumerates each of RESULT_UNSAFE, QUARANTINED, ATTEMPTED_NO_RESULT, CLAIMED_NOT_ATTEMPTED, ever-UNKNOWN; excludes
  UNCLAIMED and clean-released keys.
- Deterministic order; identical on SQLite and in-memory; survives close/reopen.
- Crash injection between the two writes (option A): a claimed key is never absent from the enumeration.
- Enumeration I/O failure / truncated index / index entry whose stream is corrupt => raises, never partial.
- A key unknown to any caller-supplied set is still found (the property `unresolved_among` lacks).
- Concurrency: two processes claiming new keys; no lost registration.

## Recommendation (PROPOSAL, not a decision)
Option B is the cleanest long-term (single source of truth, no ordering hazard) but crosses an unowned shared contract.
Option A is the smallest self-contained step and is fail-safe if the index-before-claim order is enforced and tested.
Suggested: Rin decides between A (execution/dedup track, owner DEV-EXEC) and B (storage owner, Architect-coordinated ADR).
Until decided, the OPEN-16 gate stays blocked (ADR open item 9) and `unresolved_among` stays documented as non-global.
