# ADR-013 — Web dashboard realtime state contract

Status: accepted for P9 implementation
Date: 2026-09-18
Related: [ADR-003](./ADR-003-local-price-preview.md), [ADR-012](./ADR-012-market-data-quality-sidecar.md), [P9 task](../../tasks/P9-web-dashboard.md)

## Context

P9 extends the local quote preview into a multi-surface dashboard contract while preserving the Phase 1 research-only boundary and local-only access.

## Decision

1. API endpoints:
   - `GET /config`
   - `GET /quotes`
   - `GET /quality`
   - `GET /state`
   - `GET /history?limit=...`
2. WebSocket channels:
   - `ws/quotes` for backward-compatible quote snapshots
   - `ws/events` for typed realtime envelopes (`quote_snapshot`, `quality_snapshot`)
3. Access boundary:
   - loopback clients only
   - strict allowed origins (`http://127.0.0.1:3000`, `http://localhost:3000`, plus 3100)
   - trusted-host middleware and restricted CORS to local origins
4. Dashboard surfaces:
   - Live Structure
   - Matrix
   - Signals
   - Backtest Lab (`pending_p10` until P10 delivery)
   - System (quality counters/status)
5. No live order/execution routes are introduced.

## Consequences

- Frontend receives explicit loading/empty/stale/error states from API data instead of implied readiness.
- P10 can integrate Lab comparison on top of persisted API state/history contracts.
- Security posture remains local-first until authenticated encrypted remote boundary is added and validated.
