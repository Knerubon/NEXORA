---
task: VALID1
status: in_progress
depends_on: ["ADR-030 architecture approved (Rin 2026-09-25); Quant Q-V2–Q-V5 before any evidence-grade run", "ADR-024 Phase 2 on main for Pattern Engine subjects (L2c)", "ADR-027 coordination for RECORDED mode (Q-V9)"]
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-030-replay-validation-framework-v1.md"]
translation_needed: false
---

# VALID-1 — Replay Validation Framework V1

base_commit: 4f69e9c38803796ccfc8afc01480deffd8499c38
branch: claude/replay-validation-v1
worktree: D:\NEXORA\NEXORA-REPLAY-VALIDATION

## Assignment

This task is a user-authorized Phase 1 only: repository inspection, replay/validation architecture and a draft ADR. It includes no production code, engine or config change, and no access to PROD or legacy journal data.

## Context inspected (reason)

- research/{pipeline,runtime}.py, storage.py, artifacts.py, migrations 004/007: replay order, journal contract, hashing
- market_data/{models,policy,adapters,replay,repository}.py: event and time fields, bar semantics, existing replay reader
- backtest/{models,datasets,runner,fixtures}.py, ADR-014, scripts/research_cli.py: dataset contract and causal backtest rules
- pnf, structure, trendline, signals, entry_readiness, experience models and engines: evidence knowledge times and prefix invariance
- ADR-009/011/019/020/021/022, tasks/EX1, docs/environment-isolation.md: causality, time, recovery and isolation rules
- Branch drafts, read-only: ADR-023/024 (Track D), ADR-026 (M30), ADR-027 (Journal V2), ADR-028 (Experience compat): conflicts and alignment

## Phase 1 result

Draft [ADR-030](../docs/decisions/ADR-030-replay-validation-framework-v1.md).

- Drafted as ADR-029 (`ba5c388`), then renamed to ADR-030 on Rin's instruction because PERF-1 reserves ADR-029.
- Proposed Phase 2 split:
  - 2A: pure core with RECOMPUTED mode and the G1–G10 harness;
  - 2B: journal-backup extraction and RECORDED mode;
  - 2C: CLI and report;
  - 2D: Pattern Engine subjects.
- VALID-1 touches no shared files. Phase 2 adds new files only.

## Execution record

- 2026-09-25 Phase 1: self-review; independent review pending. Docs-only change; application tests not run (no code changed).
- 2026-09-25 Rin architecture review: APPROVED WITH REQUIRED DOC FIXES. The ADR was renamed 029 → 030 and Q-V1/Q-V6/Q-V7/Q-V8/Q-V10 were recorded. Phase 2A (pure offline core) is authorized.
- 2026-09-25 Phase 2A (pure offline core) implemented in `packages/nexora/validation/` plus tests. Self-review; independent review pending.
  - Validation tests: 56 passed (`tests/test_validation_causal.py`, `tests/test_validation_core.py`).
  - Full suite: 433 passed, 3 skipped.
  - `ruff check .` passes.
  - `mypy`: no findings in new code. Two pre-existing `psutil` stub errors come from the local environment (`types-psutil` is not installed in the shared `.venv`).
  - `scripts/recovery_drill.py` passes. `git diff --check` passes.
  - Rin code review pending.
