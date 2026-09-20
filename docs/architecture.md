# NEXORA Architecture v0.1

## System context

```text
                 +------------------+
                 | Broker / MT5     |
                 +--------+---------+
                          |
                     ticks / OHLC
                          v
+---------------------------------------------------------+
| NEXORA Core                                             |
|                                                         |
| Market Data -> P&F -> Adaptive Box -> Structure         |
|                         |             |                  |
|                         +--> Matrix <-+                  |
|                               |                         |
|                         Regime / S&R                     |
|                               |                         |
|                         Signal Engine                    |
+-------------------------------+-------------------------+
                                |
                +---------------+---------------+
                |                               |
                v                               v
          PostgreSQL                    FastAPI/WebSocket
                                                |
                                                v
                                      NEXORA Web Dashboard
                                                |
                                      authenticated tunnel
                                                |
                                  Phone / Mac / Work PC
```

## Backend
Python is the reference implementation language for calculation and research engines. FastAPI exposes query APIs and WebSocket realtime events. Core domain packages must not depend on FastAPI so they can run identically in tests, backtests and live observation.

## Frontend
A React/Next.js web application will provide a responsive trading dashboard. The P&F chart should be a NEXORA-specific visualization rather than a copy of OX UI. Realtime updates arrive through WebSocket; historical/configuration queries use REST.

## Persistence
PostgreSQL stores normalized market events, engine configurations, P&F transitions, Matrix snapshots, S/R levels, signals and backtest runs. Raw market data and calculation versions must make results reproducible.

## Core event flow

```text
MarketTick
  -> NormalizedPriceEvent
  -> PnfTransition (optional)
  -> MatrixSnapshot (when state changes)
  -> Structure/Regime update
  -> ResearchSignal (optional)
  -> WebSocket event + persistence
```

## Suggested domain models
- MarketTick / Bar
- PnfConfig: symbol, box_mode, box_size, reversal_boxes, price_source, version
- PnfCell / PnfColumn / PnfTransition
- MatrixConfig / MatrixState
- MarketRegime
- StructureLevel
- Signal / SignalEvidence
- BacktestRun / BacktestMetric

## Experience memory (V1)

`ResearchRuntime` sends committed event/output artifacts directly to an internal
`ExperienceService` after existing decision/paper handling. No HTTP self-call or
Experience input to P&F, Matrix, Structure, Regime, Signal, Risk or Paper engines exists.
Pure freezing/fingerprint/measurement functions are separate from journal orchestration.
Immutable T0 snapshots and raw future observations precede append-only outcome/lifecycle
artifacts in the existing `research_journal` (migration 007), on PostgreSQL or the explicit
SQLite fallback. Startup replays original committed outputs, never reinterpreted decisions,
to finish interrupted writes and pending horizons. Scope is the configured runtime/feed;
inactive historical scopes remain queryable but need their runtime resumed to track further.

Read-only `/experiences`, `/experiences/summary`, `/experiences/{id}` and
`/experiences/{id}/outcomes` inherit existing local-access guards. Remote authenticated
encrypted deployment remains an unresolved release gate. UI is deferred for V1.
See [EX1](../tasks/EX1-experience-engine-v1.md) for exact semantics, schema and limitations.

## Security boundary
The home PC is trusted runtime infrastructure, but remote clients are untrusted by default. External access terminates through an authenticated encrypted VPN/tunnel/reverse proxy. PostgreSQL and MT5 adapter remain private. Secrets are supplied by environment variables or a local secret store and are excluded from Git.

## Phase 1 deployment

```text
Windows Home PC
  NEXORA API service
  NEXORA engine worker
  PostgreSQL
  NEXORA Web
  MT5 terminal/adapter
  secure remote-access service
```

Containerization can be used for PostgreSQL/API/Web, while an MT5 adapter may remain Windows-native where integration requires the desktop terminal.

## Design decisions
1. Separate repository from QuantoraTrade.
2. Research-first; no live execution in Phase 1.
3. Multi-resolution Matrix is a NEXORA concept; do not encode unverified OX semantics.
4. Engine logic is UI-independent and shared between replay/backtest/live observation.
5. Explainability and reproducibility are first-class requirements.
