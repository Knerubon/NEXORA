---
task: M30B1
status: in_review
depends_on: ["ADR-026 acceptance for Phase 2A (Rin): recorded 2026-09-25, after 56ba6a7", "Quant Q-M3/Q-M4/Q-M8 (open; no defaults)", "ADR-023 acceptance (Track D) for Phase 2B runtime wiring", "Track B coordination for Phase 2B checkpoint state"]
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-026-m30-next-candle-bias-v1.md"]
translation_needed: false
---

# M30 Next Candle Bias V1

base_commit: 4f69e9c38803796ccfc8afc01480deffd8499c38
branch: claude/m30-next-candle-bias-v1
worktree: D:\NEXORA\NEXORA-M30-BIAS

## Assignment

This task is a user-authorized Phase 1 only. It covers inspection, time semantics, the prediction, evaluation and no-look-ahead contracts, MFE/MAE, and Feature Lifecycle dependency analysis, and it produces a draft ADR. No implementation, algorithm, model, UI, chart overlay, Entry Readiness/Pattern Engine change or PROD data access is part of it.

## Context inspected (reason)

- market_data/{models,policy,fixtures,adapters}.py, apps/api/nexora_api/quotes.py: timestamp and bar semantics
- research/{pipeline,runtime,checkpoint_state}.py: causal per-row output, recovery, checkpoint coverage
- experience/{models,engine}.py, tasks/EX1: existing T0/horizon/excursion conventions
- signals, structure, trendline, market_regime, pnf, matrix models: time fields of the evidence
- tasks/DC1, tasks/TS1, ADR-004/014/019/022: bias naming, time proposal, replay, backtest, recovery
- Track D worktree ADR-023/024 (draft, read-only): lifecycle and pattern dependency

## Phase 1 result

Draft [ADR-026](../docs/decisions/ADR-026-m30-next-candle-bias-v1.md).
- ADR-023/024 (Track D) and ADR-025 (MT5 broker worktree, untracked) are already claimed.
- The Phase 2 split proposed for Rin:
  - 2a: pure core (bucketing, freeze, evaluation, no-look-ahead tests), with no wiring;
  - 2b: SHADOW runtime sidecar, persistence and checkpoint state, after ADR-023 acceptance and Track B coordination;
  - 2c: read API;
  - 2d: UI card.
- The algorithm needs a separate Quant ADR.

## Unblock condition

Rin final approval of ADR-026 rev 2 unblocks Phase 2A. Quant still needs to answer Q-M3, Q-M4 and Q-M8. Phase 2B needs ADR-023 acceptance and Track B coordination.

Status 2026-09-25:
- The Phase 2A gate is satisfied prospectively. It was satisfied only after implementation; see the execution record.
- Phase 2A is `in_review`, awaiting independent re-review.
- Phase 2B remains blocked on ADR-023 and Track B.

## Rin architecture review — rev 2 (2026-09-24)

Frozen Architect decisions, recorded in ADR-026 Decision 0:
- **Q-M1:** Pure Core may be built before ADR-023 is accepted, limited to models, candles, freeze, evaluate and pure tests.
- **Q-M2:** lifecycle is DISABLED → SHADOW → ACTIVE. ACTIVE is analytical output only, with no decision authority.
- **Q-M5:** no edits to Track B `runtime.py` or `checkpoint_state.py`. Phase 2A (pure) and 2B (integration) are split, with an explicit checkpoint schema version.
- **Q-M6:** provenance records `LIVE_GENERATED` / `REPLAY_GENERATED`, and reports never pool them silently.
- **Q-M7:** M30 consumes the upstream time contract and owns no broker offset logic.

Added contracts:
- Candle Identity (5A)
- Algorithm Identity with journal uniqueness (5B)
- Freeze-before-write crash contract (12A)

Still open: Q-M3, Q-M4 and Q-M8 (Quant).

## Execution record

- 2026-09-24 rev 1: worktree created from origin/main 4f69e9c. The docs-only draft ADR and this task record were committed locally. Nothing was pushed and no PR was opened. Self-review only; independent review pending.
- The main worktree `D:\NEXORA\NEXORA` has pre-existing uncommitted changes that are not from this task (AGENTS.md, apps/api/nexora_api/main.py, apps/web/{app/page.tsx,package.json,next.config.mjs,tests/gateway.test.mjs}). They were left untouched.
- 2026-09-24 rev 2: ADR-026 revised per Rin's review. Docs only, local commit, not pushed, no PR. Self-review; Rin's final review is pending.
- 2026-09-24 Phase 2A: pure core `56ba6a7` was committed locally. It covers `packages/nexora/m30_bias/*` and `tests/test_m30_bias_*.py`, with no runtime, persistence, config, API or UI. **Governance gap:** it was committed while ADR-026 still read "proposed — NOT accepted" and this task was `blocked`, so the Decision 17 gate was not met at that time.
- 2026-09-25 independent Phase 2A review of HEAD `56ba6a7`: `CHANGES_REQUESTED`. There were no blocking technical defects. Findings:
  - B-1: the governance gate was unmet.
  - NB-1: the Decision 3 `SKIPPED` target wording was ambiguous for Δ > 0.
  - NB-2: Δ > 0 tests were missing.
  - Validation: 88 M30 tests passed; the full suite had 465 passed and 3 skipped; ruff, format and mypy were clean.
  - The original review prompt cited `3a4732b`. That commit belongs to the ADR-025 / MT5 multi-broker track, so it was an incorrect cross-track reference and not M30 evidence.
- 2026-09-25 Rin architecture acceptance: ADR-026 rev 2 was accepted for the Phase 2A Architect scope, subject to the Decision 3 clarification. The current `freeze.py` semantics were accepted: `SKIPPED` goes to the latest crossed target, `bucket_start(F.event_time + Δ)`. The gate is satisfied prospectively, and the earlier sequence is not rewritten.
- 2026-09-25 governance closure commit on `claude/m30-next-candle-bias-v1` (previous HEAD `56ba6a7`):
  - ADR-026 rev 2.1: accepted status, acceptance record, normative Decision 3 wording.
  - Δ > 0 regression tests added in `tests/test_m30_bias_core.py`.
  - No production code change.
  - Local commit only; not pushed; no PR; no merge.
  - Independent re-review is pending. The session that performed the first review also authored this closure, so it cannot re-review.

## Deferred review observations (not Phase 2A work)

- **O-1 (Phase 2B design):** a feed identity change, such as a changed MT5 offset, halts the core permanently instead of starting new `candle_id`s.
- **O-2 (Phase 2B / algorithm ADR):** `except Exception` around the algorithm and θ policy stores transient failures as write-once content. A later replay surfaces them as a conflict. θ failure has no dedicated reason code.
- **O-3 (ADR clarification candidate):** when an excursion is 0, `mfe_time`/`mae_time` are null. The behavior is tested, but the ADR does not state it.
- **O-4 (note):** the `threshold_label`/`first_touch` primitives rely on the core's θ > 0 guard.
- **O-5:** execution-record evidence for `56ba6a7`, addressed by this record.
