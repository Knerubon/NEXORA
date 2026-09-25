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
