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

### Integration sync (2026-09-25; integration base `f5bbdfa`)
- Rebased onto `f5bbdfa` (VALID-1 PR #34, on top of M30 PR #33) with no conflicts. Range-diff shows all patches unchanged. The Phase 2A SHAs above are pre-rebase; the mapping is in the [integration record](evidence/PERF1-integration-sync-f5bbdfa.md).
- No direct or semantic overlap with M30 or VALID-1. VALID-1 reads `code_fingerprint()` in its run manifests, so the future H1 review must include it.
- At `4c8fa51`:
  - Full suite: 555 passed, 3 skipped.
  - PERF-1 targeted: 26 passed.
  - Recovery tests: 103 passed, 2 skipped.
  - M30: 96 passed.
  - VALID-1: 56 passed.
  - ruff and mypy pass. The format check matches pristine main. `git diff --check` is clean.
- Benchmark re-run (N ≤ 2,000): deterministic outputs are identical to Phase 2A. Wall time was higher because of machine load that was not controlled.
- Status: in_review, ready for PR review. Nothing pushed or merged.
