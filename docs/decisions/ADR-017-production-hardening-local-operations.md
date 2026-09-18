# ADR-017 — Production hardening local operations contract

Status: accepted for P13 implementation scope  
Sources: [Requirements](../requirements.md), [Architecture](../architecture.md), [ADR-007](ADR-007-task-roadmap.md), [ADR-016](ADR-016-paper-trading-simulator-boundary.md)

## Context

P13 needs verifiable hardening evidence without enabling public exposure or live execution. The project currently runs local-only in-memory contracts, so operational checks must remain deterministic and safe for Phase 1.

## Decision

1. Add local-only operations endpoints:
   - `GET /operations/readiness` exposes `ready|degraded` state with explicit reason codes.
   - `GET /operations/alerts` emits info/warning/critical alerts from quote quality and paper runtime state.
2. Keep existing local-only host/origin guard on all operations endpoints and WebSocket channels.
3. Provide deterministic recovery drill script (`scripts/recovery_drill.py`) that validates:
   - required migration files for backtest/risk/paper contracts,
   - replay determinism hash equality across repeated runs,
   - checkpoint presence for restart evidence.
4. Keep paper safety boundary unchanged:
   - no broker calls,
   - no live order endpoints,
   - no credentials or tokens in repository examples.

## Consequences

- Operators can verify degraded readiness and alert paths in a reproducible local drill.
- Hardening evidence is executable from source checkout without external infrastructure.
- Remote/public deployment remains blocked pending separate authenticated+encrypted access design.
