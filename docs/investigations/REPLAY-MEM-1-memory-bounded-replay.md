# REPLAY-MEM-1 — Memory-Bounded Research Replay Investigation

Status: **Investigation complete; one minimal mitigation committed; architecture proposals pending Rin review.** No PR, no merge, no tag.
Date: 2026-09-26
Role: DEV-PERF · Workstream REPLAY-MEM-1 · Branch `claude/replay-memory-bound-v1` · Base `origin/main` `a449f19` (`a449f1980bdd70b19c74656f5a2b70cb8ee4b99d`)
Trigger: release 1.4.0 is blocked at Gate 2 because full research replay/recovery is not memory-bounded (PROD journal-copy dry-run: `MemoryError` at ~49k/67k events inside `SQLiteJournal.iter_rows` → `fetchall()`).

All measurements below use **synthetic data only**, produced by `scripts/replay_memory_harness.py` in a scratch directory. No PROD or development journal, checkpoint or runtime directory was opened. Nothing here changes trading, P&F/OX, Pattern, Entry Readiness, Signal or Experience semantics, the journal format, the checkpoint format or any shared contract.

## 0. Summary

| # | Finding | Growth | Measured (synthetic) | Projected to PROD dry-run parameters | Fix |
|---|---|---|---|---|---|
| 1 | Replay memory growth | **Retained: O(total events)** (≈2.0–2.2 KB/event) + O(pending Experiences × history). **Transient: O(row size)**, dominated by the journal reader's page of raw rows | Untraced peak RSS: 20k 112 MB, 50k 218 MB; retained 43.3 MB / 103.9 MB | Python-level peak ≈ 0.50 GB, RSS/commit ≈ 0.58 GB | Reader page bounded (committed, §6.1): 20k 112 → 96 MB, 50k 218 → 176 MB, ≈ 0.38 GB projected. Retained growth needs an architecture decision (§6.2) |
| 2 | Checkpoint write spike | O(checkpoint size): peak **3.5 × blob** on top of live state (JSON-ready copy + `str` + `bytes`), **6.4 ×** once any character above U+00FF (e.g. `→` in signal evidence) makes the JSON `str` 2 bytes/char | 20k: 32.9 MB blob, +115 MB traced; 50k: 85.5 MB blob, +549 MB traced, RSS +373 MB | 164 MB blob → ≈ 1.05 GB traced | Proposal only (§6.3) |
| 3 | Checkpoint restore spike | O(checkpoint size): peak **5.0–5.7 × blob** traced, RSS +8.8–8.9 × blob; restored state holds ≈ 1.8 × the memory of replayed state | 20k: +163 MB traced, RSS +288 MB; 50k: +487 MB traced, RSS +759 MB | ≈ 0.93 GB traced, ≈ 1.5 GB RSS spike (PROD restore succeeded in 15 s) | Proposal only (§6.4) |
| 4 | Repeated-run retention / leak | **None found** | 3 replays per process at 1k/10k/20k and 2 at 50k: the released runtime is collected and no runtime objects remain; product-attributed traced growth between runs ≈ 0 (§6.5); RSS after `malloc_trim` flat within allocator noise | n/a | none needed |

The synthetic evidence does **not** reproduce a multi-GB replay footprint. Replay memory is linear in history, not bounded, but its slope and transient size project to roughly 0.5–0.6 GB (well under 1 GB) of process memory at the PROD dry-run parameters, not to the 24 GB / 25.5 GB commit limit of the failing host. The `MemoryError` fired at the replay's single largest allocation site (a 32-row page of multi-MB row text, up to 64 rows alive at the page boundary). The most probable explanation is **host-wide commit exhaustion** (PROD 9016004 plus other applications plus this replay) at the moment of that allocation, not an unbounded structure in the replay itself. That is an inference: it is **not proven**, because no per-process commit breakdown of the host at the time of failure exists (§4.5 lists what to collect).

## 1. Environment

| Item | Value |
|---|---|
| Machine | Cloud sandbox VM, Linux 6.18.44 x86_64 (glibc 2.39) |
| CPU | Intel Xeon Processor @ 2.10 GHz, 4 vCPU |
| RAM | 15.7 GiB (16,877,547,520 B), no swap |
| Python | 3.13.12 (`uv sync --locked --extra api`, `.venv/bin/python`) |
| SQLite | 3.45.1 |
| psutil | 7.2.2 |
| Code | `a449f19` (+ the harness; the "after" rows in §4 add commit §6.1) |

Measurements: RSS/USS from psutil; peak RSS from `/proc/self/status` `VmHWM`, reset per window with `/proc/self/clear_refs` (plus a 20 ms sampler); Python allocations from `tracemalloc`; retained bytes per structure from a `gc.get_referents` deep-size walk; `malloc_trim(0)` after release separates retained objects from glibc heap slack. Linux RSS is not Windows commit charge: CPython allocates the same objects on both, but allocator overhead and what counts as "committed" differ. The harness also reports psutil's Windows `private` (commit) bytes so the same runs can be repeated on the Windows host.

## 2. Synthetic data

`synthetic_events()` in the harness: seeded (`20260926`) Gaussian random walk from 2000.0, step σ = 0.09, rounded to the config precision (0.1), one event every 3 s, same `NormalizedPriceEvent` shape as `tests.test_recovery_checkpoint.stream_events` (`bar`, `close`). Config: **exactly** `tests.test_recovery_checkpoint.runtime_config()` (no paper).

Justification: the test zig-zag stream creates a transition on most events (1.18 GB journal from 1,000 events). PROD sees ~1.5 transitions per 100 events. The synthetic stream reaches **94 fast transitions and 33 columns at 6,000 events, the same numbers as the PROD partial replay at 6,000 events**; pending Experiences are 16–26 (PROD: 47 at 6,000).

| Journal | Research rows | Research payload | Max research row | Other (Experience) rows / max row | File | Fast transitions | Experiences | Signals | Build time (live `ingest`) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1k | 1,000 | 14.2 MB | 19.6 KB | 1,081 / 18.8 KB | 17.7 MB | 17 | 19 | 0 | 4 s |
| 10k | 10,000 | 616.5 MB | 122.6 KB | 11,117 / 128.1 KB | 657.8 MB | 170 | 169 | 0 | 135 s |
| 20k | 20,000 | 2,726.9 MB | 285.1 KB | 22,587 / 238.5 KB | 2,839.3 MB | 325 | 371 | 20 | 553 s |
| 50k | 50,000 | 18,230.4 MB | 784.9 KB | 57,301 / 600.4 KB | 18,727.7 MB | 841 | 1,024 | 75 | 3,630 s |

What can and cannot be extrapolated: the per-event retained slope (dominated by one entry per event in fixed-shape structures) transfers directly. The transient terms scale with row size, and PROD rows are richer (3.2 MB at 67k vs 285 KB at 20k synthetic, ≈ 48 B vs ≈ 14 B of row growth per event), so they are extrapolated **by measured ratio to row size**, not by event count. Pending-Experience memory scales with (pending count × Experience snapshot size), both larger on PROD (47 × ~1.9 MB). Windows commit accounting, co-resident processes, real signal/pattern density and quote metadata cannot be reproduced here.

## 3. Phase A — root-cause map (code at `a449f19`)

### 3.1 Replay path, per journal row

`ResearchRuntime._rebuild` (`packages/nexora/research/runtime.py:71`) iterates `SQLiteJournal.iter_rows` (`packages/nexora/storage.py:85`) and per row runs `decode(NormalizedPriceEvent, row["event"])`, `ResearchPipeline.replay`, `self._events.append(event)` (`runtime.py:102`), `_paper_event`, `ExperienceService.observe(event, row["output"], …)`.

| Stage | File / function | Memory behaviour |
|---|---|---|
| Journal reader | `storage.py:85` `SQLiteJournal.iter_rows` | Pages of `LIMIT 32` rows via `fetchall()`. Each research row is the event **plus the full cumulative pipeline output** (O(history)). The page list stays alive while its rows are consumed, and the previous page is still bound while `fetchall()` builds the next one: up to **64 raw rows** alive at a page boundary. Measured window peak at a page boundary: **29.5–30.7 × row size** above the steady state (which already holds one page). This is the `fetchall()` in the dry-run traceback. |
| Row verification | `storage.py:146` `_verify` | `json.loads` of the whole row (decoded graph ≈ 2.8 × row bytes) + `json.dumps(sort_keys)` + SHA-256; peak ≈ 4.7 × row. The graph is kept by the replay loop until the next row is fetched. |
| Event decode | `runtime.py` `_rebuild` | Each event decoded from JSON gets its own `str` objects (`"synthetic"`, `"XAUUSD"`, `"USD/oz"`, …): ≈ 970 B/event retained in `_events` vs ≈ 540 B/event for generator-built events that share literals. |
| Engine replay | `research/pipeline.py:86` `replay` | Does not serialize output. Engine-only replay of 100k events takes 28 s; engines are not the cost. |
| Experience observe | `experience/service.py:57` `observe` | `recorded(output)`: a full canonical walk + JSON encoding of the **recorded row output** per event (≈ 2.4 × row, transient). `materialize` on a new Experience freezes a context proportional to history. |
| Checkpoint write | `runtime.py:179` `_write_checkpoint` → `checkpoint_state.encode_state` → `checkpoint.encode` → `CheckpointStore.save` | see §5 |
| Restore | `runtime.py:129` `_restore` → `CheckpointStore.load` → `checkpoint.verify` → `checkpoint_state.restore_state` | see §5 |
| DB/session | one `sqlite3` connection per journal for the process; default page cache (2 MB); no mmap; statements not retained | not a growth source (traced product growth between runs < 2 KB) |
| Queues/buffers | none in the replay path (no background queue; paper reads `journal.read(paper stream)` only on an active signal) | not a growth source |

### 3.2 Structures retained per event (historical events kept where only derived state is needed)

| Structure | File | Per-event? | Runtime consumers | Needs raw history? |
|---|---|---|---|---|
| `ResearchRuntime._events` (list of every `NormalizedPriceEvent`) | `research/runtime.py:84,102,278` | yes | `ingest` identity dedupe (linear scan, `runtime.py:259`), `expected_count=len(...)`, `events()` → `/backtest/runs` (`apps/api/nexora_api/main.py:375`, full list) and `observe_quote` (`apps/api/nexora_api/research.py:117`, last event + linear identity scan, plus a full tuple copy per poll), checkpoint (`events`, `event_count`, `last_event_key`) | Only backtests need the full list; dedupe needs a key lookup; everything else needs a count and the last event. Rin decision 5 (2026-09-23): do not change without approval. |
| `ResearchPipeline._seen` (key → canonical hash) | `research/pipeline.py:79,142` | yes | duplicate / identity-conflict check in `_process`; `restore_state` cross-check | key lookup only |
| `_MutableSymbolState.seen_identity_keys` × 3 runners | `pnf/engine.py:65-102` | yes | P&F duplicate skip; `restore_state` cross-check | key lookup only |
| `AdaptivePnfRunner._decisions` × 3 runners | `adaptive_box/runner.py:33` | yes (one per event per resolution) | **none at runtime**: only `snapshot()` / `decisions_for()` (tests) and the checkpoint `decisions` section | no (verified by grep: no product consumer) |
| `ExperienceService._seen` ((scope, key) → digest) | `experience/service.py:143` | yes | identity-conflict check on re-observation | key lookup only |
| `ExperienceService._states`, `_completed` | `experience/service.py` | per Experience, **never pruned after completion** | only pending entries are read by `observe`; checkpoint | completed entries: no runtime reader |
| `ExperienceService._pending`, `_samples`, `_derived.plans` | `experience/service.py` | bounded by the 60-min horizon, but each pending Experience's `context_json` grows with history | `observe` | derived only |
| Engine histories: P&F `columns`/`transitions` ×3, structure `_transitions`/`_pivots`/`_levels`, trendline `_history`/`_column_by_transition`/`_lows`/`_highs`, signals `_history`, pattern state, `pipeline._output` | engines | per transition (≈ 1.5–2 per 100 events) | the recorded output itself (cumulative `columns`/`transitions` are part of every output row) | derived; product output contract |

Measured per-structure retention (20k synthetic, deep size with shared objects attributed to the first structure listed; slope between 8k and 20k):

| Structure | Items at 20k | Bytes at 20k | Slope B/event |
|---|---:|---:|---:|
| `runtime._events` | 19,999 | 19,382,718 | 970.5 |
| `experience._pending` | 20 | 3,837,410 | 205.9 (grows with history, not count) |
| `experience._seen` | 19,999 | 3,809,856 | 185.6 |
| `pipeline._seen` | 19,999 | 2,515,047 | 122.3 |
| `pnf[fast/medium/slow].seen_identity_keys` | 19,999 each | 2,097,368 each | 131.1 each (set table; keys shared with `_events`) |
| `runner[fast/medium/slow]._decisions` | 19,999 each | ≈ 2,093,100 each | 104.8 each |
| `experience._samples` / `_last` / `_states` / `_completed` | 20 / 1 / 371 / 371 | 375,424 / 208,899 / 150,232 / 93,192 | 3.4 / 10.2 / 8.2 / 5.1 |
| all engine histories + `pipeline._output` together | — | < 0.35 MB | < 20 |
| **Total** | | **43,295,609** | **2,237.6** |

Engine-only growth (no journal, no Experience; `harness engine --events 100000`): retained 11.5 MB at 10k → 114.0 MB at 100k, linear at **1.14 KB/event** (events 540, `_seen` 143, P&F seen sets 3 × 42, decisions 3 × 104 B/event); RSS over start +224 MB at 100k. No plateau.

## 4. Replay Memory Finding

### 4.1 Retained growth

Replay retains **O(total events)**: every event adds one entry to `_events`, `pipeline._seen`, three P&F seen sets, three `_decisions` lists and `experience._seen` (≈ 2.0 KB/event together, deep size), for the life of the process. Experience adds O(experiences) entries that are never pruned after completion, and O(pending × history) through pending snapshot contexts. Engine histories grow per transition and are small. There is no plateau at any measured size (1k, 10k, 20k, 50k, engine-only 100k).

### 4.2 Transient peak per event

Measured with `replay --tracemalloc` (window = processing of one row + fetch of the next):

| Journal | Row at end | Steady window peak / row (mean of last 10%) | Worst window | Worst / row | Where |
|---|---:|---:|---:|---:|---|
| 1k (before) | 19.6 KB | 2.72 × | 586 KB (row 960) | 29.9 × | page fetch |
| 10k (before) | 122.6 KB | 2.78 × | 3,587,764 B (row 9,952) | 29.5 × | page fetch |
| 10k (after §6.1) | 122.6 KB | 2.92 × | 1,404,330 B (row 4,915) | 24 × on a 58 KB row; tail max **10.6 ×** | not a page fetch: that window retained +1.18 MB, consistent with a new Experience snapshot |
| 20k (after §6.1) | 285.1 KB | 3.0 × | 5,278,200 B (row 19,661) | 18.8 × | not a page fetch: that window retained +4.44 MB (new pending Experience) |

Single-row decomposition (last 10k row, 122,631 B): `_verify` keeps a 346 KB decoded graph (2.82 ×) with a 574 KB peak (4.68 ×); `recorded(output)` adds 274 KB (2.24 ×, peak 2.38 ×).

Reader-only micro-benchmark (80 rows of 256 KB, consumer keeps nothing): **before 17,056,840 B peak = 65.1 row-equivalents; after 3,216,640 B = 12.3 row-equivalents.**

### 4.3 Process memory per replay (checkpoints off, new runtime per run)

Commands: `replay --workdir W --repeat 3 --sample-every N --deep` (retained walk on) and `--repeat 1` without it. Baseline RSS ≈ 38–40 MB.

| Journal | Code | Run | Seconds | Peak RSS | RSS at end (runtime alive) | RSS after release | RSS after `malloc_trim` | Live runtime objects after release | Retained deep size |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|
| 1k | before | 1 / 2 / 3 | 2.39 / 2.31 / 2.26 | 45.4 / 45.5 / 45.5 MB | 45.4 / 45.5 / 45.5 MB | 44.5 / 44.9 / 45.5 MB | 43.43 / 43.44 / 43.46 MB | 0 | — |
| 10k | before (`--deep`) | 1 / 2 / 3 | 49.6 / 50.3 / 50.8 | 95.2 / 94.5 / 94.5 MB | 92.2 / 92.9 / 94.5 MB | 75.3 / 75.7 / 94.5 MB | 65.94 / 65.93 / 66.14 MB | 0 | 20,603,256 B |
| 10k | before | 1 | 70.9 | 74.1 MB | 71.9 MB | 72.5 MB | 59.2 MB | 0 | — |
| 20k | before (`--deep`) | 1 / 2 / 3 | 187.6 / 186.0 / 186.5 | 158.8 / 159.2 / 159.4 MB | 153.8 / 153.2 / 155.6 MB | 116.0 / 119.0 / 120.5 MB | 87.44 / 87.56 / 89.63 MB | 0 | 43,297,645 B |
| 20k | before (untraced; `checkpoint` command's replay step) | 1 | 221.5 | **111.8 MB** | 105.4 MB | — | — | — | — |
| 20k | **after §6.1** | 1 | 222.6 | **95.5 MB** | 95.5 MB | 77.1 MB | 67.1 MB | 0 | — |
| 50k | before (`--deep --digest`) | 1 / 2 | 1,312.5 / 1,139.5 | 299.7 / 309.1 MB | 241.4 / 250.7 MB | 240.7 / 255.0 MB | 137.3 / 144.2 MB | 0 / 0 | 103,946,846 B |
| 50k | before (untraced; `checkpoint` command's replay step) | 1 | 1,320.7 | **217.8 MB** | 181.5 MB | — | — | — | — |
| 50k | **after §6.1** (`--digest`) | 1 | 1,313.1 | **176.2 MB** | 173.5 MB | 152.9 MB | 92.7 MB | 0 | — |

`--deep` samples allocate a large id set for the walk and inflate peak/end RSS; compare before/after only between untraced, non-deep rows (bold). Runs overlapped with other harness jobs on 4 vCPU, so seconds are indicative only. The paging change costs no measurable time (20k 221.5 → 222.6 s, 50k 1,320.7 → 1,313.1 s).

Recovered state is byte-identical before and after §6.1 (`--digest`: SHA-256 of the explicit encoded checkpoint state, the ADR-022 byte-exact parity form, plus `state_hash(engine.snapshot())`):

| Journal | Before | After |
|---|---|---|
| 1k | `9c014dc9…7f34a47:a2cd7a10…b82503` | identical |
| 10k | `826551af…580547b9:d6658c0d…c8bfe34` | identical |
| 20k | `b4afa5f8…587edaf7:6419aad5…a829fb33` | identical |
| 50k | `01e038ce…75346b22:a36d3b1e…5dfa68d6` (both before runs) | identical |

### 4.4 Extrapolation to the PROD dry-run (67,248 events, last row ≈ 3.2 MB, 47 pending Experiences, Experience snapshot rows ≈ 1.9 MB)

| Term | Basis | Before §6.1 | After §6.1 |
|---|---|---:|---:|
| O(events) structures | 1.99 KB/event × 67,248 (slope table above, excluding pending) | ≈ 134 MB | ≈ 134 MB |
| Pending Experiences + `_last` | 48 × ≈ 1.9 MB `context_json` | ≈ 91 MB | ≈ 91 MB |
| Engine histories | bounded by one output (≤ row size × ~3) | ≤ 10 MB | ≤ 10 MB |
| Reader page text | 65 / 12.3 row-equivalents × 3.2 MB | ≈ 208 MB | ≈ 39 MB |
| Verify + `recorded` + new Experience snapshot | worst non-page window 18.8 × row (§4.2) × 3.2 MB, conservatively added to the page term | ≈ 60 MB | ≈ 60 MB |
| **Python-level peak** | | **≈ 0.50 GB** | **≈ 0.33 GB** |
| RSS / commit | measured untraced peak-RSS-above-baseline ÷ (retained + transient) ≈ 1.1–1.2 at 20k/50k | **≈ 0.58 GB** | **≈ 0.38 GB** |

Consistency checks against real data: the dry-run's partial replay measured 0.047 GB commit at 5,000 events and 0.052 GB at 6,000; the synthetic replay measures 55–62 MB RSS at 4,000–6,000 events. The checkpoint composition extrapolates to ≈ 190 MB against the real 164 MB (§5.1).

Cannot be extrapolated: Windows commit and heap behaviour; co-resident process usage; PROD event shape (`quote` with bid/ask/metadata), signal and pattern density; whether the real journal holds rows much larger than 3.2 MB. The projection assumes the reported 3.2 MB is the maximum row size.

### 4.5 What explains the `MemoryError`, and how to confirm it

- The replay fails inside `fetchall()` because that is where it makes its largest single allocations: 32 rows of up to 3.2 MB text (≈ 100 MB), while the previous page (another ≈ 100 MB) is still alive. That site is the first to fail whenever system commit runs out, whatever consumed it.
- A second, new process ran the replay while PROD (9016004, no checkpoint support, its own full in-memory state) and other applications were running on a 24 GB / 25.5 GB-commit host. The synthetic projection for the replay process itself is ≈ 0.5–0.6 GB (under 1 GB even with generous allowance for Windows heap overhead). It cannot account for 20+ GB.
- Not proven. To confirm on the Windows host (read-only, no PROD writes): log every minute during a replay `\Memory\Committed Bytes`, `\Memory\Commit Limit`, and per process `Private Bytes` (`Get-Process | Select Name,Id,PrivateMemorySize64`), including the PROD process; and run `scripts/replay_memory_harness.py build/replay` on a synthetic 50k journal on that host to measure Windows `private` bytes for the same workload (the harness reports them).

## 5. Checkpoint and Restore Memory Findings (Phase C)

Command: `checkpoint --workdir W` (full replay untraced, then each step traced; `rss_window_peak` from `VmHWM`).

### 5.1 Composition (serialized bytes per section)

| Section | 1k | 10k | 20k | 50k | B/event (20k) |
|---|---:|---:|---:|---:|---:|
| **blob** | 2,199,803 | 17,106,267 | 32,879,000 | 85,469,930 | 1,644 |
| `events` | 501,157 | 5,051,545 | 10,147,869 | 25,436,685 | 507 |
| `pipeline.matrix` (of which `decisions` × 3) | 582,311 (535,361) | 5,888,774 (5,413,367) | 11,872,084 (10,893,367) | 29,849,839 (27,333,367) | 594 (545) |
| `pipeline.seen` | 80,894 | 818,895 | 1,648,895 | 4,138,895 | 82 |
| P&F `seen_identity_keys` × 3 | 35,682 | 386,685 | 806,685 | 2,066,685 | 40 |
| `experience.pending` | 235,421 | 2,758,713 | 4,623,696 | 15,424,270 | (20 × 231 KB; 50k: 26 × 593 KB) |
| `experience.seen` | 149,894 | 1,508,895 | 3,028,895 | 7,588,895 | 151 |
| `experience.sample_rows` / `samples` | 551,616 / 44,985 | 636,164 / 54,607 | 656,396 / 52,065 | 655,326 / 72,320 | bounded |
| `pipeline.output` + engines + other experience | ≈ 53 KB | ≈ 390 KB | ≈ 900 KB | ≈ 2.3 MB | small |

About 80% of the blob is one entry per historical event (events, per-runner decisions, seen maps). Projection to 67,248 events: O(events) sections ≈ 1.33 KB × 67,248 ≈ 89 MB, plus 47 pending snapshots of ≈ 2.1 MB after JSON escaping ≈ 99 MB, so ≈ 190 MB vs the real **164 MB** (within ~15%).

### 5.2 Checkpoint Memory Finding (write)

| Step | 20k (blob 32,879,000 B): seconds / traced peak over start / traced retained / RSS start → peak | 50k (blob 85,469,930 B): same |
|---|---|---|
| `encode_state` (JSON-ready copy of all state) | 1.66 s / 49,336,141 (1.50 × blob) / 49,261,573 / 105.5 → 181.5 MB | 3.40 s / 121,267,060 (1.42 ×) / 121,192,580 / 182.0 → 403.7 MB |
| `json.dumps` alone (`str`) | 2.59 s / 38,025,351 / 32,883,516 (1 B/char) / 189.8 → 226.8 MB | 6.92 s / 278,077,334 / **170,978,285 (2 B/char)** / 421.0 → 660.7 MB |
| `checkpoint.encode` (`json.dumps` → `str`, `.encode()` → `bytes`, SHA-256) | 3.02 s / 65,763,370 (**2.0 ×**: `str` + `bytes`) / −16,324,918 / 194.0 → 257.1 MB | 7.17 s / 427,388,091 (**5.0 ×**) / −35,668,273 / 421.0 → 677.0 MB |
| `state_hash(engine.snapshot())` | 0.20 s / 1,543,937 / 319,543 / flat | 0.42 s / 4,007,781 / 671,373 / flat |
| `CheckpointStore.save` (header line + blob, fsync, rename) | 0.21 s / 47,240 (no further copy) / 2,921 / flat | 0.39 s / 69,022 / 3,041 / flat |
| **`runtime._write_checkpoint` whole** | 4.89 s / **114,992,489 (3.5 × blob)** / 324,342 / 193.4 → 280.2 MB (+87 MB) | 11.32 s / **548,518,022 (6.4 × blob)** / 672,373 / 404.3 → 776.9 MB (**+373 MB**) |

10k: blob 17.1 MB, whole write traced peak 59,522,387 (3.5 ×), RSS +25 MB. 1k: 8,185,362 (3.7 ×).

Root cause: `ckpt.encode(checkpoint_state.encode_state(...))` builds a complete JSON-ready copy of the state (≈ 1.4–1.5 × blob), then `json.dumps` builds a list of chunks and joins them into one complete `str`, then `.encode("utf-8")` builds complete `bytes`; the copy lives until `encode` returns, so copy + chunks/`str` + `bytes` coexist. Serialization **does** create further full buffers. It gets worse with content: `ensure_ascii=False` keeps non-ASCII text, and a single character above U+00FF anywhere in the state makes CPython store the **whole** `str` at 2 bytes per character. At 50k the only such characters are two `→` in a signal evidence reason (`"P&F reversal X→O confirmed."`), which doubled the text to 171 MB for an 85 MB blob and lifted the write peak from 3.5 × to 6.4 × blob. PROD has signals, so this case must be assumed: 164 MB × 6.4 ≈ **1.05 GB traced** above live state (≈ 0.57 GB if all text were Latin-1), taken under the runtime lock every 100 live events and at shutdown.

Can write be streamed without changing semantics? **Yes, without a format change.** `json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).iterencode(state)` yields exactly the same text in chunks; each chunk can be UTF-8 encoded into a SHA-256 hasher and a temporary file, which removes the chunk list, `str` and `bytes` (2.0 × blob at 20k, 5.0 × at 50k where the text is 2 bytes/char). Because the header line (with `blob_hash`/`blob_size`) precedes the blob in the file, a streamed writer must write the blob to a temporary file first and then write `header + blob` to the final temporary file (one extra sequential copy on disk), or reserve the header; putting the header last would be a **format change** and is not proposed. Removing the JSON-ready copy as well requires a streaming encoder over live objects (a larger rewrite of `checkpoint_state`'s encoder; same output bytes).

### 5.3 Restore Memory Finding

| Step | 20k (blob 32.9 MB): seconds / traced peak over start / traced retained / RSS start → peak | 50k (blob 85.5 MB): same |
|---|---|---|
| `CheckpointStore.load` (`read_bytes` + `data[end+1:]` slice copy) | 0.04 s / 65,767,978 (2.0 × blob) / 32,888,013 / 72.8 → 138.6 MB | 0.11 s / 170,949,839 (2.0 ×) / 85,478,943 / 79.7 → 250.4 MB |
| `verify` (SHA-256, `blob.decode()` → `str`, `json.loads` graph) | 1.53 s / 124,574,539 (3.8 ×) / 58,810,171 (graph 1.8 ×) / 138.6 → 339.0 MB | 3.78 s / 401,174,402 (4.7 ×) / 144,723,402 (1.7 ×) / 165.3 → 843.9 MB |
| `restore_state` (typed objects built while the JSON graph is alive) | 9.95 s / 38,524,256 / −14,785,001 / 339.0 → 368.0 MB | 24.09 s / 83,841,532 / −43,384,505 / 587.9 → 726.9 MB |
| `state_hash(restored.snapshot())` | 0.20 s / 1,545,322 / 320,195 / flat | 0.47 s / 3,940,555 / 638,151 / flat |
| **`ResearchRuntime(..., checkpoints=store)` whole** | 12.02 s / **163,179,013 (5.0 × blob)** / 77,246,042 / 81.7 → 369.4 MB (**+288 MB = 8.8 × blob**) | 29.36 s / **486,689,755 (5.7 × blob)** / 187,483,484 / 85.7 → 844.6 MB (**+759 MB = 8.9 × blob**) |

10k: whole restore 81,811,431 traced (4.8 ×), RSS 73.7 → 229.0 MB.

Root causes: (a) `load` holds the whole file and a sliced copy of the blob; (b) `verify` decodes the bytes to a `str` and parses a full JSON graph while the bytes are still referenced by `_restore`'s locals (`loaded`, `blob`) — they stay alive through `restore_state`; (c) `restore_state` builds all typed objects before the JSON graph is released; (d) `blob.decode()` produces a 2-byte-per-character `str` whenever the state holds a character above U+00FF (the 50k case); (e) the restored state is **larger than replayed state** (traced retained 77.2 MB vs 43.3 MB deep size after a replay at 20k; 187.5 MB vs 103.9 MB at 50k) because every decoded string is a separate object: identity keys are duplicated across `events`, `pipeline._seen`, three P&F seen sets and `experience._seen`, whereas a replay shares one string per event. PROD projection: 164 MB × 5.7 ≈ 0.93 GB traced, ≈ 1.5 GB RSS spike (the dry-run restore succeeded in 15 s, so it fitted on that host at that time), and the restored runtime then holds ≈ 1.8 × the memory of a replayed one.

Can restore be streamed without changing semantics? **Partly, without a format change**: read the header line, then read the blob straight into one `bytes` (no slice copy); drop `blob`/`loaded` before `restore_state`; pop and release each JSON section as it is restored; intern identity keys on restore so restored state shares strings like replayed state. A fully incremental JSON parser is not in the standard library; streaming beyond section granularity would need a dependency or a format change (e.g. sectioned/length-prefixed blobs), which is a decision.

### 5.4 Checkpoint Minimal-State Feasibility

**Question: Can a NEXORA checkpoint contain only the minimal derived state required to resume, while the journal remains the authoritative historical event source?**

**Answer: YES — with a checkpoint state/schema version bump and an architecture decision; not without them.** Nothing in P&F, structure, trendline, pattern, regime, signal or Experience *computation* needs raw historical events after restore: their state is already derived (O(transitions) or bounded). The historical events and per-event maps in the checkpoint exist for the recovery behaviours below, each of which has a journal-backed replacement:

| Behaviour that uses per-event history today | Current source | Journal-backed replacement (semantics preserved) |
|---|---|---|
| Duplicate / identity-conflict detection for re-delivered events (`ingest` scan, `pipeline._seen`, P&F seen sets) | `_events`, `_seen`, `seen_identity_keys` | Keyed lookup on the existing `UNIQUE(stream, event_key)` index; on a hit, compare `canonical_hash` of the stored row's `event` (read one row only on a duplicate). The pure pipeline must stay persistence-free, so the check moves to the runtime boundary (which already dedupes before `engine.process`); standalone pipeline users (backtest) keep an in-memory set. |
| Experience re-observation conflict detection | `experience._seen` | Needs design: the digest covers the recorded output, while the `experience:v1:{scope}:observations` row stores only event/completeness/metadata. Either store the digest in the observation row (a stream-content change) or derive it from the research row (read on a hit only). |
| `events()` consumers: `/backtest/runs` (full list), `observe_quote` (last event, identity membership) | `_events` | Backtest streams events from the journal (`iter_rows`, event field only); `observe_quote` needs the last event plus a keyed lookup. |
| Restore cross-checks (`list(pipeline._seen) == keys`, P&F seen == keys, `len(events) == header.event_count`, last key) | persisted `events` | Already anchored independently by `verify`: `row_identity(last_sequence)` and `count_through(last_sequence) == event_count`. Keep `last` event + count in the state. |
| `AdaptivePnfRunner._decisions` | persisted `decisions` | No runtime consumer; drop from the checkpoint or bound it (changes `AdaptivePnfSnapshot.decisions`, a contract used by `from_snapshot` tests). |

Implications: (1) `STATE_VERSION` 3 and `SCHEMA_VERSION` 3; every existing checkpoint is rejected (`schema_version_mismatch`) and costs one full replay — acceptable under ADR-022 (checkpoints are disposable) but it must be planned with the PROD availability gap (§7). (2) ADR-022/ADR-029 invariant `STATE(full replay) == STATE(checkpoint + delta)` must be restated: the event list leaves "memory state" and is compared against the journal. (3) Rin decision 5 (2026-09-23) covers `_events`; changing it needs explicit approval. (4) Consumers to evaluate: API backtest and quote observation, `recovery_status`/readiness fields (`events`), tests that assert `events()` after restore, paper session (unaffected: it rebuilds from its own stream). (5) Checkpoint size would drop to ≈ pending snapshots + engine state (≈ 100 MB at PROD, dominated by pending contexts, which in turn shrink only with Research Journal Payload V2 / ADR-027). (6) The in-memory O(events) retention during a **full replay** is removed only if the runtime structures themselves move to journal lookups — the checkpoint change alone does not bound replay memory.

## 6. Proposed Minimal Fix

Four findings, four separate root causes; they are not assumed to share one.

### 6.1 Finding 1 (transient) — implemented: bounded, draining journal read pages

Commit `fix: bound journal read pages during replay` (separate commit). `packages/nexora/storage.py`: `iter_rows` (SQLite) and `iter_read` (Postgres) fetch `_PAGE_ROWS = 8` rows per query instead of 32, and `_drain` pops each row from the page as it is verified and deletes the raw payload before yielding, so no raw text of an earlier page is alive when the next page is fetched.

- Root cause proven: measured page-boundary window = 29.5–30.7 × row size; micro-benchmark 65.1 → 12.3 row-equivalents; 10k tail max 29.7 × → 10.6 ×; untraced replay peak RSS 20k 111.8 → 95.5 MB (−15%), 50k 217.8 → 176.2 MB (−19%), growing with row size (≈ −170 MB projected at 3.2 MB rows).
- No decision needed: same rows, same order, same fixed high-water mark (`end`), same verification (`_verify`), same `after` resume semantics; no format/contract change. Cost: 4 × more (rowid-range) queries — 8.4k instead of 2.1k at 67k events, negligible against hours of replay.
- Proof of equivalence: byte-exact recovered-state digests identical before/after (§4.3); full suite passes; new tests `tests/test_journal_read_memory.py` (order/resume/fixed boundary across pages, and a memory bound that fails on the old reader: 40 rows of 256 KB, peak must stay below (8 + 8) × row).
- Side effect: `code_fingerprint()` covers package code, so like any code change it invalidates existing checkpoints (one full replay on first start).
- Limitation: this removes the largest **transient** term only; it does not bound retained O(events) growth, and it cannot prevent a `MemoryError` when the host is out of commit.

### 6.2 Finding 1 (retained) — proposal, needs architecture/compatibility decision

Retained growth is by design of the current contracts (§3.2). Options, cheapest first; each needs Rin (and Architect for 2–4) approval:

1. Intern identity keys and constant strings on event decode (runtime-local; no contract change; ≈ 30–40% of `_events`' 970 B/event). Behaviour-neutral, but touches `_events` handling (Rin decision 5).
2. Stop retaining `AdaptivePnfRunner._decisions` (no runtime consumer) — changes `AdaptivePnfSnapshot` and the checkpoint `decisions` section: contract + `STATE_VERSION` decision. Saves ≈ 315 B/event.
3. Prune `ExperienceService._states`/`_completed` of closed Experiences — Experience-owned memory; changes checkpoint content; needs Experience owner + ADR-031/022 review.
4. Journal-backed identity lookups replacing `_events`/`_seen`/seen sets (§5.4) — architecture decision; bounds replay retention to O(transitions + pending).
5. Research Journal Payload V2 / compaction (ADR-027, in flight): rows stop carrying cumulative output, which removes the O(row) transient, most of the 4.5 h replay time and the O(n²) journal growth. This is the root of rows being MBs.

### 6.3 Finding 2 — checkpoint write spike (proposal, no format change)

Stream `encode` via `iterencode` into a hasher + temporary blob file, then compose `header + blob` into the final temporary file and rename (same bytes on disk). Removes the chunk list, the `str` and the `bytes` (3.6 × blob at 50k, ≈ 0.6 GB at PROD's 164 MB when the text is 2 bytes/char). A streaming state encoder that avoids the JSON-ready copy removes another 1.5 × blob. Touches shared checkpoint persistence; needs review. Not implemented because it is not the Gate 2 failure site and the change to `CheckpointStore.save`/`encode` signatures deserves its own review.

### 6.4 Finding 3 — restore spike (proposal, no format change)

In order of size: release `loaded`/`blob` in `_restore` before `restore_state` (−1 × blob); read the blob without the slice copy (−1 × blob transient); release JSON sections as restored; intern identity keys on restore (restored retained ≈ 77 MB → ≈ 43 MB at 20k). All local to `checkpoint.py`/`checkpoint_state.py`/`runtime._restore`; needs review.

### 6.5 Finding 4 — repeated-run retention

No action. Evidence (every replay in one process, checkpoints off, new runtime per run):

| Journal / mode | Runs | Released runtime collected, runtime objects left | Product-code traced growth between runs (`tracemalloc`, `packages/*` only) | RSS after `malloc_trim`, run by run |
|---|---:|---|---|---|
| 1k, `--tracemalloc` | 3 | yes, 0 | < 1 KB per pair (SQLite statement cache, `artifacts.py` caches; ±) | 47.68 / 47.93 / 48.07 MB (untraced 43.43 / 43.44 / 43.46 MB) |
| 10k, `--tracemalloc` | 2 | yes, 0 | < 2 KB | 86.8 / 90.9 MB |
| 10k, `--deep` | 3 | yes, 0 | — | 65.94 / 65.93 / 66.14 MB |
| 20k, `--deep` | 3 | yes, 0 | — | 87.44 / 87.56 / 89.63 MB |
| 20k, `--tracemalloc` (after §6.1) | 2 | yes, 0 | **4,596 B** (`storage.py` append statements) | 131.6 / 139.0 MB |
| 50k, `--deep --digest` | 2 | yes, 0 | — | 137.3 / 144.2 MB |

No product object survives a released runtime, and product-attributed allocations do not grow between runs. The first run leaves 25–100 MB of glibc/pymalloc high-water that later runs mostly reuse; the remaining 0–7 MB RSS drift per run is not attributable to any traced Python allocation (allocator fragmentation). The dry-run failure was a single replay in a new process, so this finding does not explain it. A regression test (`test_repeated_full_replays_release_the_previous_runtime`) holds weak references across three replays.

## 7. PROD Startup Recovery / Availability Gap

Documented separately; **not fixed in this branch**.

- PROD runs older code `9016004`, which has **no checkpoint support**. Every PROD restart performs a complete replay of the ~80 GB journal before `ResearchRuntime.__init__` returns, and the API (port 8100) only listens after FastAPI `lifespan` completes. Measured cold replay with the newer candidate code: 4 h 28 min; `9016004` lacks the ADR-031 Experience optimizations and is likely slower.
- During that time the API and the feed are unavailable: no observation, no dashboard, no quote ingest.
- After a machine reboot PROD **did not auto-restart**: there is no supervised service or restart policy, so the outage lasts until an operator notices and restarts it, then the multi-hour replay begins.
- The memory findings above add risk to the same window: a full replay on a busy host may fail late (as the dry-run did at ~49k/67k), restarting the multi-hour clock.

Recommended future remediation (each needs its own decision/task):

1. **Planned cutover to checkpoint-capable code** in a maintenance window. The first start on new code is a full replay (unavoidable: `code_fingerprint`). Budget ≥ 5 h plus margin, keep the host otherwise idle (close co-resident applications; PROD itself is the process being replaced), and monitor commit. A pre-built checkpoint produced from a read-only journal copy with the exact release build could shorten this to a delta replay, but that needs explicit approval under the data-safety rules (AGENTS.md §8.2) and an environment/anchor procedure.
2. **Supervised service with automatic restart on boot and on crash** (Windows service or Task Scheduler "at startup" + restart-on-failure), with a monitor/alert on port 8100.
3. **Decouple API availability from recovery**: bind the port early and report `recovering` in readiness while research endpoints return 503; the feed can buffer or pause. Architecture decision (lifespan contract).
4. **Checkpoint on graceful shutdown and periodically** (already in candidate code, ADR-022/029) so ordinary restarts restore in seconds.
5. **Research Journal Payload V2 / compaction** (ADR-027) to remove the O(n²) journal growth that makes any full replay take hours.

## 8. Files Proposed to Change

Changed in this branch:

| File | Commit | Purpose |
|---|---|---|
| `scripts/replay_memory_harness.py` | test commit | diagnostic harness (never imported by product code) |
| `tests/test_replay_memory.py` | test commit | stream sparsity/determinism, repeated-replay release, retained-slope bound, harness smoke |
| `packages/nexora/storage.py` | fix commit | §6.1 bounded draining read pages |
| `tests/test_journal_read_memory.py` | fix commit | §6.1 order/resume/boundary + memory bound |
| `docs/investigations/REPLAY-MEM-1-memory-bounded-replay.md` | docs commit | this report |

Proposed, not changed (need decisions): `packages/nexora/research/checkpoint.py` (`encode`, `CheckpointStore.save`/`load`: streaming write, slice-free read); `packages/nexora/research/runtime.py` (`_restore` release order; identity interning; journal-backed dedupe; `events()`); `packages/nexora/research/checkpoint_state.py` (streaming encoder, section release, interning, `STATE_VERSION` 3 minimal state); `packages/nexora/research/pipeline.py`, `packages/nexora/pnf/engine.py` (seen maps); `packages/nexora/adaptive_box/runner.py` (`_decisions`); `packages/nexora/experience/service.py` (`_seen`, pruning closed Experiences); `apps/api/nexora_api/main.py` (`/backtest/runs` event source), `apps/api/nexora_api/research.py` (`observe_quote` dedupe); ADR-022/029/031 amendments.

## 9. Risks / Compatibility Concerns

- **§6.1 fix**: storage is shared persistence. Risk is low (pure paging change, byte-identical results, Postgres path changed identically but its tests need a DSN and were skipped here). Any package change invalidates checkpoints (one full replay on first start) — relevant to PROD cutover planning.
- **Evidence limits**: Linux RSS ≠ Windows commit; synthetic rows are ≈ 3–4 × smaller than PROD at equal history; the synthetic stream produced few signals (75 by 50k) and no paper session. The Windows `MemoryError` root cause is inferred, not proven (§4.5).
- **Minimal-state checkpoint (§5.4)**: needs STATE/SCHEMA version bump (all checkpoints invalid), invariant restatement, Rin decision 5 on `_events`, consumer review (API backtest, quote observation, readiness fields), and Experience identity design. Doing it without PROD cutover planning would add another full replay to PROD.
- **Dropping `_decisions`**: changes a snapshot contract; any external tool relying on `AdaptivePnfSnapshot.decisions` breaks.
- **Streaming write/restore**: must keep byte-identical blobs and the header-first layout; otherwise it is a checkpoint format change. Until then, any character above U+00FF in checkpointed text (signal evidence already contains `→`) doubles the in-memory JSON text during write and restore.
- **Operational**: the PROD availability gap (§7) remains until a supervised, checkpoint-capable PROD is deployed; a full replay on a loaded host can still fail from host commit exhaustion regardless of this branch.

## 10. Commands and results

All harness commands run from the repository root with `W=<new scratch directory>` (never a runtime directory); default guard `--abort-rss-gb 3.5 --min-available-gb 1.0` (never triggered; the largest process peaked well below 1 GB).

```text
# journals (live ingest path)
.venv/bin/python scripts/replay_memory_harness.py build --workdir $W/w1000  --events 1000
.venv/bin/python scripts/replay_memory_harness.py build --workdir $W/w10000 --events 10000
.venv/bin/python scripts/replay_memory_harness.py build --workdir $W/w20000 --events 20000
.venv/bin/python scripts/replay_memory_harness.py build --workdir $W/w50000 --events 50000
# replay memory (before/after), repeated runs, retained structures, transients, parity digests
.venv/bin/python scripts/replay_memory_harness.py replay --workdir $W/w1000  --repeat 3 --sample-every 250 --deep --tracemalloc
.venv/bin/python scripts/replay_memory_harness.py replay --workdir $W/w1000  --repeat 3 --sample-every 250
.venv/bin/python scripts/replay_memory_harness.py replay --workdir $W/w10000 --repeat 3 --sample-every 2000 --deep
.venv/bin/python scripts/replay_memory_harness.py replay --workdir $W/w10000 --repeat 2 --sample-every 5000 --tracemalloc
.venv/bin/python scripts/replay_memory_harness.py replay --workdir $W/w20000 --repeat 3 --sample-every 4000 --deep
.venv/bin/python scripts/replay_memory_harness.py replay --workdir $W/w50000 --repeat 2 --sample-every 10000 --deep --digest
.venv/bin/python scripts/replay_memory_harness.py replay --workdir $W/wN --digest            # N = 1k/10k/20k/50k, before and after §6.1
# checkpoint write/restore decomposition
.venv/bin/python scripts/replay_memory_harness.py checkpoint --workdir $W/wN                 # N = 1k/10k/20k/50k
# engine-only retained growth
.venv/bin/python scripts/replay_memory_harness.py engine --events 100000 --sample-every 10000
```

"After §6.1" runs used a scratch worktree of commit `fix: bound journal read pages during replay` with `PYTHONPATH=<worktree>/packages:<worktree>/apps/api` and the same journals, so before and after read identical bytes.

Validation of the branch head (§11 lists exact results):

```text
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/mypy
git diff --check a449f19..HEAD
.venv/bin/python -m pytest -q tests/test_replay_memory.py tests/test_journal_read_memory.py tests/test_startup_recovery.py tests/test_recovery_checkpoint.py
```

## 11. Handoff

```text
ROLE: DEV-PERF
OWNER SESSION: Claude Code cloud session (scheduled routine) session_015NuBe8SdkB8dHnAgLHFrtn
WORKSTREAM: REPLAY-MEM-1
WORKTREE: cloud sandbox (/home/user/NEXORA; isolated container, synthetic data only)
BRANCH: claude/replay-memory-bound-v1
BASE SHA: a449f19 (a449f1980bdd70b19c74656f5a2b70cb8ee4b99d = origin/main at start and at final fetch)

STATUS: READY FOR REVIEW (investigation + one minimal mitigation; architecture items BLOCKED on decisions)

COMMITS:
- 621286e test: add synthetic replay memory harness and regression tests
- daa065e fix: bound journal read pages during replay
- (this commit) docs: add REPLAY-MEM-1 replay memory investigation
CHANGED FILES:
- scripts/replay_memory_harness.py (new, diagnostic only)
- tests/test_replay_memory.py (new)
- packages/nexora/storage.py (SQLiteJournal.iter_rows, PostgresJournal.iter_read, new _drain, _PAGE_ROWS)
- tests/test_journal_read_memory.py (new)
- docs/investigations/REPLAY-MEM-1-memory-bounded-replay.md (new)
TESTS:
- .venv/bin/python -m pytest -q
  pass — 787 passed, 2 skipped (PostgreSQL tests: isolated PostgreSQL test service not configured), 228.8 s
- .venv/bin/python -m pytest -q tests/test_replay_memory.py tests/test_journal_read_memory.py tests/test_startup_recovery.py tests/test_recovery_checkpoint.py
  pass — 72 passed
- .venv/bin/ruff check .
  pass — All checks passed
- .venv/bin/mypy
  pass — no issues found in 160 source files
- git diff --check a449f19..HEAD
  pass — no output
- .venv/bin/ruff format --check .
  not gating — 24 pre-existing files would be reformatted (known debt, untouched); all files changed here are formatted
- PostgresJournal.iter_read paging change
  not_run — no isolated PostgreSQL service in the sandbox; same logic as the SQLite path, covered by tests/test_postgres_journal.py when a DSN is configured
- Synthetic memory runs (1k/10k/20k/50k journals, engine-only 100k): see sections 4-5 and 10
  pass — before/after byte-exact recovered-state digests identical at 1k, 10k, 20k, 50k
ACCEPTANCE CRITERIA:
- PASS Phase A root-cause map with files/functions and retained-history analysis (section 3)
- PASS Phase B harness with configurable counts, process + tracemalloc + deep-size measurement, 3 repeated replays per process, abort guard (sections 2, 4)
- PASS Phase C checkpoint composition, write/restore step peaks, streaming analysis, minimal-state answer (section 5)
- PASS Phase D four separate findings and proposals (section 6)
- PASS PROD Startup Recovery / Availability Gap documented, not fixed (section 7)
- PASS Minimal fix only where root cause proven and no decision needed, in its own commit with behaviour and state-equivalence evidence (section 6.1)
- NOT CLAIMED Replay memory is still O(total events); bounding it needs the decisions in 6.2/5.4
DEPENDENCIES:
- ADR-022 / ADR-029 (checkpoint), ADR-031 (Experience replay), ADR-027 Research Journal Payload V2 (in flight, not on main)
BLOCKERS:
- Retained O(events) growth (6.2), minimal-state checkpoint (5.4): need Rin/Architect decisions incl. Rin decision 5 on _events; unblock = approved ADR amendment
- Checkpoint write/restore streaming (6.3/6.4): no format change, but shared checkpoint persistence; unblock = Rin approval to implement
- Windows MemoryError root cause: inferred (host commit exhaustion), not proven; unblock = Windows commit/process data (4.5)
ARCHITECTURE NOTES:
- No contract, journal-format, checkpoint-format or trading/Pattern/Entry Readiness/Signal/Experience semantic change
- Proposals needing decisions: sections 5.4 and 6.2-6.4
RUNTIME / DATA IMPACT:
- None on any runtime: synthetic scratch data only; no PROD/DEV journal, checkpoint or port touched
- Deploying daa065e changes code_fingerprint (like any code change): existing checkpoints are rejected once and a full replay runs on first start
PR:
- NOT CREATED
MERGE:
NOT PERFORMED

NEXT RECOMMENDED ACTION:
Rin reviews this report and commit daa065e; decide on (a) implementing the no-format-change checkpoint write/restore streaming (6.3/6.4), (b) the retained-growth / minimal-state architecture track (5.4/6.2), and (c) the PROD cutover and supervision plan (section 7). Collect Windows commit data during the next PROD-copy replay to confirm 4.5.
```
