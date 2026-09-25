# ADR-031 — Experience Replay Performance Investigation V1 (PERF-2)

Status: **Proposed — Phase 1 approved (Rin); Phase 2A (C1, C2, C3(a, b)) implemented on this branch, pending Rin code review and a Rin decision on `COVERED_FIELDS` (§18.7).** Self-review only.
Date: 2026-09-25
Workstream: PERF-2 (DEV-PERF role) · Branch `claude/experience-replay-performance-v1` · Worktree `D:\NEXORA\NEXORA-EXPERIENCE-PERF` · Base `origin/main` `4f69e9c`

Related (on `main`): [EX1](../../tasks/EX1-experience-engine-v1.md) (Experience V1 contract), [architecture — Experience memory](../architecture.md), [ADR-018](./ADR-018-readiness-corrections.md), [ADR-022](./ADR-022-startup-recovery-checkpoint-v1.md) (checkpoint-assisted recovery).
In flight, not on `main` (read-only inputs to this ADR): ADR-027 Research Journal Payload V2 (`claude/research-journal-payload-v2`, Decision 5 "Experience V2"), ADR-028 Experience snapshot additive fields (uncommitted in `NEXORA-EXPERIENCE-COMPAT`), ADR-029 Recovery Checkpoint V1 Hardening / PERF-1 (`claude/recovery-checkpoint-v1`), ADR-030 Replay Validation Framework V1 / VALID-1 (`claude/replay-validation-v1`).

This ADR changes **no production code, contract, formula or stream**. Phase 1 recorded evidence and proposed candidates. Phase 1B added benchmark tooling only: `scripts/experience_replay_benchmark.py` and its tests (section 16). Every candidate needs its own acceptance before implementation (Phase 2).

## 1. Problem

Startup recovery replays research journal rows through `ResearchRuntime._rebuild()`. PERF-1 (ADR-029) reduces **how many** rows are replayed. It does not change **what each replayed row costs**. ADR-022, ADR-027 and ADR-029 reported that `ExperienceService.observe` accounts for about 93–96% of replay time.

PERF-2 checks that claim independently. It explains where the per-event cost goes and how that cost grows with history. It then proposes optimizations that keep observable Experience behavior byte-identical.

## 2. Repository evidence (verified at `4f69e9c`)

| Area | File | Relevant fact |
|---|---|---|
| Recovery loop | `packages/nexora/research/runtime.py:59-114` | Per row: `decode(NormalizedPriceEvent, row["event"])`, `engine.replay(event)`, `_events.append`, `_paper_event`, `experience.observe(event, row["output"], …)`. The **recorded** output is passed to Experience, not a recomputation (EX1). |
| Live path | `runtime.py:217-273` | `ingest` → `engine.process` (returns `canonical_serialize(_output)`) → journal append → `_paper_event` → `experience.observe`. Same `observe` code path as replay. |
| Recorded output | `research/pipeline.py:116-133` | `output` holds the **cumulative** `columns` and `transitions` of the structure resolution, plus full `structure`, `trendline`, `signals`, `matrix`, `regime` and `entry_readiness`. Its size grows with history. |
| Observer | `experience/service.py:29-94` | The per-event algorithm (section 4). |
| Pure functions | `experience/engine.py` | `freeze`, `fingerprint`, `plan`, `eligible`, `measure`, `advance`, `initial_lifecycle`. |
| Model | `experience/models.py` | `Experience` is frozen. `context()` = `json.loads(context_json)` on **every call**. `HORIZONS = (5, 15, 30, 60)`. |
| Persistence | `experience/repository.py`, `storage.py:42-69, 172-200` | Every Experience write is `Journal.append`: canonical serialize + `json.dumps` + `canonical_hash`, then one write transaction (`BEGIN IMMEDIATE` / advisory lock, `SELECT`, `COUNT(*)`, `INSERT … IGNORE / ON CONFLICT DO NOTHING`, `SELECT`, `COMMIT`). |
| Canonical encoding | `artifacts.py:14-37` | `canonical_serialize` is a recursive pure-Python walk (`is_dataclass`, `isinstance`, `sorted` per dict) that rebuilds every container. `canonical_hash` = serialize + `json.dumps(sort_keys)` + SHA-256. |
| Checkpoint | `research/checkpoint_state.py:537-613` | Experience memory (`last`, `pending`, `states`, `samples`, `completed`, `seen`) is encoded explicitly. Shared sample rows are deduplicated by **object identity** (`id(row)`). |

## 3. Current Experience replay flow

```text
research_journal (stream research:<cfg>) ── iter_rows(after=last_sequence), 32-row pages
  │  storage._verify: json.loads + json.dumps(sort_keys) + sha256 of the WHOLE row  (O(|output|))
  ▼
ResearchRuntime._rebuild (per row, single thread, no runtime lock held)
  ├─ decode(NormalizedPriceEvent, row["event"])        (get_type_hints per call)
  ├─ ResearchPipeline.replay(event)                    (engines; output not serialized)
  ├─ _events.append(event)
  ├─ _paper_event(...)                                 (no-op unless paper configured)
  └─ ExperienceService.observe(event, row["output"], completeness, metadata)
        ├─ freeze(config, stream, event, output, …)            ← every event
        │    ├─ canonical_serialize(config)                    (RuntimeConfig dataclass → dict)
        │    ├─ canonical_serialize(output)                    O(|output|)
        │    ├─ scope_for → canonical_hash((config, …))        (re-serializes config)
        │    ├─ fingerprint(scope, output)                     O(|pivots|+|levels|) hash
        │    ├─ build context {event, market, decision, matrix, structure, trendline,
        │    │                  entry_readiness, regime, pnf{columns,transitions}, runtime_config, provenance…}
        │    │    ├─ canonical_hash(config), canonical_hash(event)
        │    │    └─ canonical_hash(output)                    O(|output|)  (provenance.output_hash)
        │    ├─ frozen_json(context)                           O(|output|)  (serialize + dumps)
        │    └─ experience_id = canonical_hash((POLICY, scope, key, digest))
        ├─ digest = canonical_hash((event, output, completeness, metadata))   O(|output|)
        ├─ if identity in _seen: conflict check / return        (never true on a fresh replay)
        ├─ journal.append(experience:v1:<scope>:observations, key, raw)   ← every event; 1 write txn
        ├─ if fingerprint != _last[scope].fingerprint:          (new T0 snapshot)
        │    ├─ repository.save → journal.append(experience:v1:snapshots)   O(|context_json|)
        │    └─ _persist_state("initial") → journal.append(lifecycle)
        └─ for each pending experience in the same scope and eligible for this event:
             ├─ samples[eid].append(raw)                        (shared object)
             ├─ advance(experience, state, event)
             │    └─ plan(experience) → experience.context() → json.loads(context_json)   O(|context_{t0}|)
             │         → _persist_state per transition → journal.append(lifecycle)
             └─ for each due horizon: measure(experience, h, samples, event)
                  ├─ window filter; for each window row: advance(...) → plan → json.loads(context_json)
                  ├─ plan(experience) + experience.context() again
                  └─ journal.append(outcomes)
             └─ all 4 horizons done → optional closing lifecycle append; drop pending/samples
```

During a restart replay, every Experience row already exists in the journal. Each `append` therefore goes through the full write transaction and returns `False` (INSERT ignored). The `content_hash` identity check still runs, so replay also **re-verifies** every historical Experience row.

## 4. Call graph and per-event work (static)

| Step | Frequency | Data touched | Size behavior |
|---|---|---|---|
| `canonical_serialize(output)` in `freeze` | 1 per event | whole output | O(i), i = events so far |
| `canonical_hash(output)` (output_hash) | 1 per event | whole output | O(i) |
| `frozen_json(context)` | 1 per event (built even if the fingerprint did not change) | output subset incl. `columns`, `transitions`, `structure`, `trendline` | O(i) |
| `digest = canonical_hash((event, output, …))` | 1 per event | whole output | O(i) |
| `fingerprint` hash | 1 per event | `structure.pivots`, `levels`, matrix, decision | O(pivots+levels), grows with i |
| `canonical_serialize(config)` + 2× config hash | 1 per event | `RuntimeConfig` | O(1) |
| observations `append` | 1 per event | raw event | O(1) data + 1 write transaction + `COUNT(*)` over the observations stream (O(i)) |
| `Experience.context()` → `json.loads(context_json)` | ≈ `pending_eligible` per event + ~110 per completed experience (5+15+30+60 window rows in `measure`) + 4–8 per horizon | T0 context | O(i_t0) per call |
| snapshot `append` | per fingerprint change | `context_json` | O(i) |
| lifecycle / outcomes `append` | per transition / per horizon | bounded | O(1) data + 1 write transaction |
| `measure` window filter + lifecycle rebuild | 4 per experience | ≤ 60 minutes of samples | bounded by sample rate |

State held in memory: `_last` (per scope), `_pending` (open experiences, bounded by the 60-minute horizon), `_samples` (bounded by the same window), `_completed`, `_states` (**never pruned**, O(#experiences)), `_seen` (**never pruned**, O(#events)). There is no lock inside `ExperienceService`. It relies on the runtime's single writer (`_lock` in `ingest`; `_rebuild` runs from `__init__` or under that lock). The journal takes its own `RLock` plus a database write lock per append. The `_seen` short-circuit never fires on a fresh replay, because memory starts empty.

No P&F or engine code runs inside Experience. It depends on engines only through the shape of the **recorded** output.

## 5. Baseline / profiling methodology

**Isolation.** Every run builds a synthetic SQLite journal in a **new, empty temporary directory** outside the repository. PROD, the TSID runtime, the 51.8 GB legacy database and PostgreSQL were not opened. The harness asserts that replay leaves every journal stream's row count and byte size unchanged (`journal_unchanged_rows: true` in every instrumented run).

**Data.** The generator is the same seeded, mean-reverting one-minute close series as PERF-1's `scripts/recovery_benchmark.py` (seed `20260925`, example config `docs/examples/research-config.json`). It has two profiles:

| Profile | `STEP_TENTHS` | Why | Snapshot rate | Research row size |
|---|---|---|---|---|
| **calm** | 3 | PERF-1 calibration: output growth ≈ 45–55 B/event, same order as the legacy journal (ADR-022) | ~6% of events create a new T0 snapshot | 4.3 KB (row 50) → 117 KB (row 2,000) |
| **volatile** | 20 | Stress profile. Frequent reversals, close to ADR-027's measurement profile (88 KB/row at 300 events) | ~64% of events create a new T0 snapshot | 15 KB → 181 KB by row 250 |

**Procedure.** For each (profile, N):

1. Build the journal by live `ResearchRuntime.ingest` of N events, with `checkpoints=None`. This also records live `observe` latency.
2. Run 1–3 **plain** full replays: a fresh `ResearchRuntime(config, journal)` with checkpoints disabled, timed by wall clock.
3. Run one **instrumented** full replay. In-process wrappers attribute **exclusive** time (a parent's time excludes wrapped children) to: `freeze` and its `canonical_serialize` / `canonical_hash` / `frozen_json` / `fingerprint`; the `observe` digest; `Experience.context`; `plan`; `advance` (observe vs. measure); `measure`; `append` per Experience stream kind; each SQL statement class and `COMMIT` per stream kind (through a connection proxy); `storage._verify`; `runtime.decode`; and `ResearchPipeline.replay`. Per-call `observe` latency is logged together with the pending count and whether a snapshot was created.
4. Run `cProfile` on one plain replay per profile (calm N=2,000; volatile N=500).

Scales: calm N = 500, 1,000, 2,000, 5,000; volatile N = 250, 500, 1,000. The volatile profile is quadratic enough that N ≥ 2,000 was not practical in Phase 1.

**Environment.** Windows 11, Intel64 Family 6 Model 142 (4 cores), Python 3.13.3, SQLite WAL with `synchronous=FULL`. The workstation was otherwise idle and runs were sequential.

**Instrumentation overhead.** Instrumented totals were within the run-to-run spread of plain replays (for example calm N=2,000: plain 49.5–51.9 s, instrumented 53.2 s). Percentages are taken within one instrumented run.

**Not measured.** PostgreSQL: `not_run`, because no isolated PostgreSQL instance was used in Phase 1. The same statement sequence runs there as network round trips; see section 7.4. Lock wait: replay is single-threaded and the journal `RLock` is uncontended. SQLite `BEGIN IMMEDIATE` and `COMMIT` time is reported as its own category. Allocation: not traced with `tracemalloc`. The cProfile call counts in section 6.3 stand in for container-rebuild volume.

Phase 1 used a scratch harness (`perf2_profile.py`, `run_matrix.sh`, `summarize.py`) that was not committed. Phase 1B moved it into the repository as `scripts/experience_replay_benchmark.py`, which resolves Q-P2-4. The same generator, profiles and attribution method are described in section 16. The numbers in sections 6–7 come from the Phase 1 scratch harness; section 16.5 re-measures them with the committed tool.

## 6. Performance findings

### 6.1 Replay totals

| Profile | N | Plain replay (median, s) | Mean ms / replayed event | `observe` share (instrumented) | Build by live ingest (s) |
|---|---|---|---|---|---|
| calm | 500 | 5.71 (3 runs) | 11.4 | 79.7% | 9.0 |
| calm | 1,000 | 17.46 (3 runs) | 17.5 | 84.5% | 32.9 |
| calm | 2,000 | 50.45 (3 runs) | 25.2 | 86.3% | 85.6 |
| calm | 5,000 | 262.06 (1 run) | 52.4 | 87.6% | 492.7 |
| volatile | 250 | 27.47 | 109.9 | 95.5% | 33.7 |
| volatile | 500 | 131.67 | 263.3 | 96.3% | 131.9 |
| volatile | 1,000 | 432.94 | 432.9 | 96.7% | 510.2 |

**The 93–96% claim is profile-dependent.** It reproduces on volatile data (95.5–96.7%). On the legacy-calibrated calm profile, `observe` is 80–88% and rises with N (79.7% → 84.5% → 86.3% → 87.6%). The claim is directionally right — Experience dominates replay — but the magnitude, and especially **which** sub-cost dominates, depends on the workload (section 6.2).

PERF-1 reported 423 s for a 5,000-event full replay with the same generator. This ADR measured 262 s on this workstation. Absolute times are machine- and load-dependent (ADR-022 recorded 241–466 s variance), so only same-machine comparisons are meaningful.

### 6.2 Experience cost breakdown (exclusive time, share of total replay)

| Category | calm 500 | calm 1,000 | calm 2,000 | calm 5,000 | vol 250 | vol 500 | vol 1,000 |
|---|---|---|---|---|---|---|---|
| A. `freeze()` serialize/hash of recorded output (`canonical_serialize`, 6× `canonical_hash`, `frozen_json`, `fingerprint`) | 34.2% | 32.5% | 34.5% | **38.3%** | 12.3% | 11.9% | 10.7% |
| B. `Experience.context()` re-parse + `plan()` | 14.8% | 23.1% | 27.7% | 28.4% | 68.2% | **74.4%** | **74.3%** |
| C. Idempotency digest `canonical_hash((event, output, …))` | 9.4% | 11.3% | 12.7% | 13.9% | 4.8% | 4.8% | 4.4% |
| D. SQLite `COMMIT` of no-op Experience write transactions | 13.7% | 11.1% | 6.8% | 4.1% | 5.9% | 2.6% | 5.5% |
| E. `append` payload serialize/hash | 4.7% | 3.5% | 2.3% | 1.2% | 2.2% | 1.4% | 1.1% |
| F. Other Experience SQL (`BEGIN`, `SELECT`×2, `COUNT(*)`, `INSERT`) | 1.7% | 1.6% | 1.5% | 1.3% | 0.5% | 0.3% | 0.2% |
| G. Lifecycle `advance` + `measure` arithmetic (excluding `plan`/`context`) | 0.6% | 0.7% | 0.5% | 0.3% | 1.0% | 0.7% | 0.5% |
| H. `observe` residual (loop, dict ops) | 0.7% | 0.5% | 0.4% | 0.2% | 0.5% | 0.3% | 0.2% |
| *Non-Experience:* research row read + `_verify` | 5.7% | 6.2% | 7.4% | 8.6% | 2.7% | 2.7% | 2.6% |
| *Non-Experience:* `runtime.decode` of event | 9.6% | 5.7% | 3.6% | 1.8% | 0.8% | 0.4% | 0.2% |
| *Non-Experience:* `ResearchPipeline.replay` (all engines) | 4.0% | 2.6% | 1.7% | 0.8% | 0.5% | 0.2% | 0.1% |

Per-call evidence (exclusive):

| Item | calm 500 | calm 2,000 | calm 5,000 | vol 250 | vol 1,000 |
|---|---|---|---|---|---|
| `Experience.context()` calls / events | 3,204 / 500 (6.4 per event) | 20,826 / 2,000 (10.4) | 48,950 / 5,000 (9.8) | 24,024 / 250 (96) | 110,813 / 1,000 (**111**) |
| `Experience.context()` mean cost | 0.24 ms | 0.65 ms | 1.39 ms | 0.74 ms | 2.68 ms |
| Mean pending experiences after `observe` | 2.2 (max 5) | 3.5 (max 14) | 3.3 (max 14) | 33.8 (max 45) | 37.8 (max 47) |
| `observe` digest per call | 1.08 ms | 3.38 ms | 7.17 ms | 5.44 ms | 18.99 ms |
| No-op `COMMIT` per Experience append | 1.27 ms | 1.29 ms | 1.53 ms | 1.19–1.32 ms | 3.7–4.6 ms |
| `COUNT(*)` on observations stream per append | 0.07 ms | 0.24 ms | 0.54 ms | 0.04 ms | 0.11 ms |
| `runtime.decode` per event | 1.10 ms | 0.96 ms | 0.93 ms | 0.92 ms | 0.89 ms |
| New-snapshot vs. no-snapshot `observe` mean | 12.4 / 9.1 ms | 27.1 / 22.7 ms | 49.9 / 45.0 ms | 109.6 / 103.8 ms | 426.8 / 406.0 ms |

The no-op `COMMIT` cost rose from ~1.3 ms to 3.7–4.6 ms in the volatile N=1,000 run (531 MB database). The cause (WAL/database size or OS cache pressure) was not isolated in Phase 1.

Interpretation:

- **A + C (serialize/hash of the recorded output)** are the largest sum on calm data (44–52%, rising with N). They are paid on **every** event, whether or not a snapshot is created. The no-snapshot `observe` is only ~20% cheaper than the new-snapshot one. cProfile (calm N=2,000) attributes the cost to the pure-Python recursive `canonical_serialize`: **18.9 M recursive calls** from ~35.8 k top-level calls (about 18 per event, about 9.5 k nodes walked per event), plus 88.9 M `isinstance` and 18.9 M `is_dataclass` calls. The C-level `json.dumps` (~8 s under the profiler) and SHA-256 (1.4 s) are small next to it. Every one of these walks re-canonicalizes data that **is already canonical**: the recorded output is decoded JSON on replay, and `canonical_serialize(_output)` output on the live path.
- **B (`context_json` re-parse)** dominates volatile data (68–74%) and grows on calm data (15% → 28%). cProfile (volatile N=500): `json` `raw_decode` takes 99 s of self time over 52,974 calls, the single largest item. `plan()` is a pure function of the immutable `Experience`, yet it re-parses the full T0 context — including cumulative `pnf.columns/transitions` — once per pending experience per event and once per window row inside `measure`.
- **D (no-op write transactions)** is **~1.2–1.5 ms per Experience append** (3.7–4.6 ms on the 531 MB volatile database) on SQLite with `synchronous=FULL`. It applies even though replay inserts nothing. Its share falls as the O(i) terms grow, but it scales with the number of appends: 1 per event, plus lifecycle and outcome rows. The 4 horizons alone add 4 appends per experience.
- **Engines are ≤ 4% everywhere** (0.1–0.8% at the largest N), which confirms ADR-022, ADR-027 and ADR-030 Decision 13.

### 6.3 Cost growth within one replay (history size)

Mean `observe` latency per decile of replay position:

| Profile, N | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | D10 |
|---|---|---|---|---|---|---|---|---|---|---|
| calm 2,000 (ms) | 6.8 | 10.1 | 9.4 | 21.7 | 21.6 | 18.6 | 23.7 | 34.3 | 38.4 | 44.7 |
| calm 5,000 (ms) | 8.2 | 19.6 | 25.5 | 39.0 | 42.3 | 45.3 | 59.8 | 58.5 | 73.5 | 80.8 |
| vol 250 (ms) | 10.9 | 27.9 | 65.0 | 82.6 | 101.8 | 103.7 | 131.9 | 155.8 | 195.1 | 200.2 |
| vol 1,000 (ms) | 58.5 | 137.3 | 222.7 | 303.9 | 370.5 | 460.1 | 541.4 | 662.2 | 673.8 | 764.8 |

Live ingest shows the same growth. Live `observe` goes from 9.1 ms (first decile) to 82.2 ms (last decile) in calm N=5,000, and from 44.1 to 733.5 ms in volatile N=1,000. **This is a live-path latency problem as well as a recovery problem.** On live ingest, `observe` is 53–80% of `ingest` time.

## 7. Complexity analysis

### 7.1 Per-event cost model (from code plus measurements)

For replayed event *i*, with *S_i* = size of the recorded output at *i* (cumulative `columns`, `transitions`, `pivots`, `levels`, `trendline` history), *P_i* = eligible pending experiences in scope, and *C_t0* = size of each pending experience's `context_json`:

```text
cost_observe(i) ≈ k1·S_i                       (A + C: ~4 full canonical walks + 3 dumps + 2 sha256 of output)
               + k2·Σ_{p∈P_i} C_{t0(p)}        (B: one json.loads per pending experience)
               + k3·Σ_{completing p} W_p·C_{t0(p)}   (B in measure: W ≤ ~60-min window rows)
               + k4·A_i                        (D/F: A_i appends × fixed write-txn cost)
               + k5·i                          (COUNT(*) over the observations stream; small constant)
```

- *S_i* grows **linearly** with history (measured calm row size 4.3 KB → 117 KB over 2,000 events). It grows faster on volatile data because reversals add columns and pivots.
- *C_t0* ≈ *S_t0* (the context embeds `pnf` and full `structure`/`trendline`), so it is also O(i).
- *P_i* is bounded by the snapshot rate × 60 minutes of event time: ~2–4 on calm data, ~34 on volatile data. It does **not** grow with N, but it multiplies the O(i) term.

**Conclusion: `observe` is O(i) per event (linear in accumulated history), and a full replay is O(N²).** Evidence: the per-decile latency grows roughly linearly within a replay (section 6.3), and the per-event mean grows with N (calm 11.4 → 17.5 → 25.2 → 52.4 ms for N = 500 → 1,000 → 2,000 → 5,000). Fitted exponent of total replay time: calm 500→1,000 ≈ N^1.61, 1,000→2,000 ≈ N^1.53, 2,000→5,000 ≈ N^1.80; volatile 250→500 ≈ N^2.26, 500→1,000 ≈ N^1.72. Exponents below 2 at small N reflect the fixed per-event terms (decode, commits, engines), whose share shrinks as N grows. It is not O(1) or O(log N). There is no single O(N) scan per event over an ever-growing Python collection; the growth comes from **re-walking the cumulative collections embedded in each recorded output**.

### 7.2 Repeated scans over growing collections

| Location | Collection | Per event | Growth |
|---|---|---|---|
| `freeze`/`observe` canonical walks | recorded `output` | ~4 walks | O(i) |
| `plan` → `context()` | T0 `context_json` | P_i parses (+ W per horizon) | O(i_t0) |
| `storage.append` `COUNT(*)` | observations stream rows | 1 (index range count) | O(i), 0.07 → 0.24 ms (500 → 2,000) |
| `_seen`, `_states` | dicts | O(1) lookup | memory O(N) and O(#experiences); checkpoint size grows (PERF-1 domain) |
| `measure` window | `_samples[eid]` | per horizon | bounded (≤ 60 minutes) |

### 7.3 Adjacent (non-Experience) replay costs

- `storage._verify`: `json.loads` + `dumps` + SHA-256 of each O(i) research row (6–7% calm). This is owned by ADR-027 Payload V2 and storage, not PERF-2.
- `runtime.decode`: ~1 ms per event, fixed. `get_type_hints()` is evaluated on every `_decode` call; cProfile shows ~50 k `compile()` calls from resolving string annotations. It is 1.8–9.6% on calm data (a fixed ~0.9–1.1 ms per event). This lives in `artifacts.py`, a shared file, not in Experience.

### 7.4 PostgreSQL (inferred, not measured)

`PostgresJournal.append` issues 6 statements plus the transaction, including `pg_advisory_xact_lock` and `COUNT(*)`, for **each** Experience append. That is at least 7 synchronous network round trips per append, and at least one append per replayed event. D + F are therefore expected to weigh **more** on PostgreSQL than on local SQLite. This must be measured on an isolated PostgreSQL instance before it is used for prioritization (Q-P2-3).

## 8. Correctness invariants (the boundary any optimization must keep)

A future optimization is acceptable only if **all** of the following hold, compared with the `4f69e9c` implementation on the same inputs:

| # | Invariant | Observable |
|---|---|---|
| X1 | **Journal byte-identity.** Every `experience:v1:*` row has an identical `(stream, event_key, content_hash, payload)`, and the set of rows written for a given input sequence is identical. | journal rows |
| X2 | **Write order.** Relative order of Experience appends is unchanged. In particular the observation append **precedes** every snapshot, lifecycle or outcome write that uses it (`service.py:53`), and lifecycle suffix order (`initial`, `0`, `1`, `close`) is preserved. | journal `sequence` order on live ingest and on crash-fill replay |
| X3 | **Identity and content.** `scope`, `fingerprint`, `experience_id`, `t0`, `action` and `context_json` are byte-identical. This includes `provenance.output_hash` / `event_hash` / `config_hash` and ADR-028's presence-not-value rule for additive fields. | snapshots |
| X4 | **Outcomes.** MFE, MAE, `mfe_r`, `mae_r`, `risk_distance`, excursions, `sample_count`, `sample_event_ids`, `coverage`, `plan_hits`, `entry_observed_by_horizon`, endpoint fields and `measurement_reference` are identical. Causal windows are unchanged (`event_time <= due` **and** `received_at <= due`), and the late endpoint never contributes excursions or hits. | outcomes |
| X5 | **Lifecycle.** Same states, transitions, hit facts, `entered_at` and `latest_fact` guard. Entry is never counted at T0, and there is no transition after 60 minutes. | lifecycle |
| X6 | **In-memory state equivalence.** `checkpoint_state()` after replaying any prefix encodes to identical bytes through `checkpoint_state.encode_state`. This includes `seen` digests (same digest definition) and **sample-row object sharing**: the same `raw` object is shared across pending episodes, because encoding dedups by `id(row)`. | checkpoint state (ADR-022 invariant `STATE(full) == STATE(checkpoint + delta)`) |
| X7 | **Idempotency and conflict semantics.** A repeated identity with the same digest is a no-op. A different digest raises `experience_event_identity_conflict`. Any differing committed row still raises `journal_identity_conflict`. Replay still **fills missing** Experience rows after a crash between the research commit and the Experience writes (EX1 recovery rule). | exceptions, filled rows |
| X8 | **Replay/live parity.** `observe` receives the **recorded** output on replay and the same canonical output live, and produces the same writes on either path. Decisions are never regenerated. | parity test |
| X9 | **No feedback.** Experience state never influences engine, signal, paper or risk inputs. | existing `test_runtime_observer_has_no_feedback_to_any_decision` |
| X10 | **Failure points.** An exception at any append leaves the same committed prefix as today, so `ingest`'s `_rebuild()` retry converges to the same state. | fault-injection test |
| X11 | **API reads.** `ExperienceRepository` results (`/experiences*`) are unchanged. | API tests |

**Decision-time behavior.** Experience is a post-decision observer and has no decision-time output. PERF-2 must not move `observe` before the research commit, and must not make it asynchronous relative to the research row, without a separate architecture decision (see C8).

## 9. Optimization candidates (not implemented)

Priority follows the measured shares in section 6.2. "Benefit" is the upper bound, meaning the measured exclusive share of the removed work.

### C1 — Memoize `plan()` and the parsed T0 context per Experience · LOW RISK · Priority 1

- **Current:** `plan()` calls `experience.context()` (`json.loads(context_json)`) on every `advance` and on every window row in `measure`. `measure` also reads `context()["event"]["price"]`.
- **Suspected cost (measured):** category B = 15–28% on calm data and **68–74%** on volatile data. Volatile N=1,000 performs 111 parses per event at 2.68 ms each.
- **Concept:** compute `plan(experience)` and the T0 price once per `experience_id` and cache them in `ExperienceService` (or in a module-level cache keyed by the immutable `Experience`). Drop the entry when the experience leaves `_pending`. Return immutable views (or copies), so no caller can mutate the cached decision. `advance` and `measure` never mutate `decision` today.
- **Expected benefit:** removes almost all of B. Estimated replay reduction is ~20–28% on calm data and ~65–74% on volatile data. Live `observe` latency improves by the same factor.
- **Correctness risk:** low. `plan` is pure over an immutable dataclass. The risks are cache lifetime (not a correctness issue) and accidental mutation of a shared cached dict (guard with a test).
- **Files likely affected:** `experience/engine.py` (signature or helper), `experience/service.py`. This **collides with ADR-028's uncommitted `engine.py` change**, so it must be sequenced after EXC1 lands.
- **Validation:** X3–X6 differential tests; a mutation guard test; the existing `test_experience.py` suite.

### C2 — Lazy T0 context: build `context_json` only when the fingerprint changes · LOW RISK · Priority 2

- **Current:** `freeze()` always builds and serializes the full context — `frozen_json(context)`, `canonical_hash(output)`, `canonical_hash(event)`, `canonical_hash(config)` — even though `observe` only needs `scope` and `fingerprint` unless the fingerprint differs from `_last[scope]`. That is ~94% of events on calm data and ~36% on volatile data.
- **Suspected cost:** the context and provenance part of category A. On calm data, A is 33–38%; about two of the ~four output walks can be skipped on no-snapshot events.
- **Concept:** split `freeze` into `identify(config, event, output) → (scope, fingerprint)` and `materialize(...) → Experience`. Call `materialize` only when a new snapshot is needed. `freeze()` keeps its exact output for VALID-1 L6, which calls it directly.
- **Expected benefit:** roughly half of A on calm data (~15–19% of replay). Small on volatile data.
- **Correctness risk:** low. Snapshot bytes are produced by the same code on the same inputs. The residual risk is scope/fingerprint drift between the split and the original `freeze`. A property test pins `identify(...) == (freeze(...).scope, freeze(...).fingerprint)`.
- **Files:** `experience/engine.py`, `experience/service.py` (ADR-028 collision, as in C1).
- **Validation:** X1, X3 and X6; golden snapshots, including ADR-028 legacy fixtures.

### C3 — Canonicalize and encode the recorded output once per `observe` · LOW–MEDIUM RISK · Priority 3

- **Current:** in one `observe`, the same output is walked by `canonical_serialize` about four times: `freeze` serialize, the `output_hash` hash, `frozen_json` (context subset) and the digest hash. There are 18 top-level walks per event overall, counting config and append payloads.
- **Suspected cost:** A + C = 44–52% on calm data. cProfile puts it in the recursive walk, not in JSON or SHA-256.
- **Concept:** (a) walk once, reuse the canonical tree, and derive `output_hash` and the digest from **one** compact `json.dumps` of it. The digest's canonical JSON for the tuple is exactly `"[" + enc(event) + "," + enc(output) + "," + enc(completeness) + "," + enc(metadata) + "]"`, so the output encoding can be reused byte for byte. (b) Cache `canonical_serialize(config)` and the config hashes, which are constant per service. (c) Optionally add a fast path that skips re-canonicalizing input already known to be canonical JSON. Replay output is JSON-decoded, and live output comes from `canonical_serialize`.
- **Phase 1 feasibility check (scratch, not a test):** on 300 recorded calm rows, the composed digest equals `canonical_hash((event, output, completeness, metadata))` in 300 of 300 cases, and `canonical_serialize(output) == output` held for every recorded output. This is evidence that (a) is possible, not proof for all inputs.
- **Expected benefit:** reduces A + C by an estimated 50–75%, about 25–38% of calm replay.
- **Correctness risk:** low–medium. Byte-identity depends on exact `json.dumps` options (`sort_keys=True`, `separators=(",", ":")`) and on canonical form, where (c) is the risky part: Decimal/datetime/dataclass values must never reach the fast path. It needs exhaustive equality tests against `canonical_hash` on random, legacy and ADR-028 fixtures. (a) and (b) are low risk. (c) is medium.
- **Files:** `experience/engine.py`, `experience/service.py`, possibly a helper in `artifacts.py` (shared by every package; Architect coordination).
- **Validation:** X1, X3, X6 and X7 (digest values in `seen` must be identical), plus a property test `new_hash(x) == canonical_hash(x)`.

### C4 — Read-before-write fast path for Experience appends that already exist · MEDIUM RISK · Priority 4

- **Current:** every Experience append runs a full write transaction and a `COUNT(*)` even when the row exists. The count is computed unconditionally but only used when `expected_count` is set, which Experience never sets. On SQLite that is ~1.2–1.3 ms per append. On PostgreSQL it is 7+ round trips (inferred).
- **Suspected cost:** D + F = 5–15% on calm data (SQLite; the share falls as N grows) and ~6% on volatile N=1,000. Likely more on PostgreSQL.
- **Concept:** (a) compute `COUNT(*)` only when `expected_count is not None`. (b) For Experience streams, first read `content_hash` by `(stream, key)` without a write transaction. If it equals the digest, return `False`. If it differs, raise `journal_identity_conflict`. If it is absent, fall through to today's transactional append. Rows are append-only and never updated, so a positive read result is final.
- **Expected benefit:** most of D + F (≈ 5–15% calm on SQLite; potentially larger on PostgreSQL).
- **Correctness risk:** medium. `storage.py` is shared by every writer (paper, research, checkpoints via PERF-1, Payload V2 via ADR-027). (a) alone is behavior-identical. (b) changes the locking pattern and must keep X7 and the concurrent-writer guard (`expected_count`) intact. It is safest as an opt-in method (for example `append_idempotent`) used only by Experience, rather than a change to `append`.
- **Files:** `storage.py` (shared, persistence domain), `experience/service.py`, `experience/repository.py`.
- **Validation:** X1, X2, X7 and X10; SQLite + PostgreSQL tests (`test_experience_postgres.py`); a concurrent-writer regression test; a Security review (persistence, section 12 of AGENTS.md).

### C5 — Cache resolved type hints in `decode` · LOW RISK · adjacent (outside Experience)

- **Current:** `_decode` calls `get_type_hints(model)` per dataclass per call. That costs ~1 ms per replayed event (`compile()` of string annotations).
- **Suspected cost:** 1.8–9.6% of calm replay. It is a fixed per-event cost.
- **Concept:** memoize `get_type_hints` per model (`functools.cache`).
- **Correctness risk:** low; the type hints of a class are static.
- **Owner:** `artifacts.py` is shared. This is outside PERF-2's Experience scope and is listed for the Architect to assign (Q-P2-5).

### C6 — Skip re-appending Experience rows during replay below a verified watermark · HIGH RISK · not recommended in V1

- **Concept:** on replay, trust that Experience rows up to a verified research sequence are complete, and skip appends and verification.
- **Risk:** it removes the EX1 rule "replay fills missing keys" (X7) and removes replay's re-verification of historical Experience rows (X1 enforcement). Proving completeness needs a new durable watermark or a checkpoint field, which is PERF-1's checkpoint format and is out of bounds.
- **Benefit:** D + E + F, which C4 captures most of anyway.

### C7 — Experience V2: reference instead of copy; bounded fingerprint · HIGH RISK · separate ADR (ADR-027 Decision 5)

- Stops embedding `pnf.columns/transitions`, full `structure`/`trendline` and `runtime_config` in T0 contexts. Bounds the fingerprint (the pivot/level window). Uses the stored `output_hash` as the digest.
- It would remove the O(i) term itself, making `observe` ≈ O(1) per event. That is the only candidate that changes the **asymptotic** class.
- It changes `POLICY`, ids, scopes and snapshot content, and needs a rule for V1 experiences still pending at cutover. **Quant/Architect decision**, not a performance change. It is out of PERF-2 scope. C1–C4 remain valid under V1 and V2.

### C8 — Move Experience off the recovery critical path (asynchronous or deferred observe) · HIGH RISK · not recommended

- Readiness could be reported before Experience catches up. This changes write ordering (X2), failure semantics (X10) and the single-writer model. It needs an architecture decision and is listed only for completeness.

## 10. Risk matrix

| Candidate | Measured target share (calm / volatile) | Expected gain | Risk | Contract change | Shared-file impact | Recommended order |
|---|---|---|---|---|---|---|
| C1 plan/context memo | B: 15–28% / 68–74% | high | LOW | none | `experience/engine.py` (ADR-028) | 1 |
| C2 lazy context | ~½ of A: ~15–19% / small | medium | LOW | none | `experience/engine.py` (ADR-028) | 2 |
| C3 serialize once | A+C: 44–52% / 15–17% | high on calm data | LOW–MEDIUM | none (byte-identical) | possibly `artifacts.py` | 3 |
| C4 append fast path | D+F: 5–15% / 3–6% (SQLite) | medium; PG likely more | MEDIUM | none | `storage.py` | 4 |
| C5 decode type-hint cache | 1.8–9.6% / ≤1% | small–medium | LOW | none | `artifacts.py` | assign separately |
| C6 replay append skip | ⊂ D+E+F | small over C4 | HIGH | recovery semantics | checkpoint (PERF-1) | reject for V1 |
| C7 Experience V2 | removes the O(i) term | asymptotic | HIGH | new policy/ids | Experience, ADR-027 | separate ADR |
| C8 async observe | n/a | readiness only | HIGH | ordering/failure | runtime (PERF-1) | reject for V1 |

C1–C4 target work that accounts for ~80–86% of calm replay time (calm N=5,000: A+B+C+D+F = 86%) and ~90% of volatile replay time. They do not remove all of it. **Replay stays O(N²) until C7 or ADR-027 bound the recorded collections.** C1–C4 only lower the constants. Estimates must be re-measured after each step; the shares are not additive once one of them is removed.

## 11. VALID-1 verification strategy

ADR-030 (VALID-1) never runs `ExperienceService`, because it is a journal writer (ADR-030 Decisions 6, L6, and 14). Experience code is out of its scope (Q-V8 resolved). VALID-1 can still contribute evidence **without modification**:

1. **L6 RECORDED/RECOMPUTED parity (after VALID-1 Phase 2B).** Over a journal backup, RECORDED mode reads `experience:v1:snapshots`, and RECOMPUTED mode calls the pure `freeze()`/`fingerprint()`. If C2 or C3 change the internals of `freeze()`, the L6 comparison of recorded snapshots against the optimized `freeze()` on the recorded research rows is an independent byte-identity check (X3). C2 must keep `freeze()` as a public function with identical output, so that this still holds.
2. **Outcome kernel parity (Q-V8).** VALID-1 pins its own excursion kernel to `measure()` for the EX1-equivalent definition. That parity test is a regression tripwire for X4 after C1 changes how `measure` obtains `plan`.
3. **Sealed datasets and prefix hashes (G2).** VALID-1's sealed, hashed observation datasets can serve as fixed, reproducible replay corpora for the PERF-2 differential harness, including journal-derived segments once the 2B extractor exists.

VALID-1 does not replace the PERF-2 proof obligation. Phase 2 of PERF-2 must add its own **differential equivalence harness**:

- Run the baseline and the optimized code over the same journals (calm, volatile, ADR-028 legacy fixture `experience_journal_9016004.json`, and multi-pattern/WAIT/BUY/SELL test fixtures).
- Compare every Experience row (X1), the per-stream sequence order (X2), and `encode_state(checkpoint_state())` bytes after every prefix k (X6).
- Run fault injection at every Experience append (X7, X10) and a conflict mutation corpus (X7).
- Check live-vs-replay parity (X8).

## 12. Dependencies and conflicts

| Track | Relationship | Effect on PERF-2 |
|---|---|---|
| PERF-1 / ADR-029 (`claude/recovery-checkpoint-v1`) | Owns checkpoint scheduling, shutdown, readiness metrics, checkpoint format and state, and its benchmark. Modifies `research/runtime.py`. | PERF-2 changes none of these. PERF-1's benchmark `experience_observe` component remains the macro metric. C6 and C8 are rejected partly to stay out of PERF-1's scope. |
| ADR-028 / EXC1 (`NEXORA-EXPERIENCE-COMPAT`, uncommitted `experience/engine.py`) | **Direct file collision** for C1–C3. | PERF-2 Phase 2 is **blocked on ADR-028 landing**; rebase onto it and add its legacy fixture to the equivalence corpus. |
| ADR-027 Payload V2 (`claude/research-journal-payload-v2`) | Changes how the recorded output is stored and reconstructed. Decision 5 proposes Experience V2 (= C7). | C1–C4 are independent of V2 because `observe` still receives the same output dict. C7 belongs to the Experience V2 ADR. |
| ADR-030 VALID-1 (`claude/replay-validation-v1`) | Consumer of pure `freeze()` (L6) and a `measure()` parity pin. | PERF-2 must keep `freeze()`/`measure()` signatures and outputs stable. VALID-1 Phase 2B is needed for journal-backed L6 evidence. |
| `storage.py`, `artifacts.py` (shared) | C3(c), C4 and C5 touch shared persistence and encoding. | Architect coordination and Security review for C4. |
| `NEXORA-TRENDLINE` (`claude/trendline-engine-v1`) | Branch diff touches `experience/engine.py`, but the branch is 17 commits behind `main` and its content appears superseded. | No active conflict, noted for the Integrator. |

## 13. Out of scope

- Any production code change in Phase 1, including `ExperienceService`, engines, storage and runtime.
- Checkpoint scheduling, format, state, graceful shutdown, readiness metrics and the PERF-1 benchmark.
- Journal format and compaction (ADR-027) and Experience V2 policy/identity changes (C7).
- Changes to trading, decision, signal, risk or paper semantics, and any change to MFE/MAE or horizon definitions.
- VALID-1 code or ADR-030.
- PROD or legacy-database benchmarking.

## 14. Open decisions

| ID | Owner | Question | Proposed default |
|---|---|---|---|
| Q-P2-1 | Architect (Rin) | Accept C1 → C2 → C3(a,b) as the Phase 2 scope, sequenced after ADR-028 lands? | Yes. C3(c) and C4 each get a separate review. |
| Q-P2-2 | Architect + Security | C4: an opt-in `append_idempotent` for Experience, or the `COUNT(*)`-only fix (a) in shared `append`? | Start with (a), which is behavior-identical. Then (b) as opt-in with a Security review. |
| Q-P2-3 | Architect | Is a PostgreSQL measurement on an isolated, non-PROD instance required before C4 is prioritized? | Yes, if PROD uses PostgreSQL. Its per-append round trips are the unmeasured risk. |
| Q-P2-4 | Architect | Commit the Phase 1 harness as `scripts/experience_replay_benchmark.py` (evidence only, like PERF-1's)? | **RESOLVED (Rin, Phase 1B):** yes. Committed in Phase 1B (section 16). The generator is duplicated, not imported, because PERF-1's script is not on `main`; see Q-P2-8. |
| Q-P2-5 | Architect | Owner for C5 (`decode` type-hint cache, `artifacts.py`)? | A small separate fix outside PERF-2. |
| Q-P2-6 | Architect + Quant | Is Experience V2 (C7 / ADR-027 Decision 5) the intended path to remove the O(N²) term? | Separate ADR. PERF-2 does not decide policy. |
| Q-P2-7 | Architect | Acceptable Phase 2 target, for example calm N=5,000 full replay ≤ X s? | No target frozen here. Measure after each candidate. |
| Q-P2-8 | Architect / Integrator | After PERF-1 merges, should both benchmarks share one generator module? The calm profile is currently a byte-identical copy of PERF-1's `synthetic_events` (section 16.3). | Consolidate after both land. No change in Phase 1B, to avoid touching PERF-1. |

## 15. Consequences

- No behavior, stream, identity, checkpoint or config change in Phase 1.
- The prior "93–96%" figure is corrected to be **profile-dependent** (80–88% calm, rising with N; 95–97% volatile). The dominant sub-cost differs by profile (serialize/hash on calm data vs. context re-parse on volatile data), so both profiles must be kept in every future measurement.
- Experience cost affects **live ingest latency** as well as recovery. Live `observe` grows to tens to hundreds of milliseconds per event as history grows.

## 16. Rin Phase 1 decisions and Phase 1B record

### 16.1 Phase 1 decisions (Rin, relayed 2026-09-25)

Rin's decisions reached this workstream as a relayed instruction, not as a written review document. Only what was relayed is recorded here:

- **Phase 1 is APPROVED.**
- **Phase 1B scope:** move the synthetic profiling harness into the PERF-2 branch as reproducible repository tooling; support deterministic calm and volatile profiles with configurable N; report total replay time and the `ExperienceService.observe` contribution; add tests for deterministic generation and harness safety; update this ADR.
- **Not in Phase 1B:** C1, C2, C3, C4 and C5 are not implemented. `ExperienceService` production behavior is not modified. `storage.py` and `artifacts.py` are not modified. The PERF-1 and ADR-028 worktrees are not touched. PROD and the legacy 51.8 GB journal are not used.
- **ADR-028 remains the blocker for production optimization.**
- Q-P2-4 is resolved (harness committed).

Q-P2-1, 2, 3, 5, 6 and 7 were not part of the relayed decision. They stay **open** in section 14 until Rin's written record is available. This ADR does not infer them.

### 16.2 Harness (`scripts/experience_replay_benchmark.py`)

```text
.venv/Scripts/python scripts/experience_replay_benchmark.py --workdir <new empty dir> \
    --cases calm:500 calm:1000 volatile:250 --runs 3 [--warmup K] [--breakdown] \
    [--seed S] [--reuse] [--output results.json]
```

- **Cases:** `profile:N`, with any positive N and any mix of profiles.
- **Build:** each case builds its journal by live `ResearchRuntime.ingest` into `<workdir>/<profile>-n<N>/journal.sqlite`. It records the live `observe` cost, then writes `benchmark-manifest.json`, which holds the harness id, profile, N, seed, the events' SHA-256 and the ordered journal row digest.
- **Replay:** each run is a fresh `ResearchRuntime(config, journal)` with checkpoints disabled, i.e. a full replay. The harness asserts `mode == "full"` and `replayed == N`. It reports total wall time, the summed `observe` time and its share, per-call `observe` latency (mean, median, p95, max, per-decile means) and the canonical hash of the encoded checkpoint state.
- **Breakdown:** `--breakdown` adds one attributed replay. It records exclusive time per category (Phase 1 categories A–H plus non-Experience work) and call counts.
- **Instrumentation:** functions are wrapped in-process with `setattr` and restored in `finally`, including the benchmark-owned connection proxy. No product file is modified. A test asserts that every wrapped attribute is restored.
- **Nondeterminism check:** the run fails with `nondeterministic_replay_state` if replay state hashes differ between runs, including the breakdown run. It fails with `replay_changed_journal` if any replay changes the journal row digest.

### 16.3 Profiles and determinism

| Profile | `step_tenths` | Seed | First-100-events SHA-256 (pinned in tests) |
|---|---|---|---|
| calm | 3 | 20260925 | `aece8bbb…517525fef` |
| volatile | 20 | 20260925 | `d282d886…bef9798e89` |

Determinism evidence:

1. **Generator:** identical output on repeated calls. It is prefix-stable (`events(N)[:m] == events(m)`), a different seed changes the output, and the golden digests above are pinned in tests.
2. **PERF-1 comparability:** `synthetic_events("calm", 5000)` equals PERF-1's `recovery_benchmark.synthetic_events(5000)` event for event. This was checked once by reading PERF-1's script without modifying its worktree. No `.pyc` was written there; the existing cache file's timestamp is unchanged.
3. **Build:** two independent builds produce identical ordered journal digests (test, N=40). Across harnesses: the committed harness's calm builds match the Phase 1 scratch-harness journals, built hours earlier, row for row. N=500: 1,127 rows, `8c9f7d80df3169fc…`. N=1,000: 2,404 rows, `00dad59916f7a819…`. N=2,000: 4,844 rows, `b0a48c8f55c476d0…`. All three are identical.
4. **Replay:** every case's runs, including the attributed run, produced one identical checkpoint-state hash, and no replay changed the journal (section 16.5).

### 16.4 Safety

The harness refuses to run (`BenchmarkRefused`) when:

- `NEXORA_ENV=production`;
- the work directory is inside, equal to, or a parent of any `NEXORA_RUNTIME_ROOT`, `NEXORA_JOURNAL_PATH`, `NEXORA_STATE_PATH`, `NEXORA_CHECKPOINT_PATH`, `NEXORA_CACHE_PATH` or `NEXORA_LOG_PATH`;
- the work directory overlaps the repository;
- the work directory is a file;
- the work directory is non-empty without `--reuse`.

With `--reuse`, a journal is opened only when its manifest matches this exact case and the journal's row digest still matches the manifest. A foreign journal without a manifest, or a tampered one, is refused. The harness creates only `SQLiteJournal` instances on paths it built. It never reads a DSN and never constructs `PostgresJournal`. Each test asserts that a refused directory is left untouched.

### 16.5 Benchmark evidence (committed harness)

Harness file SHA-256 `93a815ce…a1a4fd2135` (commit `756341f`). The run's metadata records commit `e75764a`, because the file was committed with identical content after the run started. Environment: same workstation and Python 3.13.3 as Phase 1, SQLite, runs sequential.

**The Phase 1B full benchmark run did not complete.** The session hosting it ended during case `calm:2000`, so no result file was written. The builds for calm N=500, 1,000 and 2,000 completed, with manifests, and supply determinism evidence item 3. Timing evidence for the committed harness is the post-sync recheck in section 17.4, which supersedes this run.

Smoke run of the committed harness before that run: calm N=120 took 0.72 s with `observe` at 76.6%; volatile N=80 took 2.67 s with `observe` at 92.3%. Each case had 2 runs plus a breakdown, and the state hashes were identical within each case.

### 16.6 Tests and validation (Phase 1B)

- `tests/test_experience_replay_benchmark.py`: 8 passed. It covers generator determinism, prefix stability and golden pins; invalid profile and size input; identical independent builds; replay that never changes the journal, with an `observe` share in (0, 1] and restored wrappers; `--reuse` accepting only matching manifests and refusing tampered or foreign journals; and workdir safety (production, runtime paths, repository, non-empty directory, file).
- Full `pytest` at `756341f`: 385 passed, 3 skipped (PostgreSQL/environment-gated).
- `ruff check` / `ruff format --check` / strict `mypy` on the two new files: clean.
- Repository-wide findings that predate this work: `ruff format --check` flags 19 unchanged source and test files plus the ADR-021 code block under the shared venv's ruff 0.16.8, and `mypy` reports missing `psutil` stubs in `apps/api/nexora_api/launch.py` and `tests/test_environment.py`. None of these files is touched by PERF-2.

### 16.7 Status of Phase 2

- **Blocked on ADR-028.** As of 2026-09-25, ADR-028 / EXC1 is still uncommitted in `NEXORA-EXPERIENCE-COMPAT` (no branch commit, not on `origin/main`, which is still `4f69e9c`). It modifies `experience/engine.py`, the file C1–C3 would change.
- **Unblock condition:** ADR-028 is merged to `main` (or its branch is frozen and approved as the base). PERF-2 then rebases, adds `tests/fixtures/experience_journal_9016004.json` to the equivalence corpus, and re-runs this benchmark as the Phase 2 baseline.

## 17. Integration sync and dependency recheck (main `f2d51ad`, 2026-09-25)

This section records the integration pass only. Sections 1–16 are unchanged in meaning. No optimization was implemented.

### 17.1 Sync

- **Before:** HEAD `3d07870` on base `4f69e9c`. The Phase 1B ADR text was completed and committed first, so the tree was clean before the rebase.
- **After:** rebased onto `origin/main` `f2d51ad` (PERF-1 merge, PR #35). HEAD is `b99fd3e`. The PERF-2 commits are now `596b393` (Phase 1, content identical to `e75764a`), `8762174` (harness) and `b99fd3e` (Phase 1B record). There were no conflicts. All three PERF-2 files are byte-identical to their pre-rebase versions, and the branch still adds only those three files relative to `origin/main`.

### 17.2 Dependency: ADR-028 / EXC1 — still BLOCKED

Verified from the current git and worktree state, not from notes:

- `NEXORA-EXPERIENCE-COMPAT` (`claude/experience-journal-compat-v1`) is still at `4f69e9c`, with no commits of its own. Six files are **staged but uncommitted**: ADR-028, `experience/engine.py` (M), the EXC1 task, compatibility tests, a fixture helper and the `experience_journal_9016004.json` fixture. File times are from 09:17–09:23 on 2026-09-25.
- ADR-028 status is `proposed (Rin review pending)`, and EXC1 status is `in_review`. There is no remote branch, and ADR-028 is not on `origin/main`.
- `packages/nexora/experience/` is **unchanged on main** since `4f69e9c`.
- Its uncommitted `engine.py` diff changes `freeze()` context construction. That is the same function C2 splits and C3 re-encodes, so the overlap is semantic as well as a shared file.
- **Other owners:** `NEXORA-TRENDLINE` also shows an `engine.py` diff against its old merge-base, but `git cherry` marks all its commits as patch-equivalent to main, its `engine.py` is identical to main's, and its worktree is clean. It is not an active owner. No other worktree touches `experience/`.
- **Conclusion:** ADR-028 still owns uncommitted changes to `experience/engine.py`, so PERF-2 cannot safely take ownership of C1–C3.

### 17.3 Main changes since `4f69e9c` — overlap

| Track | Files | Direct overlap with PERF-2 | Semantic overlap / invalidated assumptions |
|---|---|---|---|
| PERF-1 (ADR-029, PR #35) | `research/runtime.py` (age trigger, `last_checkpoint`, `recovery_status`), `apps/api/nexora_api/{launch,research,main}.py` (graceful stop, age setting), `scripts/recovery_benchmark.py`, tests | None: PERF-2 changed no runtime file | `_rebuild()`'s replay loop and its `observe` call are **unchanged**. Checkpoint *content* (`checkpoint_state.py`) and `checkpoint.py` are unchanged; only *when* checkpoints are written changed. PERF-1's benchmark independently measured `observe` at 79% / 84% / 86% of full replay at N = 500 / 1,000 / 2,000, matching section 6.1. `code_fingerprint()` still hashes every package file, so any Phase 2 Experience change invalidates all checkpoints and forces one full replay at deploy (ADR-022 by design). |
| VALID-1 (ADR-030, PR #34) | `packages/nexora/validation/*`, tests | None | Imports only `artifacts` (`canonical_hash`, `canonical_serialize`, `decode`) and engines. It does not import Experience or `storage`. L6 (`freeze()`) and the RECORDED mode are not in its Phase 2A, so section 11 items 1 and 3 remain future options. It is a `decode` consumer, which matters for C5 ownership. |
| M30 (ADR-026, PR #33) | `packages/nexora/m30_bias/*`, tests | None | Pure core with its own `freeze` state machine; there is no Experience, storage or runtime dependency. |
| Experience | — | unchanged on main | — |
| `storage.py`, `artifacts.py`, `pipeline.py`, `checkpoint*.py` | — | unchanged on main | Phase 1 cost-model assumptions hold. |

**Invariants X1–X11:** none is changed by the new main. The post-sync journals are row-identical to the Phase 1 journals (17.4), so Experience output did not change. PERF-1 adds verification obligations for Phase 2 without changing the invariants: X6 must now also hold across the age trigger, graceful-stop checkpoints and `recovery_status`. Phase 2 must therefore pass `test_checkpoint_schedule`, `test_recovery_checkpoint`, `test_startup_recovery`, `test_launch_graceful_stop` and `test_recovery_benchmark` unchanged.

### 17.4 Baseline recheck (committed harness at `b99fd3e`, synthetic temp data only)

Command: `experience_replay_benchmark.py --cases calm:500 calm:1000 volatile:250 --runs 2 --breakdown`, run in a new scratch directory outside the repository. No PROD, TSID runtime or legacy journal was involved.

**Measured:**

| Case | Median replay | ms/event | `observe` share | Phase 1 share | Run state hashes | Journal changed |
|---|---|---|---|---|---|---|
| calm:500 | 5.73 s | 11.5 | 78.6% | 79.7% | identical (3 incl. breakdown) | no |
| calm:1000 | 18.39 s | 18.4 | 83.2% | 84.5% | identical | no |
| volatile:250 | 29.26 s | 117.0 | 95.3% | 95.5% | identical | no |

- **Output identity:** the post-sync journals equal the Phase 1 (`4f69e9c`) journals row for row. calm:500 has 1,127 rows, calm:1000 has 2,404 rows and volatile:250 has 1,545 rows; all ordered digests are equal.
- **`freeze()` repetition:** exactly **6.0 `canonical_hash` calls per event** in `freeze` in every case, plus 2.0–2.9 `canonical_serialize` and 4.0–5.9 `frozen_json` calls per event, and 1.0 digest hash per event. The freeze serialize/hash category is 33.2% / 33.1% / 12.7%.
- **`context_json` parsing:** `Experience.context()` runs 6.4 / 9.7 / **96.1 times per event**, with `plan()` at 6.3 / 9.5 / 93.8 per event. The share is 14.2% / 24.2% / 66.9%.
- **Append / no-op transactions:** 1.25 / 1.40 / 5.18 Experience appends per event, each with its own `COMMIT`. The journal is unchanged after every replay, so none of them inserted a row. The `COMMIT` share is 15.3% / 9.5% / 6.7%.
- **`decode`:** `get_type_hints` is called **1.0 time per `decode(NormalizedPriceEvent)`**, at 0.84 ms per decode in isolation. The decode share is 10.1% / 6.3% / 0.9%.
- **Growth with history:** per-decile `observe` means rise from 6.8 to about 24 ms (calm:1000) and from 16.5 to 192.6 ms (volatile:250). At calm:1000 replay positions 100 → 250 → 500 → 750 → 1,000:
  - recorded output: 6.1 → 10.2 → 19.3 → 45.5 → 57.4 KB;
  - `columns`: 3 → 21; `transitions`: 5 → 56; `pivots` and `levels`: 1 → 19;
  - single `canonical_serialize(output)`: 0.25 → 2.28 ms; single `canonical_hash(output)`: 0.33 → 3.26 ms;
  - T0 `context_json`: 4.7 KB (first snapshot) → 26.5 KB (median) → 41.6 KB (last snapshot).

**Inferred (not a timing-only claim):** per-event Experience work is proportional to the size of collections that grow with history. `freeze` and the digest re-walk the whole recorded output (measured growing), and each pending experience's `plan()` re-parses a T0 context that embeds the same cumulative collections (measured growing). Replaying N events therefore performs Σ O(i) such walks, which is quadratic in N. That follows from these operation counts and sizes; the timings are consistent with it but are not the basis of the claim. A small calm:500 last-decile dip (7.0 ms) reflects fewer pending experiences at the end of that series, not a contradiction.

### 17.5 Validation (post-sync, `b99fd3e`)

| Suite | Result |
|---|---|
| Experience (`test_experience*.py`, 4 files incl. benchmark) | 55 passed, 1 skipped (PostgreSQL DSN not set) |
| Recovery / checkpoint (`test_checkpoint_schedule`, `test_recovery_checkpoint`, `test_startup_recovery`, `test_launch_graceful_stop`, `test_recovery_benchmark`) | 92 passed |
| M30 (`test_m30_bias_*.py`) | 96 passed |
| VALID-1 (`test_validation_*.py`) | 56 passed |
| Full `pytest` | 563 passed, 3 skipped |
| `ruff check .` | clean |
| `ruff format --check` | PERF-2 files clean. 22 files that predate this work are flagged (19 unchanged source/test files plus ADR-021/026/030 code blocks; shared venv ruff 0.16.8); none is a PERF-2 file. |
| `mypy` (strict) | PERF-2 files clean. 3 errors that predate this work: missing `psutil` stubs in `launch.py`, `test_launch_graceful_stop.py` and `test_environment.py`. |
| `git diff --check origin/main HEAD` | clean |

**Environment note:** the shared `.venv`'s editable install points at `D:\NEXORA\NEXORA`, a stale main worktree. Tests must run with an **absolute** `PYTHONPATH` to this worktree's `packages` and `apps/api`. With a relative path, `test_launch_graceful_stop`'s child process (`cwd=tmp_path`) resolves the stale `nexora_api` and fails with `ModuleNotFoundError: nexora_api.launch`. That is an environment artifact, not a code failure; with absolute paths the test passes.

### 17.6 Candidate re-assessment (not implemented)

| Candidate | Current evidence (calm 500–1k / volatile 250) | Risk | Files | Owner / conflict | In PERF-2? |
|---|---|---|---|---|---|
| C1 memoize `plan()` + parsed T0 context | category B 14–24% / 67% | LOW | `experience/engine.py`, `experience/service.py` | ADR-028 has uncommitted `engine.py` | yes, Phase 2A |
| C2 identify-then-materialize `freeze()` | about half of A: ~15–17% / small | LOW | `experience/engine.py`, `experience/service.py` | ADR-028 changes `freeze()` itself: direct semantic overlap | yes, Phase 2A (build on ADR-028's `freeze`) |
| C3 serialize/encode output once (a, b) | A + C 42–44% / 18% | LOW–MEDIUM | `experience/engine.py`, `experience/service.py` | ADR-028. C3(c), a canonical fast path, would need `artifacts.py`: not authorized | (a, b) yes; (c) no |
| C4 no-op append fast path | D + F 11–17% / 7% (SQLite) | MEDIUM | `storage.py` (shared) | storage/persistence ownership plus Security review; PostgreSQL unmeasured (Q-P2-3) | no, separate scope |
| C5 cache `decode` type hints | 6–10% / ~1% | LOW | `artifacts.py` (shared; VALID-1 and all packages use `decode`) | not PERF-2 unless transferred (Q-P2-5) | no |

**Recommended Phase 2A scope (when unblocked):** C1 → C2 → C3(a, b), confined to `experience/engine.py` and `experience/service.py`, rebased onto ADR-028. Required with it:

1. a differential equivalence harness (section 11) over calm, volatile and the ADR-028 `experience_journal_9016004.json` fixture, checking X1–X8 and X10;
2. the PERF-1 recovery suites unchanged (X6);
3. before/after measurement with `scripts/experience_replay_benchmark.py` on the same machine.

C4 and C5 stay out.

**Q-P2-8 update:** PERF-1's `scripts/recovery_benchmark.py` is now on main, so generator consolidation is possible. It stays deferred, because it is not needed for Phase 2A.

### 17.7 Readiness

**BLOCKED.** The only blocker is ADR-028 / EXC1: it is uncommitted and unmerged, and it owns `experience/engine.py`. Main has not moved beyond `f2d51ad`, and no invariant or Phase 1 assumption was invalidated.

**Unblock condition:** ADR-028 is accepted and merged to main (or committed on an approved branch that PERF-2 is directed to base on). Then rebase, re-run 17.4 as the Phase 2A baseline, and request Rin's Phase 2A decision.

## 18. Phase 2 implementation — C1, C2, C3(a, b) (2026-09-25)

The scope was authorized by Rin after EXC1 / ADR-028 merged (PR #36, `ec0aad5`). C3(c), C4, C5, Experience V2, async observe, and any journal, checkpoint-format or storage-format change are **not** implemented. Self-review only; Rin code review is pending.

### 18.1 Sync and baseline

- Rebased from `c69523b` (base `f2d51ad`) onto `origin/main` `ec0aad5`. There were no conflicts, and the four PERF-2 commits were kept (not squashed). Main's `experience/` equals EXC1's reviewed head `a7972d4`. The only other worktree with an `experience/engine.py` diff is the stale, fully merged `claude/trendline-engine-v1`; it is not an owner.
- **EXC1 baseline before any change:**
  - `test_experience_journal_compat.py`: 15 passed.
  - Experience suites: 55 passed, 1 skipped.
  - Recovery/checkpoint suites: 80 passed.

  These cover byte-identical old-writer rows, cold replay = checkpoint + delta (C12), ADR-028 presence semantics, and no rewrite.

### 18.2 Implementation

| Candidate | Change | Where |
|---|---|---|
| **C1** | `Plan(entry, risk, decision, t0_price)` and `plan_of(experience)` parse `context_json` **once** per Experience. `ExperienceService` caches it per pending `experience_id`, drops the entry when the Experience completes, and starts empty after `restore_state`. `advance()` and `measure()` take an optional keyword-only `prepared`. Without it they behave and call each other exactly as before, so the public contract holds (VALID-1 and `test_measure_replays_only_causal_window` use them unchanged). | `engine.py`, `service.py` |
| **C2** | `observe()` computes scope and fingerprint first. `materialize()` builds the full T0 context and `context_json` **only when the fingerprint starts a new Experience**, still before any journal write, as before. Public `freeze()` is now `recorded()` + `fingerprint()` + `materialize()`, and its result is equal to the baseline's. ADR-028 presence semantics are the same code (`ADDITIVE_OUTPUT_CONTEXT`, `key in output`). | `engine.py`, `service.py` |
| **C3(a)** | `recorded(output)` does **one** canonical walk and **one** compact sorted encoding per `observe()`. `output_hash` is the SHA-256 of that encoding. `observation_digest()` assembles the digest's canonical tuple encoding as `"[" + event + "," + output + "," + completeness + "," + metadata + "]"` from the same output text. Both equal `canonical_hash`, proven by tests. | `engine.py` |
| **C3(b)** | The canonical config, its hash and the scope (keyed by source, symbol, price source and units) are computed once per service. The config is an immutable `RuntimeConfig` at every construction site. | `service.py` |

- **Failure parity:** `observe()` still evaluates `current_signal()` on every event, so a malformed recorded `signals.latest` fails on every event as it did when the full context was built. Everything else `materialize()` serializes was already walked by `recorded()` or the digest.
- **Derived state:** the three caches live in one attribute, `ExperienceService._derived`, which is never checkpointed. **Shared-file touch:** `research/checkpoint_state.py` `COVERED_FIELDS` gains the name `"_derived"`, with a comment that it is derived and not persisted. `COVERED_FIELDS` is read only by the contract test `test_component_contracts_cover_every_attribute`. Encode and decode, `STATE_VERSION` and the checkpoint format are unchanged. Not approved by Rin; decision pending (18.7).
- `storage.py`, `artifacts.py`, the runtime, VALID-1, M30 and the API are unchanged.

### 18.3 Equivalence evidence

**Oracle:** `tests/experience_baseline/` holds the ec0aad5 `engine.py` verbatim and `service.py` with only its import block changed (to import the baseline engine). `tests/test_experience_replay_equivalence.py` (20 tests) runs both implementations through the real `ResearchRuntime`:

| # | Requirement | Test and result |
|---|---|---|
| 1–7 | Rows, write order, IDs, fingerprints, `context_json`, MFE/MAE and outcome windows, lifecycle | Live ingest of calm (160) and volatile (140) data. **All journal rows are equal as `(sequence, stream, key, content_hash, payload)`.** The volatile run asserts BUY and SELL plans, `ENTRY_TRIGGERED`/`ACTIVE`/`INVALIDATED` states and entry-zone outcomes are present, so the plan cache is exercised. Plus pure checks: `plan_of`, `advance`/`measure` with and without `prepared` match the baseline for every horizon, and `freeze()` matches the baseline for every recorded row × {additive fields absent, explicit null, present}, and for raw (non-canonical) live output. |
| 8 | Checkpoint state byte-identical after equivalent prefixes | The checkpoint **blob hash after every event** is equal between implementations. Warm restore (checkpoint + delta) and cold replay are each byte-equal across implementations. |
| 9 | Replay and live converge | Replay by both implementations leaves rows unchanged and yields equal state. An optimized live journal equals the baseline one and replays to the same state. Checkpoint + delta equals cold replay (value level; see 18.6 O1). |
| 10 | Conflict detection | `experience_event_identity_conflict` still raises; a tampered snapshot still fails replay with `journal_identity_conflict` under both implementations. |
| 11 | EXC1 old journal | The `9016004` fixture replays under both implementations to equal state, with rows byte-identical. All 15 EXC1 tests pass. |
| 12 | Fault/restart | An injected failure at the 3rd, 40th or 170th Experience append gives identical rows under both implementations, no duplicate or missing rows against a clean run, and the same replay state. |
| — | **The tests catch bugs** | Six mutations each make the comparison fail: a stale fingerprint (C2), a weakened digest (C3; only `_seen` changes, caught by the prefix checkpoint hash), dropped additive fields (ADR-028), one plan shared by all Experiences (C1), a wrong cached T0 price (C1), and a different output-hash encoding (C3). |

**At benchmark scale:** journals built live by the ec0aad5 code and by the optimized code are **identical**: calm 500 has 1,127 rows, calm 1,000 has 2,404 and volatile 250 has 1,545, all with equal ordered digests. Their replay state hashes are identical too.

### 18.4 Before / after (same harness, same machine, sequential, idle)

Evidence: [tasks/evidence/PERF2-phase2-benchmark.md](../../tasks/evidence/PERF2-phase2-benchmark.md), with raw before/after JSON.

The "before" run used `git archive` of the base (Experience code = ec0aad5); the "after" run used the working tree. Both used `experience_replay_benchmark.py --cases calm:500 calm:1000 volatile:250 --runs 3 --breakdown`. The harness now wraps only functions present in the loaded code, and maps the new function names to the same cost categories.

| Case | Replay median before → after | ms/event | Speed-up | `observe` share |
|---|---|---|---|---|
| calm 500 | 6.29 → **3.15 s** | 12.6 → 6.3 | 2.0× | 78.6% → 61.7% |
| calm 1,000 | 19.83 → **8.91 s** | 19.8 → 8.9 | 2.2× | 83.7% → 63.6% |
| volatile 250 | 33.44 → **7.75 s** | 133.8 → 31.0 | 4.3× | 95.4% → 79.1% |

Attributed replay run, before → after, in seconds and share of that run (calm 500 / calm 1,000 / volatile 250):

| Category | calm 500 | calm 1,000 | volatile 250 |
|---|---|---|---|
| `freeze` / serialize / hash of the output | 2.07 (34%) → 0.69 (20%) | 7.17 (34%) → 2.85 (29%) | 4.50 (13%) → 2.73 (36%) |
| Context parsing | 0.83 (14%) → 0.01 (0.2%) | 5.03 (24%) → 0.04 (0.4%) | 23.18 (68%) → 0.23 (3%) |
| Idempotency digest | 0.55 (9%) → 0.06 (2%) | 2.36 (11%) → 0.20 (2%) | 1.70 (5%) → 0.12 (2%) |
| Append/write (commit + encode + SQL) | 1.23 → 1.30 | 2.96 → 2.93 | 2.75 → 2.53 |

| Per event | calm 500 | calm 1,000 | volatile 250 |
|---|---|---|---|
| `Experience.context()` parses | 6.41 → **0.04** | 9.71 → **0.06** | 96.1 → **0.64** |
| Full-output canonical walks | ~4 → 1 + new-snapshot rate (0.04) | ~4 → 1 + 0.06 | ~4 → 1 + 0.64 |
| Experience appends / commits | 1.25 → 1.25 | 1.40 → 1.40 | 5.18 → 5.18 (unchanged; C4 not done) |
| Live ingest mean | 16.8 → 11.5 ms | 31.9 → 19.7 ms | 165.3 → 63.4 ms |
| Live `observe` mean | 9.5 → 4.1 ms | 18.1 → 6.3 ms | 129.0 → 26.4 ms |

Append/write cost is unchanged by design. It becomes the largest single Experience item on calm data (commit 20–27%). `decode` (C5) is now 13–18% of calm replay. Volatile "after" had one noisy run (12.5 s); the median is reported.

### 18.5 Complexity after Phase 2

**Phase 2 does not remove the O(N²).** It removes repeated work per event, so the constant shrinks, but the per-event cost still grows with history. "After" per-decile `observe` means still rise: 3.5 → 7.6 ms (calm 1,000) and 6.8 → 28.5 ms (volatile 250).

The remaining O(i) work per event:

1. The one `recorded()` canonical walk and encoding of the cumulative recorded output (`columns`, `transitions`, `structure`, `trendline`, and so on);
2. `fingerprint()` over all `pivots` and `levels`;
3. `storage._verify` of each O(i) research row;
4. on new-snapshot events, `materialize()` serializing a T0 context that embeds the same cumulative collections.

Removing these requires bounding the recorded collections or the frozen context: Experience V2 (C7) or ADR-027. That is a policy or format decision outside PERF-2.

### 18.6 Findings

- **O1 (predates this work, not Experience):** a live runtime's checkpoint blob differs from a replayed runtime's even in the baseline. Live events keep their Decimal exponent (`2000.0`) while journal-decoded events do not (`2000`); values and canonical hashes are equal. Byte-exact live-vs-replay or checkpoint-vs-cold comparisons therefore hold only for data without trailing zeros (for example the EXC1 fixture); the recovery suite's `state()` comparison holds generally. This is reported for ADR-022/PERF-1 awareness; PERF-2 does not change it.
- **O2:** because `engine.py` and `service.py` change, deployment rejects every existing checkpoint once (whole-package code fingerprint) and performs one full replay, now roughly 2–4× faster than before.

### 18.7 `COVERED_FIELDS` / `"_derived"` — Rin decision required (not approved)

Rin has **not** approved the shared checkpoint contract change: Phase 2A scope is C1 → C2 → C3(a, b), and `checkpoint_state.py` is a shared recovery/checkpoint area. Evidence for that decision:

**Exact diff of `packages/nexora/research/checkpoint_state.py`:** a comment plus one list entry.

```diff
 # Every attribute of every checkpointed component. Configuration attributes are
-# rebuilt from the current config; all others are persisted below.
+# rebuilt from the current config; all others are persisted below, except
+# ExperienceService._derived: caches derived from the config and pending Experiences,
+# never persisted and rebuilt on demand after restore (ADR-031).
 COVERED_FIELDS: dict[str, frozenset[str]] = {
@@
             "_completed",
             "_seen",
+            "_derived",
```

**Which candidate needs `_derived`:** C1 (`plans`, the per-pending-Experience `Plan` cache) and C3(b) (`config`, the canonical config and hash, and `scopes`, the scope per source key). These caches must outlive one `observe()` call, so they are instance state. C2 and C3(a) do not need it; they are per-call. `_derived` is **not** needed for correctness: all three caches are pure functions of immutable inputs, and a restore starts empty.

**What fails if `checkpoint_state.py` is reverted:** tested in a scratch copy with C1/C2/C3 unchanged and only `checkpoint_state.py` reverted to HEAD. Running `test_recovery_checkpoint`, `test_experience_replay_equivalence`, `test_experience_journal_compat`, `test_checkpoint_schedule`, `test_startup_recovery` and `test_experience` gave **1 failed, 160 passed**. The single failure is `test_component_contracts_cover_every_attribute`, the guard that every instance attribute of a checkpointed component is declared. **No equivalence, EXC1, recovery or checkpoint-parity test fails.** It is guard bookkeeping, but it cannot be removed while all tests stay green, so it was **not** reverted.

**Serialized bytes, schema and restore semantics: unchanged.**
- `COVERED_FIELDS` is read only by that contract test; `encode_state` and `restore_state` do not use it.
- `STATE_VERSION = 1` and `checkpoint.py` (`SCHEMA_VERSION = 2`, `FORMAT`) are unchanged.
- The equivalence tests show the checkpoint **blob hash after every event** is byte-equal to the ec0aad5 baseline, and that warm restore (checkpoint + delta) is byte-equal across implementations.
- `ExperienceService.checkpoint_state()` still returns exactly `last, pending, states, samples, completed, seen`. `restore_state` assigns the same six maps and resets `_derived`.

**Checkpoint/recovery equivalence, before → after:**

| Suite | Before (ec0aad5 Experience code) | After (Phase 2) |
|---|---|---|
| Recovery/checkpoint (5 files) | 92 passed | 92 passed |
| EXC1 compatibility | 15 passed | 15 passed |
| Equivalence: live rows, checkpoint blob after every prefix, warm = cold, fault/restart | — | 20 passed (baseline vs optimized) |

**Options for Rin:**

| Option | Effect |
|---|---|
| **A (recommended).** Accept the one-name `COVERED_FIELDS` entry | Keeps C1 and C3(b) as implemented. The guard stays exhaustive and documents why `_derived` is not persisted. |
| **B.** Move the caches off the service instance (module-level memo keyed by immutable values) | No `checkpoint_state.py` change. But it adds process-global cache state (memory retention of T0 contexts, lifetime not tied to the runtime) and sidesteps the guard's inventory of component state. Not recommended. |
| **C.** Drop C1 and C3(b) caching | No contract change, but loses most of the gain (C1 alone removed 68% of volatile replay). Not recommended. |

### 18.8 Deferred (not implemented)

| Item | Status |
|---|---|
| C3(c) canonical fast path | Needs `artifacts.py`; not authorized. |
| C4 no-op append fast path | Needs `storage.py`, a PostgreSQL measurement and Security review. It is now the largest remaining Experience cost on calm data. |
| C5 `decode` type-hint cache | Needs `artifacts.py`; no owner assigned (Q-P2-5). It is now 13–18% of calm replay. |
| C7 Experience V2 | The only candidate that removes the O(i) term; a policy change (Q-P2-6). |

### 18.9 Regression

| Check | Result |
|---|---|
| PERF-2 tests (equivalence 20 + benchmark 8) | 28 passed |
| EXC1 compatibility | 15 passed |
| Experience suites (6 files) | 90 passed, 1 skipped (PostgreSQL DSN not set) |
| Recovery/checkpoint (5 files) | 92 passed |
| VALID-1 | 56 passed |
| M30 | 96 passed |
| Full `pytest` | **598 passed, 3 skipped** |
| `ruff check .` | clean |
| `ruff format --check`, changed files | clean |
| `mypy` (strict), changed files | clean |
| `mypy`, whole repo | 3 errors that predate this work: missing `psutil` stubs in `launch.py`, `test_launch_graceful_stop.py` and `test_environment.py` |
| `git diff --check` | clean |

All suites ran with an absolute `PYTHONPATH` to this worktree.
