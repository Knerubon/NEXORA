# PERF1 Recovery Checkpoint Hardening

status: in_review
translation_needed: false
owner: DEV-PERF (PERF-1)
base_commit: 4f69e9c
branch: claude/recovery-checkpoint-v1
worktree: D:\NEXORA\NEXORA-RECOVERY-CHECKPOINT

Inputs: [ADR-029](../docs/decisions/ADR-029-recovery-checkpoint-v1-hardening.md), [ADR-022](../docs/decisions/ADR-022-startup-recovery-checkpoint-v1.md), docs/requirements.md, docs/architecture.md, docs/research-runtime.md, docs/environment-isolation.md.

Scope (Rin, 2026-09-25): Phase 2A only.
- (A) synthetic recovery benchmark baseline;
- (B) H2 graceful stop in `apps/api/nexora_api/launch.py`;
- (C) H3 time/idle checkpoint trigger in checkpoint scheduling;
- (D) H5 recovery facts in the readiness payload.

Not authorized: H1, H4, H6, H7, H8. Do not modify `packages/nexora/experience/**`, `packages/nexora/storage.py`, `code_fingerprint`, `checkpoint_state.py`, engines, PROD or legacy data, or other worktrees.

## Execution record

### Phase 1 (2026-09-25)
- Draft ADR-029 (`27a5111`): ADR-022 is already merged, so PERF-1 was re-scoped as hardening.
- Rin: approved with changes. D9 resolved: Startup Recovery V1 is closed and PERF-1 owns the hardening work.

### Phase 2A (2026-09-25; self-review, independent review pending)
- `fd8ae4a`: H3 optional age trigger (off by default, injectable clock) and H5 `recovery` object in readiness (informational).
- `0c1c778`: H2 per-run stop token, bounded 30 s graceful wait, then the unchanged terminate/kill fallback.
- `3c571ca`: synthetic recovery benchmark and its tests.
- Tests at `3c571ca` (base `4f69e9c`): `.venv/Scripts/python -m pytest` gave 403 passed and 3 skipped (baseline 377 passed, 3 skipped). `ruff check .` and `mypy` pass. `ruff format --check` passes on the changed files; the 20 files it flags repo-wide already fail at base.
- Benchmark: [evidence](evidence/PERF1-recovery-benchmark.md), N = 500 to 5,000, median of 3.
- Open decisions carried to Rin: D2 (the file token deviates from the recommended console signal) and D3 (no value chosen for T; a true idle timer is not implemented).
