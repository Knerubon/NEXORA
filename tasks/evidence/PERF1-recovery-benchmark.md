# PERF-1 recovery benchmark: Phase 2A baseline

Written for: Rin (architecture review) and the QA/code reviewers of PERF-1. It records the evidence only; no performance target is set or implied ([ADR-029](../../docs/decisions/ADR-029-recovery-checkpoint-v1-hardening.md) §13, §19).

Raw results: [PERF1-recovery-benchmark.json](PERF1-recovery-benchmark.json). This is the script's own output, unedited.

## Provenance

| Item | Value |
|---|---|
| Code | `3c571ca`: the benchmark commit on `claude/recovery-checkpoint-v1` as it stood **before** the integration rebase onto `f5bbdfa`, based on `4f69e9c`. The tree was clean (`worktree_dirty: false`). |
| Command | `.venv/Scripts/python scripts/recovery_benchmark.py --workdir <scratch>/bench-v1 --sizes 500 1000 2000 5000 --runs 3 --warmup 1 --output <scratch>/bench-v1.json` |
| Started | 2026-09-25T04:07:41Z; finished about 12:33 +07 (about 4.4 h) |
| Machine | Windows 11 (10.0.26200), Intel64 Family 6 Model 142, Python 3.13.3. Development workstation, not otherwise loaded by this session during the run. |
| Data | Only seeded synthetic events (seed `20260925`, step up to ±0.3, one-minute bars), written to a new scratch directory. **No PROD, TSID or legacy `data/research.sqlite` data was read.** |
| Config | `docs/examples/research-config.json` (three fixed-box resolutions; `paper: null`, so paper replay is ~0 ms) |
| Method | Per N: build the journal by live ingest, saving checkpoints at N−100, N−10, N−1 and N. Then run 1 warm-up and 3 measured rounds, round-robin over every scenario. The statistic is the **median**. Timer: `time.perf_counter`. Components are timed by wrapping the existing methods in-process. |
| Correctness guards | In every scenario and run, the final state hash equals the full-replay state hash, and the journal row count is unchanged (`journal_rows_changed: 0`). The script fails if either check breaks. |

## Scenario definitions

| Scenario | Checkpoint store | What it represents |
|---|---|---|
| `full_replay` | none (checkpoints off) | pre-ADR-022 behavior and `NEXORA_RESEARCH_CHECKPOINTS=off` |
| `no_checkpoint` | empty | first start: full replay, then a checkpoint write |
| `warm_checkpoint` | checkpoint at N | graceful restart (H2 target) |
| `checkpoint_plus_k` | checkpoint at N−k | restart after k uncovered events (crash, hard kill) |
| `fingerprint_mismatch` | checkpoint at N with a foreign `code_fingerprint` | first start after any code change (G1) |

"Recovery ms" is `last_recovery.duration_ms`: checkpoint restore plus replay. It excludes the checkpoint written afterwards, which is reported separately.

## Results (median of 3)

| N | scenario | replayed | recovery ms | `ExperienceService.observe` ms (share) | engine replay ms | restore ms | checkpoint write ms |
|---|---|---|---|---|---|---|---|
| 500 | full_replay | 500 | 5,198 | 4,104 (79%) | 220 | 0 | 0 |
| 500 | no_checkpoint | 500 | 5,253 | 4,137 (79%) | 225 | 0 | 66 |
| 500 | warm_checkpoint | 0 | 176 | 0 | 0 | 176 | 0 |
| 500 | checkpoint_plus_1 | 1 | 140 | 11 (8%) | 0 | 126 | 58 |
| 500 | checkpoint_plus_10 | 10 | 292 | 106 (36%) | 6 | 154 | 56 |
| 500 | checkpoint_plus_100 | 100 | 1,433 | 1,016 (71%) | 55 | 127 | 77 |
| 500 | fingerprint_mismatch | 500 | 5,563 | 4,400 (79%) | 231 | 13 | 70 |
| 1,000 | full_replay | 1,000 | 16,614 | 13,908 (84%) | 427 | 0 | 0 |
| 1,000 | no_checkpoint | 1,000 | 16,567 | 13,873 (84%) | 425 | 0 | 100 |
| 1,000 | warm_checkpoint | 0 | 209 | 0 | 0 | 209 | 0 |
| 1,000 | checkpoint_plus_1 | 1 | 239 | 18 (8%) | 0 | 217 | 87 |
| 1,000 | checkpoint_plus_10 | 10 | 414 | 192 (46%) | 4 | 187 | 110 |
| 1,000 | checkpoint_plus_100 | 100 | 2,533 | 1,979 (78%) | 37 | 241 | 98 |
| 1,000 | fingerprint_mismatch | 1,000 | 17,149 | 14,356 (84%) | 435 | 15 | 88 |
| 2,000 | full_replay | 2,000 | 53,408 | 45,842 (86%) | 879 | 0 | 0 |
| 2,000 | no_checkpoint | 2,000 | 52,307 | 44,922 (86%) | 873 | 0 | 179 |
| 2,000 | warm_checkpoint | 0 | 500 | 0 | 0 | 499 | 0 |
| 2,000 | checkpoint_plus_1 | 1 | 484 | 26 (5%) | 0 | 457 | 211 |
| 2,000 | checkpoint_plus_10 | 10 | 710 | 234 (33%) | 5 | 399 | 225 |
| 2,000 | checkpoint_plus_100 | 100 | 5,561 | 4,437 (80%) | 50 | 462 | 202 |
| 2,000 | fingerprint_mismatch | 2,000 | 53,386 | 45,850 (86%) | 877 | 17 | 174 |
| 5,000 | full_replay | 5,000 | 274,931 | 241,880 (88%) | 2,274 | 0 | 0 |
| 5,000 | no_checkpoint | 5,000 | 299,505 | 263,724 (88%) | 2,568 | 0 | 485 |
| 5,000 | warm_checkpoint | 0 | 1,282 | 0 | 0 | 1,281 | 0 |
| 5,000 | checkpoint_plus_1 | 1 | 1,392 | 69 (5%) | 1 | 1,312 | 537 |
| 5,000 | checkpoint_plus_10 | 10 | 2,186 | 838 (38%) | 4 | 1,241 | 500 |
| 5,000 | checkpoint_plus_100 | 100 | 12,392 | 9,672 (78%) | 45 | 1,363 | 482 |
| 5,000 | fingerprint_mismatch | 5,000 | 268,761 | 236,708 (88%) | 2,295 | 22 | 482 |

Run spread at N=5,000: `full_replay` 256–305 s, `no_checkpoint` 277–340 s, `fingerprint_mismatch` 260–337 s. Only differences well outside that spread are meaningful. The full-replay-like scenarios are indistinguishable from one another.

### Build (live ingest)

| N | total ingest | mean ingest, last 100 events | journal size | checkpoint size |
|---|---|---|---|---|
| 500 | 8.3 s | 27 ms | 6.5 MB | 0.73 MB |
| 1,000 | 28.1 s | 49 ms | 32.1 MB | 1.63 MB |
| 2,000 | 84.5 s | 87 ms | 121.8 MB | 3.06 MB |
| 5,000 | 477.3 s | 180 ms | 713.7 MB | 7.58 MB |

## Findings (evidence only, no targets)

1. **Full replay is super-linear.** Doubling N from 1,000 to 2,000 multiplied recovery by 3.2×, and 2,000 → 5,000 (2.5×) multiplied it by 5.1×. Per-event live ingest grows linearly with history (27 → 180 ms), which matches ADR-022's diagnosis: every row stores cumulative output.
2. **`ExperienceService.observe` dominates replay:** 79% of full replay at N=500, 84% at 1,000, 86% at 2,000 and 88% at 5,000, rising with history. It is also 71–80% of a 100-event delta. The engines are about 1% (220–2,568 ms). This is **lower than the 93–96%** in ADR-022 (legacy journal prefix) and ADR-027 (cProfile, synthetic). The share depends on how large each recorded row is: a pathological ±3.0-step probe at N=200 measured 96%. The finding stands, and it is not optimized here: Experience is out of PERF-1 scope.
3. **A warm restart is bounded by checkpoint size, not history:** 0.18 → 1.28 s for 0.73 → 7.58 MB, against 5.2 → 274.9 s for full replay (−96.6% at N=500, −99.5% at N=5,000). This is the path H2 makes reachable on Windows.
4. **Crash/hard-kill delta cost** at N=5,000 is about 111 ms per uncovered event: (12,392 − 1,282) / 100. The count interval of 100 therefore bounds a crash restart at about 12 s at this size. The H3 age trigger bounds the same delta by time instead of count.
5. **A fingerprint mismatch costs a full replay** (268.8 s at N=5,000). This is G1 (any code change); H1 remains deferred for its separate correctness review.
6. **Checkpoint write cost** (H8 input): 66 → 485 ms for 0.73 → 7.58 MB. It runs under the runtime lock. At N=5,000 one write equals about 2.7 mean live ingests. Whether that matters is Rin's decision (D7); no change was made.
7. **Recovery never changed the journal** (0 rows added in every run), and every path reproduced the full-replay state.

## Limitations

- Synthetic data on one workstation. PROD journal size (tens of GB) is **not** extrapolated from these numbers.
- The example config has no paper session, so `_paper_event` cost is not exercised.
- The instrumentation wrappers add one `perf_counter` pair per call, which is negligible against the measured components.
- The `no_checkpoint` run includes the first-start write. `startup_ms` in the JSON also includes the config append and the post-recovery checkpoint write.
