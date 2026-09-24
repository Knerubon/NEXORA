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

Rin approves ADR-026, or requests changes. Quant answers Q-M3, Q-M4 and Q-M8. Architect answers Q-M1, Q-M2, Q-M5, Q-M6 and Q-M7.

## Execution record

- 2026-09-24: worktree created from origin/main 4f69e9c. The docs-only draft ADR and this task record were committed locally. Nothing was pushed and no PR was opened. Self-review only; independent review pending.
- The main worktree `D:\NEXORA\NEXORA` has pre-existing uncommitted changes that are not from this task (AGENTS.md, apps/api/nexora_api/main.py, apps/web/{app/page.tsx,package.json,next.config.mjs,tests/gateway.test.mjs}). They were left untouched.
