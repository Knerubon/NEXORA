# ADR-009 — Market structure pivot and S/R lifecycle

Status: accepted for implementation in P6 scope
Date: 2026-09-18
Related: [ADR-005](./ADR-005-pnf-fixed-box-rules.md), [P6 task](../../tasks/P6-market-structure.md)

## Context

P6 requires causal structure extraction where occurrence and confirmation times remain distinct, with explicit candidate level lifecycle and deterministic rebuild behavior.

## Decision

1. Confirmed pivot detection uses a 3-transition local window and confirms only at decision time:
   - center greater than neighbors => confirmed high
   - center less than neighbors => confirmed low
2. Pivot contract stores:
   - `occurrence_time`
   - `confirmation_time`
   - `source_transition_id`
   - `config_version`
3. Candidate levels are represented as:
   - `candidate | confirmed | invalidated | unavailable`
4. Confirmed levels become invalidated only by causal later transitions crossing the level boundary; previous snapshots are unchanged.
5. Structure snapshot storage uses deterministic JSON payload replay for restart/rebuild parity.

## Consequences

- Consumers can reason separately about “when it happened” vs “when it was known”.
- Level lifecycle is explicit and auditable.
- No probabilistic certainty claim is encoded in structure outputs.
