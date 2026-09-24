---
task: M30B1
status: blocked
depends_on: ["ADR-026 acceptance (Rin; Quant for outcome/θ/Δ)", "ADR-023 acceptance (Track D) for runtime wiring", "Track B coordination for checkpoint state"]
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
