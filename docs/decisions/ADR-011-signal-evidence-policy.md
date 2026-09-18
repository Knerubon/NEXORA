# ADR-011 — Research signal evidence and dedup policy

Status: accepted for implementation in P8 scope
Date: 2026-09-18
Related: [ADR-008](./ADR-008-multi-resolution-matrix.md), [ADR-009](./ADR-009-market-structure-lifecycle.md), [ADR-010](./ADR-010-market-regime-thresholds.md), [P8 task](../../tasks/P8-signal-engine.md)

## Context

P8 must generate explainable research signals only, without risk sizing or order execution side effects, and must support deterministic replay/restart.

## Decision

1. Signals are emitted only from confirmed inputs available at decision time:
   - structure pivots
   - matrix alignment
   - regime classification
2. Signal payload includes:
   - occurrence/confirmation/decision timestamps
   - human `reasons`
   - machine `reason_codes`
   - source references and config/engine versions
3. Duplicate prevention:
   - active signal with same side + reason codes + source references is not re-emitted
4. Cooldown and expiry:
   - cooldown suppresses immediate repeats for configured event window
   - expiry marks stale active signals as `expired` via event progression only
5. No order/risk approvals are produced; output remains research-only contract.

## Consequences

- Downstream UI/backtest components receive traceable, auditable signal artifacts.
- Restart and replay remain deterministic through persisted snapshots/history.
- Risk and execution remain isolated for later phases.
