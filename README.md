# NEXORA

Adaptive Price Structure Trading System.

NEXORA is an independent trading research project focused on price-structure analysis and noise reduction. It is intentionally separate from QuantoraTrade.

## Phase 1

Phase 1 is research and observation only — **no live auto-trading**.

- Market data ingestion
- Point & Figure (P&F) engine
- Fixed and adaptive box/noise filtering
- Multi-resolution Matrix (Fast / Medium / Slow)
- Market-regime detection
- Support / Resistance structure
- Signal logging with explainable reasons
- FastAPI + WebSocket realtime API
- PostgreSQL persistence
- Web dashboard
- Backtest lab

## Target architecture

```text
Broker / MT5
    |
Market Data
    |
P&F + Adaptive Box Engine
    |
Market Structure / Matrix / S&R
    |
Signal Engine
    +----> PostgreSQL
    |
FastAPI + WebSocket
    |
NEXORA Web Dashboard
    |
Secure remote access
    |
Phone / Mac / Work PC
```

The primary runtime target is a Windows home PC. Remote access must use authenticated secure networking/tunneling; broker credentials, API keys, passwords and other secrets must never be committed to Git.

## Planned repository layout

```text
apps/
  api/              # FastAPI / WebSocket application
  web/              # NEXORA web dashboard
packages/
  market_data/      # broker adapters and normalized market events
  pnf/              # Point & Figure engine
  adaptive_box/     # volatility-aware box sizing
  matrix/           # multi-resolution structure engine
  market_regime/    # trend / range / volatility classification
  structure/        # S/R and price structure
  signals/          # explainable signal generation
  risk/             # future risk controls (not live in Phase 1)
  backtest/         # deterministic research/backtesting
infra/              # local/server deployment configuration
tests/              # unit, integration and replay tests
docs/               # requirements, architecture and research notes
scripts/             # development/runtime helpers
```

## Engineering principles

1. Raw market data is immutable; derived structures can always be rebuilt.
2. Every signal must be explainable from stored inputs and parameters.
3. P&F parameters and Matrix resolutions are configuration, not hard-coded assumptions.
4. Backtests and live observation must use the same core calculation engines.
5. No secrets in source control.
6. Paper/research mode comes before any live order execution.

See `docs/requirements.md` and `docs/architecture.md` as the project is built out.

## Execution roadmap

See [P1–P13 roadmap](docs/roadmap.md) and [ADR-007 numbering/boundary clarification](docs/decisions/ADR-007-task-roadmap.md).
Includes additive market-data quality (DQ1), dataset versioning (P10), a separate Risk Engine (P11), local Paper Trading (P12) and Production Hardening (P13).
Requirements/architecture remain the source of truth; no live/demo broker orders. Historical task records are preserved; merged implementation is not proof of completed independent review.

## Research runtime status

[FIX1 corrective work](tasks/FIX1-system-readiness.md) connects recorded inputs, shared engines, durable paper state and actual-price backtests. Read [runtime setup and release gaps](docs/research-runtime.md) before using the system; merged phase code does not certify production readiness.
