# ADR-022 — Startup Recovery Checkpoint V1 (Checkpoint-Assisted Research Recovery)

Status: proposed (pending Rin review)
Date: 2026-09-23
Related: [ADR-017](./ADR-017-production-hardening-local-operations.md) (local operations, restart evidence), [ADR-018](./ADR-018-readiness-corrections.md) (rebuild from the same ordered journal), [environment isolation](../environment-isolation.md) (reserved per-environment `checkpoints` path), [runtime release gates](../research-runtime.md)
Sources: `packages/nexora/research/{runtime,checkpoint}.py`, `packages/nexora/storage.py`, `packages/nexora/experience/service.py`, `apps/api/nexora_api/{research,main}.py`

## Source-of-truth rule

**The Research event journal remains the authoritative source of truth.
Checkpoints are disposable recovery accelerators.
Deleting a checkpoint must never destroy Research history.**

A checkpoint is never written to the journal database, never replaces or rewrites a journal row, and is trusted only after it has been proven to correspond to exact rows of the journal it is restored against. Every doubt resolves to a full journal replay.

## Context — problem and existing recovery behavior

`ResearchRuntime.__init__` calls `_rebuild()` synchronously inside the FastAPI `lifespan`, so the API cannot report `Application startup complete` until recovery ends. `_rebuild()` streams every row of the research stream (`SQLiteJournal.iter_read`, which JSON-parses and SHA-256-verifies each row) and, per row, runs `ResearchPipeline.replay`, `_paper_event` and `ExperienceService.observe`.

Each research row stores the event **and the full pipeline output after it**, which includes every P&F column and transition to date. Row size therefore grows linearly with history, and total recovery work quadratically. Measured on the legacy development journal (`data/research.sqlite`, 51.8 GB, 54,141 events in the main stream): rows grow from 3.3 KB at the first event to 2,446.9 KB at the last.

Baseline measurement (unmodified `origin/main` `efeef59`, real 5,000-event prefix of that journal, see *Benchmark*): full replay took 423–466 s, of which **93% was `ExperienceService.observe`** re-freezing and re-hashing each stored output, 4–5% journal read/verify, and <1% the P&F/structure/trendline/signal engines. Recovery time therefore grows super-linearly with lifetime history.

## Decision 1 — What a checkpoint captures

The complete replay-derived in-memory state of one research stream after journal row `last_sequence`:

- `engine` — the `ResearchPipeline` (matrix/P&F runners, structure, trendline, regime, signal engines, seen-identity map, last output);
- `events` — the runtime's ordered event list (used for dedupe, `expected_count` and the API);
- `experience` — `ExperienceService` replay memory (`last`, `pending`, `states`, `samples`, `completed`, `seen`) via two additive methods, `checkpoint_state()`/`restore_state()`.

Not captured: `PaperSession`, which already rebuilds from its own journal stream at construction, unchanged.

Serialization is `pickle` (protocol 5) of these live objects. No engine gains a new serialization contract, so P&F, Trendline, Signal, Entry Readiness, Matrix and Experience logic is untouched (scope guard). Opaque pickles are made safe for this use by Decisions 3 and 4, and their security boundary is stated in *Risks*.

## Decision 2 — Format, location and versioning

One file per research stream, `<environment.checkpoints>/research-<config hash>.checkpoint`: a single JSON header line followed by the pickled blob.

Header: `format` (`nexora-research-checkpoint`), `schema_version` (1), `environment`, `stream`, `code_fingerprint`, `last_sequence`, `last_event_key`, `last_content_hash`, `event_count`, `state_hash`, `blob_hash`, `blob_size`, `created_at` (informational only).

- `last_sequence` is the journal's own ordering key (`research_journal.sequence`, the SQLite `INTEGER PRIMARY KEY AUTOINCREMENT` that `iter_read` already orders by). Recovery resumes strictly after it: `WHERE sequence > last_sequence`.
- `code_fingerprint` is SHA-256 over every `.py` file of the installed `nexora` package, the interpreter version, and the format/schema version. **Any code change invalidates every checkpoint**, because state captured by different code could differ from a full replay by the current code. This deliberately preserves today's rule that a newer engine rebuilds research state from the journal.
- The stream name already binds the full `RuntimeConfig` hash.

The directory is the per-environment `NEXORA_CHECKPOINT_PATH` reserved by ENV1 (default `<runtime root>/checkpoints`), which `Environment.resolve()` already confines under the environment root. The journal database is not modified: **no schema change, no migration**.

## Decision 3 — Restore algorithm and validation

```
load <env>/checkpoints/<stream>.checkpoint           missing → full replay (no_checkpoint)
parse header                                         → header_corrupt
format/schema_version == current                     → schema_version_mismatch
environment == this environment                      → environment_mismatch
stream == this stream                                → stream_mismatch
code_fingerprint == current                          → code_fingerprint_mismatch
len(blob) == blob_size and sha256(blob) == blob_hash → blob_hash_mismatch   (before unpickling)
journal row at last_sequence == (last_event_key, last_content_hash) → event_reference_invalid
journal row count through last_sequence == event_count              → event_count_mismatch
unpickle                                             → decode_failed
engine type, len(events), events[-1] identity, canonical hash of engine snapshot == state_hash
                                                     → state_validation_failed
adopt state; replay journal rows with sequence > last_sequence
```

Any other exception resolves to `checkpoint_corrupt`. State is adopted only after every check passes; on any failure the runtime starts from empty state and replays the whole journal. A checkpoint that claims more events than the journal holds, belongs to another journal, or to another environment, cannot pass the row-anchor checks.

## Decision 4 — Creation policy and consistency rule

A checkpoint is written only when every side effect of every event it covers has been committed, i.e. only from state produced by a *completed* rebuild plus *successful* ingests:

- after a completed recovery that replayed at least one row (including every full-replay fallback, which rewrites a fresh valid checkpoint);
- every `DEFAULT_CHECKPOINT_INTERVAL` = 100 successful live ingests, bounding a crash restart's delta;
- on graceful API shutdown (`lifespan` finally, after the quote feed stops), so a normal restart replays no delta.

An interrupted rebuild can stop inside the last journal row (research row committed, Experience/paper side effects not), which the journal's `expected_count` guard cannot detect. Therefore:

- no checkpoint is written until a later rebuild completes (`_recovered`);
- `ingest` first completes recovery before processing an event whenever the previous rebuild did not complete. Without this, the next live event could write Experience outcomes computed from incomplete memory, permanently conflicting with the journal (found and covered by a regression test).

A checkpoint write failure is logged and never fails research processing.

## Decision 5 — Crash safety

Written to `<name>.<pid>.tmp`, flushed and `fsync`ed, then atomically `os.replace`d over the previous file. A crash before or during the write leaves the previous checkpoint intact. A leftover `*.tmp` is never read. A torn or partial file fails `blob_size`/`blob_hash`. A crash after the write simply restores it.

## Decision 6 — DEV/PROD isolation

- The store directory comes only from `Environment.resolve()` of the running process (`configured_checkpoints`), which already rejects writable paths outside the environment root and hard-linked shared storage.
- The header carries the environment name and must match.
- Independently, the row anchors bind a checkpoint to its own journal's exact rows, so even a copied file with a forged environment is rejected.

## Decision 7 — Operations

- `NEXORA_RESEARCH_CHECKPOINTS=on|off` (default `on`; any other value fails startup with `invalid_research_checkpoints`). `off` restores exactly the pre-change full-replay path.
- Callers that construct `ResearchRuntime` without `checkpoints=` (CLI, tests, backtests) keep full replay unchanged.
- Journals without row anchors (`PostgresJournal` in V1) use full replay (`journal_not_anchored`).
- Startup logs name the path taken, for example: `Research recovery: checkpoint found checkpoint_sequence=… events=…`, `…: restored checkpoint at sequence …`, `…: checkpoint invalid reason=…; falling back to full replay`, `…: completed mode=checkpoint replayed=… events=… in … ms`, `Research checkpoint: written sequence=… events=… bytes=… in … ms`. State objects are never logged.

## Correctness invariant

`STATE(full replay of journal) == STATE(checkpoint + delta replay)`: runtime snapshot, event list, research signals, Experience memory and paper snapshot compare equal, and journal rows are byte-identical before and after recovery. Proven by `tests/test_recovery_checkpoint.py` with and without paper, with a committed BUY signal whose pending Experience lives inside the checkpoint, and after further live events.

## Benchmark

Environment: development workstation (Windows 11, Python 3.13.3), measured with `time.monotonic()`. Data: verbatim global-sequence prefixes of the real legacy development journal `D:\NEXORA\NEXORA\data\research.sqlite` (opened read-only), copied into scratch space. Each copy equals that journal exactly as it stood when its main stream held 5,000 or 5,100 events. PROD data was not read. "Before" is unmodified code (tree identical to `origin/main` `efeef59`); "after" is this change. Each database had one warm-up start first, so measured runs do not include one-time Experience row writes.

| 5,100-event journal (550.9 MB) | Events replayed | Recovery | App startup (lifespan) |
|---|---|---|---|
| Before: full replay | 5,100 | 338,822 ms | 278,790 ms |
| After: checkpoint @5,000 + delta | 100 | 23,769 ms | — |
| After: warm restart (checkpoint @5,100) | 0 | 277 ms | 316 ms |

Other measured runs:
- 5,000-event journal, before: 408,780 ms (warm-up), then 423,136 ms and 466,494 ms. Breakdown of the 423,136 ms run: Experience observe 394,415 ms, journal read/verify 18,794 ms, engine replay 2,539 ms.
- 5,000-event journal, after, first start without a checkpoint (the *first start after upgrade* path): 254,073 ms full replay, then a 6,818,049-byte checkpoint written in 253 ms.
- An earlier before-app-startup run of 527,131 ms on the 5,000-event journal overlapped a test run, so it is excluded as contended.

Run-to-run variance on this workstation is large (the same full replay measured 254–466 s), so compare rows measured on the same journal. Improvement on the 5,100-event journal:
- warm restart: replay −100% (5,100 → 0 events), recovery −99.92% (338,822 → 277 ms), app startup −99.89% (278,790 → 316 ms);
- crash-style restart with a 100-event delta: replay −98.04%, recovery −92.98% (338,822 → 23,769 ms), of which 22,762 ms is Experience observe over the 100 delta rows.

Warm restart cost is now a checkpoint load and verification (about 0.3 s at 5,100 events) instead of a replay of lifetime history. It still grows with state size, not with replay work.

## Known limitations

- **First start after any code change is a full replay** (fingerprint mismatch), as slow as today, and then writes a fresh checkpoint.
- **Delta cost per event is unchanged**: at large history each replayed delta row still costs what it costs today (dominated by Experience re-hashing of the stored output), so the 100-event interval bounds but does not remove crash-restart cost.
- **The root growth is not addressed**: every research row still stores the full cumulative output, so journal size and per-event ingest cost keep growing with history (54,141 events → 51.8 GB). This is a separate, larger decision (what the journal stores) outside this infrastructure-only change.
- Checkpoint size grows with history (engine state + event list), and each periodic write serializes it under the runtime lock.
- Pickle ties a checkpoint to the exact interpreter and code (by design, via the fingerprint). It is not a portable or long-term format.
- Recovery via a checkpoint no longer re-verifies the SHA-256 of rows it skips. Full verification remains available with `NEXORA_RESEARCH_CHECKPOINTS=off` or by deleting the checkpoint.
- PostgreSQL journals are not accelerated in V1.

## Risks

- **Pickle trust boundary**: loading a checkpoint executes pickle. Bytes are integrity-checked (`blob_hash`) against a header in the same file, which protects against corruption, not against an attacker. Anyone able to write the environment's runtime directory could therefore run code as the API user. That is the same OS account that already owns the code worktree and `.venv`, so no new privilege is crossed. The directory must remain local, non-shared and never exposed remotely. Requires security review per AGENTS.md (persistence).

## Rollback

Set `NEXORA_RESEARCH_CHECKPOINTS=off`, or revert the PR. The journal was never modified, so no data migration is needed either way; checkpoint files may be deleted at any time.

## Future improvements

Stop storing cumulative output per journal row (root cause of growth); make Experience replay avoid re-hashing stored outputs; explicit versioned state contracts per engine instead of pickle; anchor support for `PostgresJournal`; writing checkpoints outside the runtime lock.
