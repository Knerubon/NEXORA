# ADR-008 — Multi-resolution Matrix contracts

Status: accepted for implementation in P5 scope
Date: 2026-09-18
Related: [ADR-005](./ADR-005-pnf-fixed-box-rules.md), [ADR-006](./ADR-006-adaptive-box-sizing.md), [P5 task](../../tasks/P5-matrix.md)

## Context

P5 requires Fast/Medium/Slow independent resolution views over shared normalized events while preserving deterministic replay and strict isolation between resolution states.

## Decision

1. Matrix snapshot is versioned (`schema_version=1`) and includes:
   - `symbol`, `sequence`, `watermark_sequence`, `generated_at`
   - overall `alignment` (`aligned_bullish|aligned_bearish|mixed|unavailable`) and `strength`
   - per-resolution state: `direction`, `latest_transition`, and `status` (`warmup|ready|stale|unavailable`)
2. Each resolution owns an independent `AdaptivePnfRunner`; no mutable state sharing across resolutions.
3. Watermark is event-sequence based and monotonic; append-only processing must not rewrite prior snapshots.
4. Staleness is explicit (`status=stale`) when a resolution has not produced a fresh transition within configured event distance.
5. Persistence contract stores snapshot payloads as deterministic JSON with rebuild by ordered replay.

## Consequences

- Downstream modules (P6–P9) consume stable matrix schema with explicit unavailable/stale semantics.
- Matrix remains pure-domain and transport/storage agnostic.
- Configuration changes require a new run/version rather than historical rewrite.
