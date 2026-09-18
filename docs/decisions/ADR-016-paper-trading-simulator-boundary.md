# ADR-016 — Paper trading simulator boundary and recovery contract

Status: accepted for P12 implementation scope
Sources: [Requirements](../requirements.md), [Architecture](../architecture.md), [ADR-007](ADR-007-task-roadmap.md), [ADR-015](ADR-015-risk-engine-policy.md)

## Context

P12 requires a local paper simulator that consumes P11 RiskDecision contracts and remains strictly outside live broker execution. The simulator must provide deterministic traceability from research signal to paper order/fill to ledger, with restart-safe idempotency.

## Decision

1. `packages/nexora/paper` is the only paper execution package and remains core-domain (no API/UI/DB dependency).
2. Paper execution consumes `RiskDecision` results directly and does not duplicate sizing or policy limits.
3. Namespace is `paper-*` only. Any other namespace is rejected at construction.
4. Simulator behavior is fail-closed:
   - Rejected `RiskDecision` never creates fill.
   - Runtime `paused` or `kill_switch` status rejects all incoming decisions.
5. Idempotency key is `proposal_id`; duplicate proposals return the previously recorded order/fill and never create a second fill.
6. Recovery uses checkpoint snapshots that include orders/fills/positions/seen proposals. Restored simulator replays without duplicate fills.
7. API exposure is read-only (`/paper/replay`) for local observation contracts. No order placement endpoint is added.

## Consequences

- Preserves Phase 1 safety boundary: no live/demo broker path.
- Keeps risk ownership in P11 with explicit contract consumption in P12.
- Provides deterministic, replayable paper evidence for P13 hardening and recovery drills.
