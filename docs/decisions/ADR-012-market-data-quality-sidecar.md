# ADR-012 — Market-data quality sidecar contract

Status: accepted for DQ1 implementation
Date: 2026-09-18
Related: [ADR-004](./ADR-004-market-data.md), [DQ1 task](../../tasks/DQ1-market-data-quality.md)

## Context

DQ1 requires observability for freshness, latency, gaps, reconnects, and transport failures without rewriting P2 normalized event semantics.

## Decision

1. Introduce versioned sidecar contracts:
   - `QualityConfig`
   - `QualityCounters`
   - `QualitySnapshot`
2. Quality monitor consumes normalized events and transport statuses, tracking:
   - duplicate/out-of-order/gap/backfill counters
   - disconnect/reconnect/rejected counters
   - freshness, clock-skew, latency, and market-closed states
3. Sidecar is append-only and independent from raw/normalized event tables.
4. Restart/rebuild parity is provided through deterministic JSON snapshot replay.
5. API consumers may use sidecar completeness states (`complete|partial|unknown`) without asserting hidden tick completeness.

## Consequences

- P2 normalization and replay outputs remain unchanged.
- P9/P10/P11/P13 can consume quality metadata with explicit uncertainty.
- Local-only logs and payloads stay sanitized (no credentials/account payload leaks).
