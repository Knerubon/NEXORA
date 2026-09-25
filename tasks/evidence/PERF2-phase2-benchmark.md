# PERF-2 Phase 2 benchmark evidence (ADR-031 §18)

Date: 2026-09-25. Machine: Windows 11, Intel64 Family 6 Model 142, Python 3.13.3, SQLite (synthetic, benchmark-owned). No PROD, TSID runtime or legacy journal data was used.

Command, identical for both runs, run back to back on an idle machine:

```text
python scripts/experience_replay_benchmark.py --workdir <new scratch dir> \
    --cases calm:500 calm:1000 volatile:250 --runs 3 --breakdown --output <file>
```

- **Before:** `PYTHONPATH` = `git archive` of branch HEAD `64ebb12`. Its `packages/nexora/experience/` equals main `ec0aad5`.
- **After:** `PYTHONPATH` = the Phase 2 working tree (C1, C2, C3(a, b)).

Raw results: [before](PERF2-phase2-benchmark-before.json), [after](PERF2-phase2-benchmark-after.json). `meta.commit` records the harness checkout; `meta.code_under_test` says which code ran.

## Output equivalence at benchmark scale

| Case | Journal rows | Ordered journal digest, before-code build vs after-code build | Replay state hash, before vs after |
|---|---|---|---|
| calm 500 | 1,127 | identical | identical |
| calm 1,000 | 2,404 | identical | identical |
| volatile 250 | 1,545 | identical | identical |

## Replay time (median of 3 full replays, checkpoints disabled)

| Case | Before | After | ms/event | Speed-up | `observe` share |
|---|---|---|---|---|---|
| calm 500 | 6.29 s | 3.15 s | 12.6 → 6.3 | 2.0× | 78.6% → 61.7% |
| calm 1,000 | 19.83 s | 8.91 s | 19.8 → 8.9 | 2.2× | 83.7% → 63.6% |
| volatile 250 | 33.44 s | 7.75 s | 133.8 → 31.0 | 4.3× | 95.4% → 79.1% |

Individual runs: calm 500 before 6.63 / 6.19 / 6.29 s, after 3.15 / 3.08 / 3.17 s. calm 1,000 before 20.15 / 19.83 / 19.10 s, after 8.97 / 8.72 / 8.91 s. volatile 250 before 32.33 / 33.44 / 33.56 s, after 6.88 / 12.48 / 7.75 s (one noisy run).

## Cost breakdown (attributed run; seconds and share of that run)

| Category | calm 500 | calm 1,000 | volatile 250 |
|---|---|---|---|
| `freeze` / serialize / hash of the recorded output | 2.07 (34%) → 0.69 (20%) | 7.17 (34%) → 2.85 (29%) | 4.50 (13%) → 2.73 (36%) |
| Context parsing (`Experience.context`, `plan`, `plan_of`) | 0.83 (14%) → 0.01 (0.2%) | 5.03 (24%) → 0.04 (0.4%) | 23.18 (68%) → 0.23 (3%) |
| Idempotency digest | 0.55 (9%) → 0.06 (2%) | 2.36 (11%) → 0.20 (2%) | 1.70 (5%) → 0.12 (2%) |
| Append/write (commit + encode + SQL) | 1.23 → 1.30 | 2.96 → 2.93 | 2.75 → 2.53 |
| `decode` of each event (outside Experience) | 0.63 → 0.61 | 1.28 → 1.24 | 0.29 → 0.29 |

| Per event | calm 500 | calm 1,000 | volatile 250 |
|---|---|---|---|
| `Experience.context()` parses | 6.41 → 0.04 | 9.71 → 0.06 | 96.1 → 0.64 |
| Full-output canonical walks | ~4 → 1 + 0.04 | ~4 → 1 + 0.06 | ~4 → 1 + 0.64 |
| Experience appends / commits | 1.25 → 1.25 | 1.40 → 1.40 | 5.18 → 5.18 |
| Live ingest, mean | 16.8 → 11.5 ms | 31.9 → 19.7 ms | 165.3 → 63.4 ms |
| Live `observe`, mean | 9.5 → 4.1 ms | 18.1 → 6.3 ms | 129.0 → 26.4 ms |

The "+ x" is the new-snapshot rate: `materialize()` builds a T0 context only for events that start a new Experience.

## Not removed

Per-event cost still grows with history. The "after" per-decile `observe` means rise from 3.5 to 7.6 ms (calm 1,000) and from 6.8 to 28.5 ms (volatile 250). Replay remains quadratic in N; see ADR-031 §18.5. Append/write (C4) and `decode` (C5) are unchanged by design.

## Review

Rin code review (2026-09-25 21:29 +07:00) approved C1 → C2 → C3(a, b) for integration preparation on this evidence, and approved the `COVERED_FIELDS` `"_derived"` entry (Option A). See ADR-031 §19. The Decimal-exponent checkpoint-byte observation and the remaining O(N²) growth are deferred.
