# ADR-014 — Backtest Lab reproducibility contract

Status: accepted for P10 implementation
Date: 2026-09-18
Related: [ADR-012](./ADR-012-market-data-quality-sidecar.md), [P10 task](../../tasks/P10-backtest.md)

## Context

P10 must compare baseline/fixed/adaptive runs using the same dataset and assumptions while preserving deterministic reruns and explicit failure when dataset provenance mismatches.

## Decision

1. Backtest inputs are versioned contracts:
   - `DatasetManifest` with partition hashes and quality snapshot reference
   - `BacktestConfig` with engine/signal versions, split, seed, and execution/cost policy
2. Canonical hashes are computed over normalized dataclass payloads:
   - `dataset_hash` must match expected hash before running
   - `config_hash` and `assumptions_hash` are persisted with each run
3. Runner generates deterministic simulated trades from signal decision time and configured delay/hold policy.
4. Metrics include:
   - trade count, win rate, expectancy, profit factor (nullable), max drawdown, false-entry proxy, average entry delay
5. Lab API serves stored runs and compare-by-run-id responses; UI reads stored metrics from API and does not recalculate formulas.

## Consequences

- Missing/mutated dataset partitions fail closed with explicit errors.
- Zero-loss scenarios use nullable profit factor instead of divide-by-zero fallbacks.
- P10 remains research-only and does not add live broker execution paths.
