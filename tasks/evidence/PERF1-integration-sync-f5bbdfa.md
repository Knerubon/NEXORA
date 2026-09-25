# PERF-1 integration sync onto `f5bbdfa`

Written for: Rin (integration review) and the PERF-1 PR reviewers. This record adds to the Phase 2A evidence and does not replace it. [PERF1-recovery-benchmark.md](PERF1-recovery-benchmark.md) and its JSON are unchanged historical records at code `3c571ca`.

## Bases and rebase

| Item | Value |
|---|---|
| Original PERF-1 base | `4f69e9c` (PR #32 merge) |
| New integration base | `f5bbdfaf37e30175eeb3c6cd617d8cdd05addbc3` (PR #34 VALID-1, on top of PR #33 M30); matches `origin/main` at fetch |
| Before the sync | `3c571ca` plus uncommitted Phase 2A docs. Those docs were committed as `d90a032` before the rebase. |
| Rebase | `git rebase f5bbdfa` completed with no conflicts. `git range-diff 4f69e9c..d90a032 f5bbdfa..HEAD` shows all 5 patches identical (`=`). |
| SHA mapping | `27a5111`→`1a34d15`, `fd8ae4a`→`6e76adc`, `0c1c778`→`9c0e4af`, `3c571ca`→`5db34b8`, `d90a032`→`4c8fa51` |
| Pre-rebase commits | kept reachable through the local ref `perf1-pre-rebase-backup-tmp`, so the `3c571ca` evidence stays verifiable; not pushed |

## Overlap analysis (`4f69e9c..f5bbdfa`: 28 files, +6,617 / −0)

| Area | Direct overlap | Semantic overlap | Assessment |
|---|---|---|---|
| M30 (`packages/nexora/m30_bias/**`, ADR-026, tests) | none | none. The purity test scans only `m30_bias/*.py`. The package is not imported by the recovery path. | no conflict |
| VALID-1 (`packages/nexora/validation/**`, ADR-030, tests) | none | `validation/runner.py` imports `nexora.research.checkpoint.code_fingerprint()` into run manifests. Tests assert only its length (64). PERF-1 does not change `code_fingerprint` (H1 deferred). | no conflict. **New consumer for H1** (see below). |
| Recovery/checkpoint (`runtime.py`, `checkpoint*.py`, `storage.py`, recovery tests) | none: main did not change them | none | PERF-1 changes apply unchanged |
| API/launch (`main.py`, `launch.py`, `research.py`) | none: main did not change them | none | PERF-1 changes apply unchanged |
| Tests/config (shared fixtures, `pyproject.toml`, `uv.lock`, env examples) | none. `tests/validation_fixtures.py` is new and not used by PERF-1. | none | no conflict |
| ADR numbering | ADR-029 is not on main. ADR-030 records that it was renumbered because PERF-1 reserves ADR-029. | none | no stale reference: ADR-029's "in flight" list (ADR-023/024/027/028) is still accurate, since none of those is on main |

No PERF-1 commit overwrites newer main behavior, because main changed none of the files PERF-1 modifies. `git diff --name-status f5bbdfa HEAD` lists only PERF-1 paths.

**H1 observation (no change made):** VALID-1 now records `code_fingerprint()` as run-manifest identity. Any future H1 change to that function's scope changes VALID-1's code identity semantics too, so the H1 correctness review must include VALID-1 (ADR-030) as a consumer.

## Validation at `4c8fa51` (clean tree)

| Check | Command | Result |
|---|---|---|
| PERF-1 targeted | `pytest tests/test_checkpoint_schedule.py tests/test_launch_graceful_stop.py tests/test_recovery_benchmark.py` | 26 passed |
| Recovery/checkpoint | `pytest tests/test_recovery_checkpoint.py tests/test_startup_recovery.py tests/test_postgres_journal.py tests/test_environment.py` | 103 passed, 2 skipped (no PostgreSQL test service; no symlink privilege: environment-only, also skipped at base) |
| M30 | `pytest tests/test_m30_bias_*.py` (5 files) | 96 passed |
| VALID-1 | `pytest tests/test_validation_causal.py tests/test_validation_core.py` | 56 passed |
| Full suite | `.venv/Scripts/python -m pytest` | 555 passed, 3 skipped |
| Ruff | `ruff check .` | all checks passed |
| Format | `ruff format --check` on the 8 changed Python files | already formatted. Repo-wide: 22 files flagged, **the same file set** as a pristine `git archive f5bbdfa` export, so none of them come from PERF-1. |
| Mypy | `mypy` (strict; packages, apps/api, tests, scripts) | no issues in 141 files |
| Diff check | `git diff --check f5bbdfa HEAD` | clean |

## Benchmark re-verification

- Command: `scripts/recovery_benchmark.py --sizes 500 1000 2000 --runs 3 --warmup 1` at `4c8fa51`, started 2026-09-25T10:41:25Z, exit 0.
- Output: [PERF1-integration-benchmark-f5bbdfa.json](PERF1-integration-benchmark-f5bbdfa.json).
- Synthetic scratch data only.
- N=5,000 was not re-run: it took about 4.4 h, and its evidence at `3c571ca` stays the historical baseline.

Deterministic outputs are **identical** to the Phase 2A run for every N and scenario:
- the final state hash (equal to full replay in every scenario);
- replay counts, recovery modes and reasons;
- checkpoint bytes and journal bytes;
- 0 journal rows changed;
- the `ExperienceService.observe` share of full replay: 79% / 83–84% / 86%.

| N | scenario | Phase 2A median (`3c571ca`) | re-run median (`4c8fa51`) |
|---|---|---|---|
| 500 | full replay / warm / +100 | 5,198 / 176 / 1,433 ms | 5,623 / 181 / 1,470 ms |
| 1,000 | full replay / warm / +100 | 16,614 / 209 / 2,533 ms | 19,549 / 282 / 3,884 ms |
| 2,000 | full replay / warm / +100 | 53,408 / 500 / 5,561 ms | 62,351 / 643 / 6,950 ms |

Wall-clock medians are 3–53% higher across all scenarios, including the full-replay-only paths that PERF-1 does not alter. The cause cannot be PERF-1 code:
- the patches are byte-identical;
- `nexora.validation` and `nexora.m30_bias` are not imported on the recovery path (verified through `sys.modules` after importing `nexora.research.runtime`).

It is **attributed to machine load, which was not controlled**. The live PROD API (`NEXORA-TSID`, running since 2026-09-23) and other desktop tools were running during the re-run. Relative findings are unchanged: warm restart is about 1% of full replay, and Experience dominates replay.

## Current integration assessment

No blocking issue. PERF-1 is mechanically and semantically independent of M30 and VALID-1 at `f5bbdfa`. H2/H3/H5 behavior and the benchmark contract re-verified. The branch is ready for PR review. The following remain open and are not part of this sync: D2 (Rin + Security must confirm the graceful-stop mechanism) and D3 (value of T). The ADR is not accepted.
