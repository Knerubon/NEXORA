# ADR-029 — PERF-1 Recovery Snapshot / Checkpoint V1: gap analysis and hardening

Status: **draft** (PERF-1 Phase 1; Rin architecture review pending; no implementation approved)
Date: 2026-09-25
Role: DEV-PERF (inspection + architecture only; self-review, independent review pending)
Base: `origin/main` `4f69e9c` (PR #32 merged)
Related: [ADR-022](./ADR-022-startup-recovery-checkpoint-v1.md) (checkpoint-assisted recovery, merged), [ADR-018](./ADR-018-readiness-corrections.md), [ADR-017](./ADR-017-production-hardening-local-operations.md), [environment isolation](../environment-isolation.md), [runtime release gates](../research-runtime.md). In flight, not on `main`: ADR-027 Research Journal Payload V2 (`claude/research-journal-payload-v2`), ADR-028 Experience snapshot additive fields (uncommitted in `NEXORA-EXPERIENCE-COMPAT`), ADR-023/024 Pattern Engine (`claude/pnf-pattern-engine-v1`).

## 0. Summary for the reviewer

The PERF-1 brief asks for a *Snapshot + Incremental Replay* design. **Repository evidence shows that design already exists on `main`.** ADR-022 / PR #32 (`f3f2938`, `6ba4f35`, merged as `4f69e9c`) implements checkpoint restore and replays only `sequence > last_sequence`, with full-replay fallback and 30 dedicated tests. ADR-022 has the exact safety model this brief asks for.

This ADR therefore **does not propose a second checkpoint mechanism**. Doing so would duplicate ADR-022 and break AGENTS.md §3/§7. Instead it:

1. maps each of the 14 required safety properties to the existing implementation and evidence (§4, §8);
2. identifies the gaps that still make startup slow or fragile in practice (§3);
3. proposes a scoped hardening increment (V1.1) on top of ADR-022's frozen format, each item gated by an open decision (§5, §15).

The biggest finding is that **ADR-022 does not help the startups that matter most today**:

- Every code deployment invalidates every checkpoint (G1).
- The Windows launcher stop is a hard kill, so the shutdown checkpoint never runs (G2).
- PROD runs `9016004`, which predates checkpoints, and newer code cannot recover its journal until ADR-028 lands (dependency, §14).

## 1. Current problem

`ResearchRuntime.__init__` runs `_rebuild()` synchronously inside the FastAPI `lifespan`, so the API is not ready until recovery ends. A full replay costs super-linear time in lifetime history. Each research row stores cumulative output, and `ExperienceService.observe` re-canonicalizes and re-hashes that output for every row.

Evidence already recorded in ADR-022 (legacy development journal prefix, Windows 11, Python 3.13.3):

| Measurement | Value |
|---|---|
| Full replay, 5,000 events | 423,136 ms; 93% `ExperienceService.observe`, 4–5% journal read/verify, <1% engines |
| Full replay, 5,100 events | 338,822 ms (run-to-run variance 241–466 s on the same data) |
| Warm restart from checkpoint @5,100 | 1,359–1,452 ms |
| Checkpoint @5,000 + 100-event delta | 24,191 ms (dominated by Experience observe on the delta) |
| Row size growth | 3.3 KB at first event, 2,446.9 KB at event 54,141 (51.8 GB legacy journal) |

ADR-027 re-measured the replay split independently with cProfile on synthetic data: Experience 96%, engines about 1%.

These figures are **historical evidence, not a baseline for this ADR**. §11 defines a fresh synthetic baseline, which must be measured before any target is set.

## 2. Repository evidence (inspected at `4f69e9c`)

| Area | Evidence |
|---|---|
| Event persistence | `packages/nexora/storage.py`: `Journal` protocol; `SQLiteJournal` (`research_journal`, `sequence INTEGER PRIMARY KEY AUTOINCREMENT`, `stream`, `event_key`, `content_hash`, `payload`); `PostgresJournal` (`sequence BIGINT GENERATED ALWAYS AS IDENTITY`). Rows are immutable, and each payload is SHA-256 verified on read. `append(..., expected_count=)` is an optimistic single-writer guard. Migration `infra/migrations/007_research_journal.sql`. |
| Ordering / identity | Order = journal `sequence`, read with `ORDER BY sequence` in bounded pages of 32 (FIX2). Identity = `(stream, event_key)`, with `content_hash` per row. Stream = `research:` + `canonical_hash(RuntimeConfig)`. |
| Recovery path | `packages/nexora/research/runtime.py` `_rebuild()`: optional `_restore()`, then `iter_rows(after=last_sequence)`, which per row runs `engine.replay`, `_events.append`, `_paper_event` and `experience.observe`. Afterwards it sets `_recovered`, logs, and writes a checkpoint. |
| State rebuilt | `ResearchPipeline` (Matrix, AdaptivePnfRunner×N, PnfEngine, Structure, Trendline, Regime, Signal), runtime `_events`, and `ExperienceService` memory (`last`, `pending`, `states`, `samples`, `completed`, `seen`). `PaperSession` rebuilds itself from its own stream. |
| Checkpoint mechanism | `packages/nexora/research/checkpoint.py` (store, header, `verify`, `code_fingerprint`) and `checkpoint_state.py` (explicit typed JSON state, `COVERED_FIELDS`). Wired in `apps/api/nexora_api/research.py` `configured_checkpoints()` (`NEXORA_RESEARCH_CHECKPOINTS=on\|off`, default on). |
| Checkpoint triggers | End of a rebuild that replayed ≥1 row; every 100 successful live ingests (`DEFAULT_CHECKPOINT_INTERVAL`); and `lifespan` `finally` → `engine.checkpoint()` (`apps/api/nexora_api/main.py:182`). |
| Startup lifecycle | `lifespan`: journal → `configured_runtime` (blocking recovery) → `BacktestLabService.bootstrap` → feed start → publisher. Shutdown: publisher cancel → feed stop → checkpoint → journal close. |
| Launcher stop | `apps/api/nexora_api/launch.py` `stop_owned()` calls `psutil` `terminate()`/`kill()`. On Windows this is `TerminateProcess`, so `lifespan` `finally` does not run and **no shutdown checkpoint is written**. |
| DEV/PROD separation | `Environment.resolve()` confines `NEXORA_CHECKPOINT_PATH` (default `<root>/checkpoints`) under the environment root. The header carries `environment` and must match. Row anchors bind a checkpoint to its own journal's rows. |
| Observability | Log lines only (`uvicorn.error`). `ResearchRuntime.last_recovery` is populated but **not exposed** by any endpoint. `/operations/readiness` does not report the recovery mode. |
| Tests | `tests/test_recovery_checkpoint.py` (30 tests), `tests/test_startup_recovery.py` (4), `tests/test_postgres_journal.py`, `scripts/recovery_drill.py`. |
| Governance | ADR-022 header still says `Status: proposed — revision 2` although the implementation is merged. |

## 3. Gaps that remain after ADR-022

| # | Gap | Evidence | Effect |
|---|---|---|---|
| G1 | `code_fingerprint` hashes **every `.py` file** of the `nexora` package plus `sys.version` | `checkpoint.py:66-78` | Any code change anywhere (docs-adjacent helpers, API-unrelated modules) forces a full replay on the next start. Every deployment pays the full super-linear replay. That is the most frequent slow start. |
| G2 | Shutdown checkpoint depends on graceful `lifespan` exit, but the launcher hard-kills on Windows | `launch.py:37-59`, `main.py:179-182` | A normal operator restart replays up to 99 delta rows, and each costs what a full-replay row costs at that history size. |
| G3 | Only count-based periodic checkpoints (100 ingests) | `runtime.py:22,272` | Under a slow feed, the uncovered delta can stay open for a long time. There is no time/idle trigger. |
| G4 | `PostgresJournal` does not implement `AnchoredJournal` | `storage.py:160-231`, `runtime.py:75-77` | PostgreSQL, the documented primary target, always does a full replay (`journal_not_anchored`). |
| G5 | The checkpoint is written under the runtime `RLock` | `runtime.py:164-209,272` | Ingest stalls during the write (0.6–0.7 s at 5,100 events, growing with state). |
| G6 | Recovery outcome is not observable outside logs | `last_recovery` unused | The operator cannot confirm which recovery path ran without reading logs. The readiness gate cannot report it. |
| G7 | Checkpoint restore skips SHA-256 verification of the rows it covers | ADR-022 Known limitations | Corruption in skipped rows is found only on a later full replay. |
| G8 | Upgrade path: the first start on new code is always a full replay; PROD has no checkpoint | memory of `prod-runtime-topology`; ADR-022 Known limitations | The PROD cutover costs a full replay of a very large journal inside the maintenance window. Full replay is also currently **blocked** by `journal_identity_conflict` (ADR-028). |
| G9 | ADR-022 not marked accepted | ADR-022 header | A merged contract is formally unfrozen (AGENTS.md §3). |

Not a recovery gap, but noted: `ingest()` scans the whole `_events` list linearly for duplicate identity (`runtime.py:236`). That is O(history) per live event. It is out of scope here (§13).

## 4. Existing recovery flow (unchanged by this ADR)

```
lifespan start
  └─ configured_runtime(journal)
       └─ ResearchRuntime.__init__
            ├─ journal.append(stream:config)            (idempotent)
            ├─ PaperSession(journal)                    (rebuilds from its own stream)
            └─ _rebuild()
                 ├─ fresh pipeline / Experience / _events
                 ├─ _restore(): load → verify header/env/stream/fingerprint/blob hash
                 │              → row anchor (key, content_hash) at last_sequence
                 │              → count_through == event_count
                 │              → typed JSON decode onto fresh components
                 │              → events/state_hash cross-check
                 │              → adopt atomically, else full replay (reason logged)
                 ├─ replay rows WHERE sequence > last_sequence (bounded pages, hash-verified)
                 ├─ _recovered = True
                 └─ write checkpoint if full replay or replayed > 0
```

## 5. Proposed architecture (V1.1 hardening on ADR-022)

The ADR-022 architecture stays unchanged: disposable file checkpoint per stream, explicit typed JSON state, header anchors, strict `sequence > last_sequence` resume, and fallback to full replay on any doubt. The proposals below are **independent, individually reviewable increments**. None changes the journal schema, row payloads, or engine behavior.

| Id | Proposal | Addresses | Needs decision |
|---|---|---|---|
| H1 | **Scoped recovery compatibility key** replaces the whole-package fingerprint. Hash only the modules that determine checkpointed state or replay side effects (`research/`, `pnf/`, `adaptive_box/`, `matrix/`, `structure/`, `trendline/`, `market_regime/`, `signals/`, `experience/`, `paper/`, `entry_readiness/`, `artifacts.py`, `storage.py`, and any module those import), plus `sys.version_info[:2]` and the format/schema version. A contract test fails if a state-bearing package is not in the scope list. | G1 | **D1** |
| H2 | **Graceful stop before hard kill** in the launcher. Send a local-only shutdown signal (Windows `CTRL_BREAK_EVENT` to a process group, or an authenticated loopback-only shutdown route), wait a bounded time for `lifespan` `finally`, then fall back to today's terminate/kill. | G2 | **D2** |
| H3 | **Time/idle checkpoint trigger** in addition to the 100-ingest trigger. When `_since_checkpoint > 0` and no checkpoint has been written for `T` seconds, write one on the next ingest (no background thread in V1.1). | G3 | **D3** (value of `T`; measure first) |
| H4 | **`PostgresJournal` row anchors**: `iter_rows`, `row_identity`, `count_through`, `sequence_of`. The resume is safe under PG identity gaps and out-of-order commit, because `count_through(last_sequence) == event_count` already rejects a checkpoint when a lower sequence commits late. | G4 | **D4** (Postgres scope in PERF-1 or separate) |
| H5 | **Recovery observability**. Expose `last_recovery` and checkpoint write stats (`mode`, `reason`, `checkpoint_sequence`, `replayed`, `events`, `duration_ms`, `last_checkpoint_sequence`, `last_checkpoint_bytes`, `last_checkpoint_write_ms`, `last_checkpoint_age_s`, `since_checkpoint`) in `/operations/readiness` as informational fields that never gate readiness. Also emit one structured log line per recovery. | G6 | none (additive), but API contract owner review |
| H6 | **Optional deferred verification of skipped rows**. After startup, a read-only background pass re-verifies the SHA-256 of rows `≤ last_sequence` in bounded pages. A mismatch sets `error`/readiness reason `journal_verification_failed` and never mutates state. Default off. | G7 | **D5** |
| H7 | **Offline checkpoint pre-build for upgrades**. A CLI runs the *target* code against a SQLite backup-API copy of a stopped journal in an isolated scratch environment and writes a checkpoint whose header names the target environment. An operator installs it into the target `checkpoints/` directory while the writer is stopped. At start, the normal `verify()` checks anchors against the real journal, so rows written after the backup are replayed as the delta. The checkpoint gains no new trust; every existing check still applies. | G8 | **D6** (touches PROD procedure; Rin + human) |
| H8 | **Write outside the lock**, only if §11 measurements show ingest stalls that matter. Encode under the lock, and hash/fsync/rename outside it. | G5 | **D7** (measure first) |
| H9 | **Accept ADR-022** (status change only, with an accurate description of what is merged). | G9 | Rin |

Rejected alternatives:

- **A new checkpoint store in the journal database** (for example a `research_checkpoint` table). It duplicates ADR-022 and moves derived state next to authoritative state. Rin's 2026-09-23 decision placed checkpoints with the environment, and ADR-022 revision 2 implemented them as files under `root/checkpoints`. Changing that would be a contract change (§3 of AGENTS.md).
- **Keeping N previous checkpoint generations**. Every generation shares the same fingerprint, so a code change invalidates all of them together. Crash safety already comes from atomic `os.replace`. The only gain is bit-rot resilience, which does not justify the added state.
- **Relaxing the fingerprint to "never invalidate"**. This breaks the determinism requirement (§9).

## 6. Checkpoint data model

**Unchanged** from ADR-022 Decision 1/2: header fields `format, schema_version (2), environment, stream, code_fingerprint, last_sequence, last_event_key, last_content_hash, event_count, state_hash, blob_hash, blob_size, created_at`, followed by deterministic UTF-8 JSON state.

H1 changes only how `code_fingerprint` is *computed*. It is still a single opaque string, so no header change is needed. On first start after H1 ships, the new value will not match, which causes one full replay (expected behavior). If Rin prefers an explicit record, H1 may bump `SCHEMA_VERSION` to 3 and rename the field `compatibility_key`. That would also invalidate once, and is part of D1.

## 7. Checkpoint lifecycle

| Stage | Current (ADR-022) | V1.1 change |
|---|---|---|
| Create | after a completed rebuild with replayed > 0; every 100 ingests; graceful shutdown | + time/idle trigger (H3); graceful shutdown actually reached on Windows (H2); optional offline pre-build (H7) |
| Validate | full header/anchor/count/decode/state chain on every restore | unchanged; H6 adds optional post-start row verification |
| Invalidate | any header/anchor/decode/state failure; any package code change | code change **scoped to state-bearing modules** (H1) |
| Replace | atomic `tmp` + `fsync` + `os.replace` | unchanged |
| Delete | operator may delete at any time; never touches the journal | unchanged |
| Retention | one file per stream | unchanged |

## 8. Recovery algorithm

Unchanged from ADR-022 Decision 3 (see §4). V1.1 adds only:

1. record `last_recovery` for H5 at the end of `_rebuild()` (already populated);
2. optionally start H6 after `lifespan` becomes ready, never before.

## 9. Failure / fallback matrix

| Condition | Detection (existing) | Result | Test (existing unless marked NEW) |
|---|---|---|---|
| Missing checkpoint | `load()` → `None` | full replay, `no_checkpoint`, new checkpoint written | `test_no_checkpoint_full_replay_then_writes_one`, `test_empty_journal_starts_without_checkpoint` |
| Corrupt header | `header_corrupt` | full replay | `test_corrupted_checkpoint_falls_back_to_full_replay`, `test_header_identity_and_types_are_enforced` |
| Corrupt/truncated blob | `blob_hash_mismatch` | full replay | `test_corrupted_checkpoint_falls_back_to_full_replay` |
| Valid hash, undecodable / non-state JSON | `decode_failed` / `state_invalid` | full replay | `test_undecodable_blob_with_matching_hash_falls_back`, `test_non_state_json_falls_back`, `test_malformed_payload_fails_safely_without_partial_adoption` |
| Late component failure | any exception before adopt | full replay; nothing adopted | `test_late_component_failure_adopts_nothing` |
| Incompatible format/schema | `schema_version_mismatch` | full replay | `test_unsupported_checkpoint_version_falls_back` |
| Code change | `code_fingerprint_mismatch` | full replay | covered via header tests; **NEW** H1 scope tests |
| Other environment | `environment_mismatch` + anchors | full replay | `test_environment_isolation` |
| Other stream/config | `stream_mismatch` | full replay | `test_header_identity_and_types_are_enforced` |
| Checkpoint ahead of journal / foreign journal | `event_reference_invalid` / `event_count_mismatch` | full replay | `test_checkpoint_newer_than_journal_is_rejected`, `test_invalid_event_reference_or_state_falls_back` |
| Interrupted write | previous file kept; `*.tmp` ignored | restore previous | `test_crash_during_checkpoint_write_keeps_previous_checkpoint`, `test_leftover_partial_temporary_file_is_ignored` |
| Write failure | logged, ingest unaffected | continue; next trigger retries | `test_failed_checkpoint_save_never_fails_ingest` |
| Interrupted rebuild inside last row | `_recovered=False` | ingest completes recovery first; no checkpoint until complete | `test_interrupted_side_effect_is_repaired_from_journal_not_skipped` |
| Ingest failure | `_rebuild()` via checkpoint path | same verified restore | covered by ingest error path; **NEW** explicit test |
| Pickle/gadget payload | typed decode only | full replay, no execution | `test_pickle_payload_is_never_executed` |
| Unanchored journal (Postgres) | `journal_not_anchored` | full replay | `test_journal_without_row_anchors_uses_full_replay`; **NEW** with H4 |
| Checkpoints disabled | `checkpoints_disabled` | full replay (pre-ADR-022 behavior) | `test_checkpoints_disabled_preserve_full_replay` |
| Hard kill (G2) | no shutdown checkpoint | delta ≤ interval replayed | **NEW** launcher test with H2 |
| Journal row corrupt ≤ `last_sequence` | not detected at restore | **NEW** H6: readiness reason, no mutation | **NEW** |
| Replay fails (e.g. `journal_identity_conflict`) | exception out of `_rebuild` | API startup fails, same as without checkpoint | depends on ADR-028 |

Safety properties 1–14 from the brief map as follows:

1. consistency: Decision 4 of ADR-022, `_recovered`/`_consistent`.
2. atomicity: `tmp` + `fsync` + `os.replace`.
3. ordering: journal `sequence`.
4. boundary: strict `>` plus the anchor row.
5–7. corrupted, missing and incompatible checkpoints: the matrix above.
8. interrupted write: atomic replace.
9. fallback: every rejection path.
10. no event loss: the resume reads the journal, and the count check covers the prefix.
11. no duplicate application: strict `>`, the boundary test, and idempotent Experience/paper with identity-conflict checks.
12. DEV/PROD isolation: ADR-022 Decision 6.
13. upgrade: §12.
14. observability: logs today, H5 proposed.

## 10. Determinism requirements

- **Invariant (unchanged):** `STATE(full replay) == STATE(checkpoint + delta replay)` for the runtime snapshot, event list, signals, Experience memory (byte-exact encoded state) and paper snapshot. Journal rows must be byte-identical before and after recovery.
- **H1 adds a burden of proof.** Scoping the fingerprint is safe only if no module outside the scope can change checkpointed state or replay side effects. The mitigation is a static import-closure test: the scope must include the transitive import closure of `research.runtime` and `experience`, computed from the source tree, so it cannot be hand-maintained wrong. If Rin judges this insufficient, D1 resolves to "keep the whole-package fingerprint", and G1 is addressed by H7 alone.
- No wall-clock or randomness enters state; `created_at` stays informational.
- Pattern Engine (ADR-024) additions become new state components. The `COVERED_FIELDS` contract test forces them into `checkpoint_state.py`. That is shared-file coordination with DEV-PNF (§14), not a PERF-1 redesign.

## 11. Observability

Existing log lines (ADR-022 Decision 7) stay as they are. H5 adds:

- one structured summary line per recovery: `Research recovery: summary mode=… reason=… checkpoint_sequence=… replayed=… events=… duration_ms=… restore_ms=… delta_ms=…`;
- the `/operations/readiness` field `research.recovery` (informational, never gating);
- the checkpoint write fields listed in H5.

State objects, journal payloads, DSNs and paths outside the environment root are never logged.

## 12. Test strategy

The existing suite already covers the minimum list in the brief:

| Brief requirement | Existing test |
|---|---|
| no checkpoint | `test_no_checkpoint_full_replay_then_writes_one` |
| valid checkpoint | `test_valid_checkpoint_without_delta_replays_nothing` |
| checkpoint + new events | `test_checkpoint_plus_delta_replays_only_later_events_and_never_the_boundary`, `test_periodic_checkpoint_bounds_the_restart_delta` |
| corrupt checkpoint | `test_corrupted_checkpoint_falls_back_to_full_replay` (+3 decode variants) |
| incompatible checkpoint | `test_unsupported_checkpoint_version_falls_back` |
| interrupted write | `test_crash_during_checkpoint_write_keeps_previous_checkpoint`, `test_leftover_partial_temporary_file_is_ignored` |
| event boundary | `…never_the_boundary`, `test_bounded_recovery_order_and_fixed_boundary` |
| FULL == CHECKPOINT + DELTA | `test_checkpoint_plus_delta_state_equals_full_replay[paper=True/False]`, `test_exact_state_round_trip_and_parity` |

New tests per increment (planned, not written):

- **H1:** the fingerprint is unchanged when a non-state module (for example `apps/`) changes, and changes when any module in the import closure changes; the scope contains the import closure of `research.runtime` + `experience`; parity with full replay after an out-of-scope edit.
- **H2:** in a launcher test on a synthetic environment, a graceful stop runs the `lifespan` shutdown checkpoint and the next start replays 0 rows; the fallback kill still works when the process ignores the signal.
- **H3:** with a fake clock, a checkpoint is written after `T` with fewer than 100 ingests, and no write happens when `_since_checkpoint == 0`.
- **H4:** the whole checkpoint suite parametrized over `PostgresJournal`, using the CI ephemeral loopback PG. Cases: an identity gap, and a late-committed lower sequence rejected by `event_count_mismatch`.
- **H5:** the readiness payload contains the recovery fields, and readiness status is unaffected by them.
- **H6:** tampering with a skipped row produces `journal_verification_failed` without mutating state, and the default is off.
- **H7:** a checkpoint pre-built on a backup copy restores against the original plus later appended rows, with parity to full replay. A pre-build carrying the wrong environment, or made from a divergent journal, is rejected.
- **Regression:** the full `pytest`, `ruff`, `mypy` gates from `docs/development.md` on every increment.

All tests use `tmp_path` journals and environments. None reads `NEXORA-TSID`, `NEXORA\data`, or any runtime root.

## 13. Benchmark strategy

No performance target is proposed until a fresh baseline exists.

- **Data:** a deterministic synthetic journal generator (fixed seed, the realistic `RuntimeConfig` shape from `docs/examples/research-config.json`) writes N ∈ {500, 1,000, 2,000, 5,000} events into a scratch environment. **Never** copy, open or benchmark the PROD journal (`NEXORA-TSID/.runtime/production`) or the legacy `NEXORA\data\research.sqlite`.
- **Scenarios per N:**
  - (a) full replay with checkpoints off;
  - (b) first start with no checkpoint;
  - (c) warm restart from a checkpoint at N;
  - (d) checkpoint at N−k plus delta, with k ∈ {1, 10, 100};
  - (e) fingerprint mismatch;
  - (f) checkpoint write time and size at N;
  - (g) ingest latency during a checkpoint write (for H8).
- **Metrics:** recovery `duration_ms` split into restore/verify, journal read/verify, engine replay, Experience observe and paper; app startup (`lifespan` entry to ready); checkpoint bytes; peak RSS.
- **Method:** `time.monotonic()`; one warm-up run per journal, then median of ≥3 runs; same machine and idle system; record the commit SHA, Python version and hardware. Compare only rows measured on the same journal (ADR-022 observed 241–466 s variance).
- **Before/after:** each H-increment reports scenario deltas against the baseline measured on its base SHA.

## 14. Migration / compatibility

- There is no journal schema change and no row rewrite. Checkpoint files remain disposable.
- H1 causes exactly one full replay per stream on the first start after it ships, the same as any code change today.
- H4 is additive to `PostgresJournal` and needs no DDL, because `sequence`, `event_key` and `content_hash` already exist.
- H7 is an operator procedure that follows `docs/environment-migration-checklist.md`: stop the writer, back up through the SQLite backup API, pre-build in isolation, install, then start. It is never run automatically. It needs Rin's GO and a maintenance window.
- **PROD cutover dependency:** new code cannot recover the TSID journal until ADR-028 (`journal_identity_conflict` on pre-Trendline snapshots) is merged, with checkpoints on or off. Checkpoints cannot help, because the first full replay fails.
- Rollback: `NEXORA_RESEARCH_CHECKPOINTS=off`, or revert. Each H-increment can be reverted on its own.

## 15. Security / integrity

- There is still no executable deserialization; H1–H8 do not touch the decoder.
- Integrity protects against corruption, not against someone who can already write the environment root. That is the same trust boundary as the journal (ADR-022 Risks). H7 does not widen it: the pre-built file passes the same anchors, and its installation is a manual step by the operator.
- H2's shutdown channel must be local-only: a process-group console signal, or a loopback route that requires the existing local-origin check. It must never be reachable via the Tailscale/remote gateway. **Security review required.**
- H5 must not expose paths, DSNs or state contents.
- Security review applies to H2, H4, H6 and H7 (persistence and operations).

## 16. Out of scope

- Journal payload size and compaction (ADR-027).
- Experience replay cost (a future Experience V2 ADR).
- Journal retention or deletion of any historical row. The journal remains the audit and recovery source.
- The linear `_events` identity scan in `ingest()`, and whether the runtime needs the full `_events` list after startup (Rin decision 5 of 2026-09-23: do not change it without approval).
- Changing engine semantics, trading logic, pattern contracts or the paper boundary.
- Any PROD benchmark, copy or migration execution.

## 17. Dependencies / risks

| Item | Type | Note |
|---|---|---|
| ADR-028 / EXC1 (`NEXORA-EXPERIENCE-COMPAT`) | blocking for PROD cutover and H7 | Full replay of TSID fails without it |
| ADR-027 Payload V2 | complementary | Reduces per-row cost. Changing `iter_rows` payload reconstruction shares `storage.py`/`runtime.py` |
| ADR-023/024 Pattern Engine | shared file | A new engine state goes through `checkpoint_state.py` + `COVERED_FIELDS`. DEV-PNF owns semantics, DEV-PERF the restore contract |
| Startup Recovery V1 ownership | **governance conflict** | Rin (2026-09-23) assigned `claude/startup-recovery-v1` and "no competing implementation" to another session (`a429c23c…`). PERF-1 extends the same code. Rin must confirm PERF-1 ownership before any implementation |
| Launcher (`nexora_api/launch.py`) | shared ops file | H2 changes process stop semantics used by PROD tooling |
| H1 scope error | correctness risk | Mitigated by the computed import-closure test; the fallback option is D1 = keep the whole-package fingerprint |
| Windows `CTRL_BREAK` semantics under uvicorn | technical risk | Prototype in an isolated DEV environment only |

## 18. Open decisions

| Id | Decision | Owner | Recommendation |
|---|---|---|---|
| D1 | Scoped compatibility key (H1) vs. keep the whole-package fingerprint | Rin (Architect) | Scoped, with a computed import-closure test; the fallback is to keep it whole |
| D2 | Graceful-stop mechanism on Windows (console signal vs. loopback route) | Rin + Security | Console signal to its own process group; no new HTTP surface |
| D3 | Time/idle checkpoint trigger `T` | Rin, after §13 measurements | No value until measured |
| D4 | Postgres anchors in PERF-1 or a separate task | Rin | Include; small and additive |
| D5 | Deferred skipped-row verification (H6) default | Rin + Security | Implement, default off |
| D6 | Offline pre-build procedure for PROD cutover (H7) | Rin + human (PROD) | Design and test on synthetic data only; any PROD use needs a separate GO |
| D7 | Checkpoint write outside the lock (H8) | Rin, after measurement (g) | Defer unless measured stalls matter |
| D8 | ADR-022 acceptance status (H9) | Rin | Accept, reflecting revision 2 as merged |
| D9 | PERF-1 ownership vs. the Startup Recovery V1 owner session | Rin | Required before Phase 2 |

No product or quant decision is required. No proposal changes trading semantics, signals, patterns or risk.
