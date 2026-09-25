# PNF2 — Pattern Engine Phase 2 integration

Status: in_progress
Owner: DEV-PNF / Codex Lane C (current session)
Branch: `claude/pnf-pattern-engine-v1`
Worktree: `D:\NEXORA\NEXORA-PATTERN-ENGINE`
Base: `6cdff36119a766a99a421591fb2ddcec4c8405ec`

## Scope and context

Complete ADR-024 Phase 2 after PERF-2 merged; preserve the Phase 2A engine.
No Signal source switch, Quant decisions, classic formulas, UI or Experience semantics.
User authorizes checkpoint integration contract resolution and implementation;
no push, PR or merge to main.

Required context:
- `docs/requirements.md`, `docs/architecture.md`: scope and core boundaries.
- `docs/decisions/ADR-023-feature-lifecycle-v1.md` §11 and
  `docs/decisions/ADR-024-pnf-pattern-engine-v1.md` §13: accepted integration requirements.
- `docs/decisions/ADR-022-startup-recovery-checkpoint-v1.md`: JSON/version/fallback rules.
- `docs/decisions/ADR-031-experience-replay-performance-v1.md`: merged derived-cache contract.
- `packages/nexora/research/{pipeline,runtime,checkpoint,checkpoint_state}.py`:
  wiring, payload schema, validation and atomic adoption.
- `apps/api/nexora_api/research.py`, `packages/nexora/features.py`:
  startup feature configuration; `packages/nexora/patterns/`: existing engine contract.
- `apps/api/nexora_api/environment.py`, `tests/test_environment.py`:
  apply existing configuration-path isolation to the new optional feature file.
- `tests/test_recovery_checkpoint.py`, `tests/test_checkpoint_schedule.py`,
  `tests/test_startup_recovery.py`, `tests/test_experience_replay_equivalence.py`,
  `tests/test_dashboard_api.py`, `tests/conftest.py`:
  isolated fixtures, recovery/API contracts and PERF-2 regression evidence.
- `docs/development.md`, `pyproject.toml`: validation commands.
- `.env.example`, `config/development.env.example`, `config/production.env.example`:
  optional startup setting documentation.

## Acceptance

- DISABLED defaults and SHADOW evidence preserve trading outputs and stream identity.
- Explicit typed Pattern state, startup feature hash validation, atomic restore/fallback.
- Cold replay, checkpoint plus delta and uninterrupted results are deterministic/equivalent.
- Corrupt/mismatched sections reject without partial adoption or rewriting journal rows.
- PERF-2 `_derived` remains unpersisted and reconstructed on demand.
- Targeted checks precede broader regression; no expensive benchmarks or production data.

## Execution record

- Fetched main at the base above; synced existing branch without conflicts.
- PERF-2 checkpoint diff only adds coverage/documentation for `_derived`.
- Concrete contract recorded before implementation in ADR-023 §11e / ADR-024 §13i.
- Implementation: pipeline event wiring, startup feature config/path isolation, typed
  state codec and atomic restore. No Phase 2A detection changes. Only the Phase 2A
  test's no-wiring guard changed to an explicit three-module orchestration allowlist.
- Targeted baseline before code: `.venv/Scripts/python -m pytest tests/test_features.py
  tests/test_pattern_engine.py tests/test_pnf.py tests/test_recovery_checkpoint.py -q`
  — 219 passed (198.34s).
- Final Pattern/lifecycle/integration: `.venv/Scripts/python -m pytest
  tests/test_pattern_engine.py tests/test_features.py tests/test_pattern_integration.py -q`
  — 182 passed (51.32s). Includes disabled/shadow/partial-unit control, API config,
  every-event state round trip, expired evidence, rejected-event health, no trading
  mutation, feature-hash mismatch and corruption rejection, no journal rewrite,
  cold/checkpoint/delta/future-event parity and PERF-2 derived cache reset.
- P&F regression (`tests/test_pnf.py tests/test_structure.py tests/test_signals.py
  tests/test_signal_upgrade.py tests/test_trendline.py`) passed in the combined targeted
  run. That run initially had one obsolete Phase 2A no-wiring assertion failure;
  its corrected allowlist passed both individually and in the final 182-test run.
- `.venv/Scripts/ruff check .`: PASS.
- `.venv/Scripts/mypy`: PASS, 157 source files.
- Changed Python files: `.venv/Scripts/ruff format --check` PASS (8 files).
  Repo-wide format check: FAIL, 24 existing files outside this integration's edits;
  no unrelated reformatting performed.
- `D:/NEXORA/NEXORA/.tools/Scripts/uv.exe lock --check` and `.../uv.exe build`:
  PASS (shared executable only; output in this worktree's ignored `dist/`).
- Recovery/PERF-2 and full regression: pending.

## Runtime and review boundary

- No runtime/configuration deployment, DB migration, journal rewrite or production-data access.
- New code fingerprint invalidates previous checkpoints once; full replay rebuilds the
  disposable JSON payload at state version 2. Header schema remains 2.
- Startup feature changes retain stream identity, invalidate incompatible checkpoints,
  rebuild Pattern state and leave historical feature metadata untouched.
- SHADOW/DISABLED outputs differ only in their additive feature block. Whole-output
  provenance hashes may differ as already documented in ADR-024; this is not a change
  to Experience identity or trading decisions.
- Self-review (DEV-PNF acting as QA/Integrator); independent code/security review pending.
  No claim of Rin/Quant approval of this implementation. Q-PE1–3, classic formulas,
  Phase 3 source switch and Phase 5 chart consumption remain deferred.
- No push, PR or merge to main. API TestClient coverage used without binding service ports;
  frontend validation and the external API smoke script are not run (no frontend changes;
  startup and output verified in isolated API tests). No benchmark commands executed.
