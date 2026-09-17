# NEXORA Requirements v0.1

## Product goal
Build a web-based trading research system that transforms raw price movement into cleaner, explainable market structure using Point & Figure concepts, adaptive noise filtering and multiple structure resolutions.

## Phase 1 scope

### FR-01 Market data
- Ingest XAUUSD market data from a broker/MT5 adapter.
- Normalize ticks/OHLC into an internal event format.
- Persist sufficient raw data for deterministic replay.

### FR-02 P&F engine
- Support configurable box size.
- Support configurable reversal size.
- Generate deterministic X/O columns from the same input stream.
- Preserve transition reason, price and timestamp for debugging/replay.

### FR-03 Adaptive box engine
- Support fixed box mode first.
- Add volatility-aware mode (initial research candidate: ATR-derived sizing).
- Record the box-sizing rule/version with every derived run.

### FR-04 Multi-resolution Matrix
- Run at least three independently configured structure resolutions: Fast, Medium and Slow.
- Expose current X/O direction and latest transition for each resolution.
- Do not assume OX BOX 10/20/30 semantics; NEXORA parameters must be independently researched and validated.

### FR-05 Market structure
- Detect structural highs/lows and candidate support/resistance.
- Provide trend/range/high-volatility regime metadata.

### FR-06 Signals
- Generate research signals only in Phase 1.
- Every signal must contain human-readable reasons plus machine-readable evidence.
- Log signal version, parameters and source data references.

### FR-07 Web/API
- FastAPI REST endpoints for state/history/configuration.
- WebSocket stream for realtime price, P&F transitions, Matrix changes and signals.
- Responsive dashboard usable from desktop and mobile.

### FR-08 Dashboard
Initial views:
1. Live Structure — P&F chart + S/R + current price.
2. Matrix — Fast/Medium/Slow alignment and explanation.
3. Signals — history, evidence and reasons.
4. Backtest Lab — compare parameter sets and strategies.
5. System — broker/data/database/engine health.

### FR-09 Backtesting
- Replay historical data through the same core engines used by live observation.
- Compare baseline candlestick approach vs fixed P&F vs adaptive P&F.
- Initial metrics: trade count, win rate, expectancy, profit factor, max drawdown, false-entry proxy and latency/entry delay.
- Include spread/commission/slippage assumptions where trade simulation is used.

## Non-functional requirements
- Windows home PC is the initial production-like host.
- PostgreSQL is the primary persistence layer.
- Calculation engines must be deterministic and unit-testable without UI/API.
- No credentials or secrets in repository/config examples.
- Remote web access must be authenticated and encrypted; do not expose database/MT5 services directly to the public Internet.
- Logs must make state transitions traceable.

## Explicitly out of Phase 1
- Live automatic order execution.
- Copying proprietary OX implementation details.
- Treating visually inferred OX BOX 10/20/30 behavior as confirmed specifications.

## Phase gates
P1 Foundation -> P2 Market Data -> P3 P&F Engine -> P4 Adaptive Box -> P5 Matrix/Structure -> P6 Live Web -> P7 Backtest Validation -> P8 Paper Trading -> future Live Trading only after separate approval and risk controls.
