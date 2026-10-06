# P&F Baseline v1 — Specification (DRAFT, frozen at bc1712f)

Track: Trading Edge Validation, EDGE-1. Base: `origin/main` bc1712f. Branch: `claude/edge-validation-v1`.
Status: **spec reconstructed from code; numeric production config values not yet pinned** (see Open items).
Scope: research/backtest only. No production change, no order path.

## 1. Decision pipeline (as implemented)

`NormalizedPriceEvent` -> per-resolution `AdaptivePnfRunner` (fixed or ATR box) -> `MatrixEngine`
-> `StructureEngine` (pivots, S/R levels) + `RegimeSnapshot` -> `SignalEngine.evaluate()`
-> `ResearchSignal` (action BUY/SELL, evidence, entry zone, invalidation, TP1/TP2).
Source: `packages/nexora/research/pipeline.py`, `packages/nexora/signals/engine.py`.

## 2. Signal rules (SignalEngine, SignalConfig defaults)

Evidence points: pnf_reversal 20, structure 25, support_resistance 20, matrix 20, regime 15,
pattern_confirmation +10, pattern_conflict_penalty -15.
Score = |buy_points - sell_points| clamped 0..100.
Action = WAIT unless ALL hold: gap |buy-sell| >= `action_gap_min` (8); score > `wait_score_max` (49);
score >= `action_score_min` (65). Otherwise BUY if buy>sell else SELL.
Forced WAIT: cooldown active (`cooldown_events`), no structure pivots, matrix alignment
"unavailable", regime "unknown", inputs confirmed after decision time ("future_inputs" guard),
matrix alignment "mixed", regime in {range, high_volatility} (score capped at 49).
Trade setup (`_build_trade_setup`): entry zone = latest transition `to_price` +/- 0.5 x effective box;
invalidation = nearest support (BUY) / resistance (SELL), else latest low/high pivot; setup rejected
if invalidation is on the wrong side of the zone; TP1 = 1.5R, TP2 = 2.5R from reference price.
Signals expire after `expiry_events`.

## 3. Behaviour that does NOT exist in the baseline (must not be invented)

No position sizing, no trailing stop, no session/time filter, no spread filter inside the signal engine.
Entry Readiness (`packages/nexora/entry_readiness`) and trendline/pattern engines are evidence/shadow
consumers; their influence on BUY/SELL is only through the pattern +/- points above.

## 4. Backtest-fidelity gap (important)

`BacktestRunner._to_trade` (packages/nexora/backtest/runner.py) exits at a **fixed time delay**
(`exit_delay_seconds`) and uses flat per-unit cost; it does **not** apply the signal's own invalidation
(SL) or TP1/TP2. Results from it therefore measure "signal followed by N-second hold", not the
strategy's stated trade plan. EDGE-2 must add an SL/TP-faithful exit mode **as a new mode, leaving
this one intact**. The runner's `baseline` mode is a naive 2-bar close-momentum control, not P&F; it is
useful only as a null/benchmark comparator.

## 5. Open items blocking a full freeze

1. Production values for box size/ATR params, resolutions, `cooldown_events`, `expiry_events`,
   `stale_after_events`, regime thresholds (ADR-010) must be pinned from the config actually used in
   PROD observation; not read here.
2. Bar/tick source and timeframe for replay — see EDGE-3 data report.
