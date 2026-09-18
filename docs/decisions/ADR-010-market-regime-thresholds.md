# ADR-010 — Market regime decision table

Status: accepted for implementation in P7 scope
Date: 2026-09-18
Related: [ADR-008](./ADR-008-multi-resolution-matrix.md), [ADR-009](./ADR-009-market-structure-lifecycle.md), [P7 task](../../tasks/P7-market-regime.md)

## Context

P7 must classify trend/range/high-volatility from already-confirmed structure and matrix states, with explicit warm-up/unknown handling and deterministic replay.

## Decision

1. Inputs:
   - confirmed structure pivots (lookback window)
   - matrix alignment availability
2. Output state labels:
   - `unknown`: unavailable matrix or insufficient pivots
   - `high_volatility`: pivot-width over volatility threshold
   - `trend`: absolute slope over trend threshold
   - `range`: remaining classified states
3. Hysteresis hold:
   - if label switch occurs near threshold boundary, keep previous non-unknown label
   - reason code is `hysteresis_hold`
4. Every classification stores `effective_time`, `source_ref`, `reason`, and `config_version`.
5. Persistence/rebuild uses ordered deterministic JSON payload replay.

## Consequences

- Regime metadata stays causal and explainable.
- Boundary thrash is reduced with explicit hysteresis policy.
- P8 signal decisions can reference stable regime reason/version metadata.
