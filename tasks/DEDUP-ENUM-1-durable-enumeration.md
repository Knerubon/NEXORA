# DEDUP-ENUM-1 - Durable enumeration of unresolved dedup keys

Status: Option A IMPLEMENTED (draft PR, self-review; independent + Security review pending). The sections below up to
"Decision and implementation" are the original proposal, kept for history.
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

## Decision and implementation (Option A)
Rin decision (frozen): Option A, durable index stream. The index is DISCOVERY METADATA, NOT execution truth. Required flow:
index -> candidate key -> authoritative `inspect()`/`state()` -> decision. Execution permission is never derived from the
index. If completeness/integrity cannot be established: FAIL CLOSED.

Design (`execution/dedup_store.py` only; storage.py untouched, Journal Protocol only):
- Index stream `execution-dedup-index` (no ':' so it cannot collide with `execution-dedup:<key>`). Rows: `key#<k>` =
  `{event: index_register, idempotency_key: k}` and `genesis` = `{event: index_genesis, version: 1}`. Same Journal envelope/hash,
  first-writer-wins, no `expected_count` => concurrent writers/processes lose no registration; re-registering is a no-op.
- `claim()` registers the key BEFORE the first claim row (index first, claim second). A crash between them leaves an index
  entry for an UNCLAIMED key (harmless, not enumerated). Index write failure => `DedupStoreIOError`, no claim written.
  claim/attempt/abort/result/release outcomes are otherwise unchanged (all pre-existing dedup tests pass unmodified).
- Accessor (name/type FLAGGED for Rin to freeze; minimal choice): `enumerate_unresolved() -> tuple[DedupKeyStatus, ...]`,
  sorted by key. Includes RESULT_UNSAFE, QUARANTINED, ATTEMPTED_NO_RESULT, CLAIMED_NOT_ATTEMPTED, ABORTED_NEVER_ATTEMPTED
  (still claimed, OPEN-14) and ever-UNKNOWN in the current generation; excludes UNCLAIMED and cleanly released/clean-rejected.
- Fail closed: index I/O => `DedupStoreIOError`; malformed/duplicate/hash-failed index row => `DedupStoreCorruptError`; per-key
  read I/O => propagates; per-key integrity violation => key reported QUARANTINED; genesis missing =>
  `DedupEnumerationIncompleteError` (new sibling under `DedupStoreError`). Never a partial list.
- Extra admin/audit methods: `register_known_keys(keys)`, `establish_index_genesis(legacy_keys=())`,
  `unindexed_claims_among(keys)` (caller-scoped audit: claimed but not indexed).
- `unresolved_among` remains documented CALLER-SCOPED / non-global.

### Legacy-key question (RECORDED FOR RIN)
Streams claimed before this change have no index row and cannot be listed (Journal cannot list streams). Mechanism: enumeration
refuses (`dedup_index_genesis_missing`) until an operator runs the one-time `establish_index_genesis(legacy_keys)`, which registers
the supplied legacy keys and then writes the genesis marker. Residual limits (cannot be closed without Option B / a policy step):
1. Genesis is an operator ASSERTION; the store cannot verify the legacy key list is complete. A forgotten legacy key stays invisible
   (auditable only via `unindexed_claims_among` for keys someone names).
2. Pre-index code still claiming after genesis voids completeness (deployment rule: no pre-change writers once genesis exists).
3. Deleting rows from the index (truncation) is not detectable beyond per-row hashes; a claimed key without an index row is only
   discoverable via known keys. Needs Security review.
Rin to confirm: (a) acceptable operator process for the legacy list (source of the list for PROD), (b) accessor name/type,
(c) that fresh/dev stores call `establish_index_genesis()` at bootstrap. No RIN_DECISION_REQUIRED on retry/idempotency semantics: none changed.

### PR-4 consumption rule (OPEN-16 gate)
Call `enumerate_unresolved()`; the gate MUST then consume each returned status (already `inspect()`-derived) and block globally on
RESULT_UNSAFE, QUARANTINED, ATTEMPTED_NO_RESULT, CLAIMED_NOT_ATTEMPTED, ABORTED_NEVER_ATTEMPTED and ever-UNKNOWN; and deny (fail
closed) on ANY `DedupStoreError` from enumeration (I/O, corrupt, genesis missing). An empty result is "none unresolved" only when
no exception was raised. The index never grants permission; the per-key `inspect()` remains the only authority.
