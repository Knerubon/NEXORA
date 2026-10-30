# Offline validation record

Base: `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`.
Actual interpreter: CPython 3.13.16 on Linux. Dependencies installed by `uv sync --locked --extra api --extra postgres`; no MT5 extra. No production DSN or broker configured. Test prices are handcrafted or repository synthetic fixtures; runtime outputs use disposable test locations.

| Command | Actual result |
|---|---|
| `uv lock --check` | PASS; 36 packages resolved |
| `uv run --no-sync pytest -q tests/test_edge_proof_offline_20261030.py` | 13 passed, 2 xfailed, 11 subtests passed, 0.35 s on final name-only delta |
| `uv run --no-sync pytest -q tests/test_pnf.py tests/test_adaptive_box.py tests/test_signals.py tests/test_signal_upgrade.py tests/test_signal_intelligence.py tests/test_entry_readiness.py tests/test_risk.py tests/test_backtest.py tests/test_pattern_integration.py tests/test_validation_core.py` | 188 passed, 2 dependency deprecation warnings, 46.86 s |
| `uv run --no-sync pytest -q` | FAIL: 2046 passed, 5 failed, 2 skipped, 2 xfailed, 11 subtests passed, 2 warnings; 393.16 s. Failures in disposable process ownership/shutdown tests below; PostgreSQL tests skipped without disposable DSN. |
| `uv run --no-sync ruff check .` | PASS |
| `uv run --no-sync ruff format --check tests/test_edge_proof_offline_20261030.py` | PASS |
| `uv run --no-sync ruff format --check .` | FAIL: 24 existing files would be reformatted, 338 already formatted; no formatting applied. Ruff 0.16.8 also checks Python snippets in existing Markdown. This workstream's test passes formatting; do not claim repository formatting green. |
| `uv run --no-sync mypy` | PASS; 222 source files |
| `uv run --no-sync mypy tests/test_edge_proof_offline_20261030.py` | Initial invocation FAIL, 6 import-untyped errors when checking only installed modules; full project command above resolves source roots and passes. Agent also passed targeted check with explicit MYPYPATH. No suppression added. |
| `uv run --no-sync python scripts/recovery_drill.py` | PASS SQLite disposable backup + fresh-process paper/risk recovery; hash `1491ad9bfb21485d481bd15ba6936fd376cb041c5c670685854ecedeec901b29`; PostgreSQL, host crash, RPO/RTO and remote access NOT VERIFIED |
| `git diff --check` | PASS |
| Web test/lint/typecheck/build | NOT RUN: no web/production change; this report makes no web/merge readiness claim |
| Historical backtest / walk-forward / holdout | NOT RUN: prohibited by task and blocked on policy, harness fidelity and data/cost provenance |

## Explicit unresolved expected failures

- `test_nominal_stop_breach_must_not_be_ignored_known_defect`: current runner holds until timed exit despite a traversed nominal stop. Desired invariant, not an approved fixed-hold contract violation; production policy must first be decided.
- `test_restore_preserves_duplicate_seed_identity_known_defect`: direct PnfEngine restore does not preserve seed dedup identity. Replaying seed after restore can reverse; uninterrupted engine ignores it. This does not establish a ResearchPipeline checkpoint bug.

Both use unittest.expectedFailure: an unexpected success fails the test suite. They are not successful safety assertions. The public backtest and complete decision chain still need independent causal/portfolio/execution adversarial coverage.

## Full-suite failure diagnosis

Full run failures (all psutil NoSuchProcess / AccessDenied while inspecting disposable subprocesses):

- tests/test_environment.py::test_stop_validates_identity_and_leaves_other_environment_alive
- tests/test_launch_graceful_stop.py::test_graceful_stop_runs_application_shutdown_before_exit
- tests/test_launch_graceful_stop.py::test_ignored_request_falls_back_to_bounded_termination
- tests/test_launch_graceful_stop.py::test_ownership_checks_precede_any_stop_request
- tests/test_launch_graceful_stop.py::test_record_without_token_keeps_the_hard_path


A standalone `uv run --no-sync pytest -q tests/test_environment.py -x` reproduced failure `test_stop_validates_identity_and_leaves_other_environment_alive`: psutil could not inspect disposable subprocess PID (`NoSuchProcess`). 35 tests passed before it.

A second isolated worktree, with the same base source and environment test unchanged, reproduced the same test failure with `psutil.AccessDenied` reading `/proc/<pid>/cwd`:

`PYTHONPATH=packages:apps/api /workspace/scratch/360aace8544f/nexora/.venv/bin/python -m pytest -q tests/test_environment.py::test_stop_validates_identity_and_leaves_other_environment_alive`

Result: 1 failed. `git diff <base> -- packages apps tests/test_environment.py` is empty in the proof branch. Evidence is consistent with subprocess visibility restrictions in this container; exact environment-test root cause is not fixed or fully certified. No tests were weakened or skipped to obtain green. This failure blocks any all-checks-pass/merge-ready claim and is outside strategy proof scope.

## Source integrity and protected work

Source pin fingerprint (canonical JSON without its fingerprint field): `0dd3e9faca1447d519b0cd0bea2f09752711ef2f66f4f61aeeb36539c897d354`.
Independent reviewer verified 56 source-file hashes and this fingerprint. No configuration or strategy parameter was changed.

Main was fetched again and remains `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`. Protected PR metadata read and rechecked in this session (same HEADs, no mutation):

| PR | HEAD | State |
|---|---|---|
| #94 | 930af2aef829fa14064d0ad0be0cfa4bda12fbec | open, draft, unmerged |
| #95 | 60de0644591b9611df34dbf2b1960cd921f866d7 | open, draft, unmerged |
| #96 | 1c536d5acdef2c9fe47ef5a6ecfb127595ef42b8 | open, draft, unmerged |

No G1/G2/G3 approval, merge, market payload/holdout access, MT5, broker order, PROD or release occurred.
