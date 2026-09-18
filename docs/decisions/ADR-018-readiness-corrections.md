# ADR-018 — Correctness and honest research readiness

Status: implementation decision for FIX1; independent review pending
Sources: [requirements](../requirements.md), [architecture](../architecture.md), [FIX1](../../tasks/FIX1-system-readiness.md)

## Problem

The merged modules do not yet constitute a connected research system. Runtime fixture results, hard-coded backtest prices, permissive risk inputs and in-memory-only evidence must not be presented as completed delivery.

## Decisions

1. Risk uses aware decision-time inputs, finite positive prices/equity, matching symbols, configurable freshness and account-bound reservations. Release is idempotent; day roll resets only daily PnL, never outstanding exposure or lifetime equity peak. Honor policy timezone. Persist risk checkpoint together with paper state, or rebuild both from the same ordered journal.
2. Risk approvals bind account, signal fingerprint, symbol, side, policy, effective time and expiry. Paper refuses mismatches and early/expired fills. Existing proposal retries return the original result without adding a fill. Approved exposure remains reserved until position closure/cancellation, not merely opening a fill.
3. Production bootstrap has no fixture runs. Backtest requires immutable verified event partitions and generates signals through configured strategies. Baseline is explicit past-only close-momentum on completed bars (not a claim of validated profitability); fixed/adaptive modes use the same domain pipeline as observation. Timing/cost/size/configuration are versioned research assumptions.
4. Fill prices come from the first available event at/after the configured decision delay; exits likewise require observed later prices. No future event is exposed to signal generation; signals without exit data remain unfilled rather than receiving invented prices. Entry delay is entry_time minus decision_time.
5. Run identity includes verified dataset content, full config and signal content. Reject missing/changed partitions; normalize Decimal/timestamp serialization. Data corrections create new content identity.
6. Research processing is an ordered, symbol-isolated pipeline through existing P&F/adaptive/matrix/structure/regime/signal modules. Explicit configuration is required; no implicit OX mapping or production tuning values. Durable accepted-input journals rebuild domain state after restart. PostgreSQL remains the target persistence layer; a local SQLite implementation supports offline operation/testing with its backend clearly exposed.
7. Local API/UI reflects only real initialized engine output, verified stored runs and paper sessions. No engine-ready defaults; no fake running paper state. Fixture utilities remain explicitly test/demo-only.
8. Quality distinguishes transport liveness from verified completeness. A live quote is not proof of complete tick capture. Bound operational history. Regime hysteresis uses separate entry/exit bands for volatility and slope, so a previous high-volatility regime can exit when conditions calm.
9. Local-only network boundary remains mandatory. PostgreSQL restore, encrypted remote access, capacity targets and independent security review require actual environmental evidence, not migration-file existence or repeated fixture hashing. Deliver executable local recovery and integration checks with explicit skips for unavailable infrastructure.

## Compatibility

Historical P1–P4 records and requirements/architecture are not rewritten. Backtest callers must supply actual events; unsafe approvals without binding metadata fail closed. Policy/engine versions change for corrected semantics. Historical fixture results are not migrated into real research history.

## Delivery clarification

Schema 2 WebSocket envelopes carry complete authoritative research snapshots including the FR-07 data channels. This replaces per-channel delta framing; reconnect receives current state. [Runtime setup and unresolved release gates](../research-runtime.md) distinguish implemented local research from required production/remote evidence. SQLite fallback, per-signal trade accounting and conservative paper reservation retention do not satisfy every PostgreSQL, portfolio or production requirement. Independent review remains pending.
