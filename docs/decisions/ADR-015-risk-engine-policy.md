# ADR-015 — Risk engine policy and replay contract

Status: accepted for P11 implementation
Date: 2026-09-18
Related: [ADR-014](./ADR-014-backtest-lab-reproducibility.md), [P11 task](../../tasks/P11-risk-engine.md)

## Context

P11 introduces a dedicated risk decision layer between research signals and paper execution. Decisions must be deterministic, fail closed on missing/stale inputs, and replayable with P10 outputs.

## Decision

1. Introduce pure-domain risk contracts:
   - `RiskPolicy`, `RiskProposal`, `RiskDecision`, `RiskState`
   - `AccountSnapshot` and `PriceSnapshot` decision-time inputs
2. Enforce fail-closed policy:
   - reject unknown quality, stale/missing price, currency mismatch, invalid stop/size
3. Apply deterministic limits with quantized sizing:
   - max risk per trade
   - max total exposure
   - max daily loss
   - max drawdown
4. Support idempotent proposals, reservation release, daily reset, and kill-switch.
5. Add replay harness over P10 signals for accepted/rejected parity evidence.

## Consequences

- Signal generation remains independent from risk authorization.
- Paper trading (P12) can consume explicit allow/reject decisions with policy version traceability.
- Risk decisions and state snapshots are persistable as append-only audit artifacts.
