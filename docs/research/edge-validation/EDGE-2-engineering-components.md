# EDGE-2 — Engineering components for edge validation

Track: EDGE-VALIDATION-1 (Issue #77), branch `claude/edge-validation-v1`.
Extends [EDGE-1 Baseline v1 spec](./EDGE-1-baseline-v1-spec.md) (unchanged, still DRAFT).
Scope: research/backtest only. No production change, no order path, no SignalEngine / Risk /
ExecutionGuard / broker change. Package: `packages/nexora/edge/` (new files only; no existing
file edited, `nexora.backtest` and `nexora.validation` are reused, not forked).

## Status in one table

| Question | Status |
|---|---|
| Engineering components (dataset manifest, splits, costs, SL/TP exit mode) | Implemented + tested on **synthetic** fixtures |
| P&F Baseline v1 frozen | **NO** — pinned non-secret config not supplied |
| Real-market dataset | **NONE** — no authorized data in this environment |
| Statistical edge conclusions / final holdout / strategy selection | **NOT RUN, BLOCKED** |

Synthetic-fixture results test code correctness only. They are not evidence of profitability,
and `EdgeDatasetManifest.statistical_evidence_eligible` is `False` for `synthetic_fixture` data.

## Components

| Module | Purpose |
|---|---|
| `edge/dataset.py` | Provenance (`data_class`, source, UTC, tool version, adapter-supplied capability profile), quality audit (duplicate, out-of-order, stale, gap, missing sequence, crossed quote, non-positive price, timestamp anomaly, mixed symbol), manifest + hash verification. Fails closed unless an issue is explicitly accepted in the manifest. Wraps `backtest.datasets.manifest_for/verify_events`. |
| `edge/splits.py` | Half-open chronological train / validation / holdout with optional purge; holdout needs a `HoldoutUnlock` bound to the exact plan hash with named authority and reason, and every read is recorded in a mandatory `HoldoutAccessLog` (repeat reads are flagged); walk-forward windows (rolling or anchored, non-overlapping tests, test after train + purge) take the **plan**, and their range is always `[plan.start, plan.validation_end)`, so they cannot reach the holdout. |
| `edge/costs.py` | Spread / commission / slippage / swap each `known`, `unknown` or `unavailable`. Unknown is never zero; `CostBreakdown.complete` is false and net PnL is reported as *known-cost net* only. Spread can come from observed bid/ask. No broker, symbol or lot rule is encoded. |
| `edge/exits.py` | New SL/TP-aware exit mode on completed OHLC bars. Legacy `BacktestRunner._to_trade` fixed-delay exit is untouched. |

## Exit-mode assumptions (research policy, not the production exit rules)

* Entry at the open of the first bar with `event_time >= decision_time + entry_delay`; no earlier
  bar and nothing after the exit is read (tests prove both).
* Stop and target come from an explicit `TradePlan`. `plan_from_signal` requires the caller to
  name `TP1` or `TP2`; there is no default.
* **Ambiguous bar** (range touches both stop and target, no tick order): resolved by policy,
  default `stop_first` (conservative); the result is flagged `ambiguous`, carries the
  `same_bar_stop_and_target_order_unknown:<policy>` assumption, and keeps both alternative exit
  prices, which `EdgeTrade.sensitivity` turns into gross / known-cost-net per resolution.
  `target_first` exists for sensitivity only and is still flagged.
* Gap through the stop fills at the (worse) open. A bar that opens beyond the target fills at the
  target price (no price improvement) and is **not** ambiguous even if it later trades through
  the stop, because the open prints first. Plans whose stop/target do not bracket the real entry
  price are `rejected_invalid_plan`, not traded. Trades with no exit in the data are
  `open_at_end` and excluded from the ledger.
* `unit_size` is a research multiplier, not a position-sizing rule.
* `exit_time` of an intrabar stop/target is the bar close and is flagged `exit_time_upper_bound`;
  swap derived from it is therefore an upper bound (costs are never credits, so this is the
  conservative direction). Gap and time-stop exits are exact.
* **Concurrency has no default**: `ExitPolicy.concurrency` must be `one_position` (an entry while a
  trade is open is recorded as `skipped_overlap`) or `allow_overlap` (every plan booked, exposure
  can stack). A trade that never closes in the data blocks all later entries. Entry exactly at the
  previous exit time is allowed. Which policy the baseline uses is an open Quant decision.
* A crossed or non-finite entry quote makes that trade's spread *unavailable* (noted in
  `assumptions`) instead of aborting the run.

## Run identity and dataset binding

`run_exit_mode` takes the full event tuple plus its `EdgeDatasetManifest` and the expected
manifest hash, and re-verifies hashes, integrity and the quality report before using any bar. The
run records `manifest_hash`, `statistical_evidence_eligible`, the segment, the split-plan hash and
any holdout access. With `split` + `segment` only that segment's *completed* bars are used (a bar
finishing after the segment ends is excluded) and every plan must decide inside the segment;
without them the run is labelled `unsplit` and is not valid out-of-sample evidence.

## Hardening Round 1 (PR #79)

Fixed after the implementer's adversarial self-audit; each has regression tests in
`tests/test_edge_hardening.py`, and each fix was mutation-checked (reversing it turns a test red).

| Id | Was | Now |
|---|---|---|
| F1 | walk-forward range was caller-supplied and could include the holdout | takes a `SplitPlan`; range fixed to non-holdout |
| F2 | NaN/inf crashed the audit; naive timestamps could crash ordering | counted as `non_finite_value` (blocking); naive timestamps are anomalies and never ordered |
| F3 | `dataset_hash` was a free string | runs are bound to a verified manifest and optional split segment |
| F4 | `real_market` and holdout unlock were pure honour system | real-market requires an adapter capability ref and no synthetic markers; holdout reads need an access log |
| F5 | concurrent identical trades all booked | explicit `concurrency` policy, no default |
| F6 | open-beyond-target bar flagged ambiguous | resolved as target at the open |
| F7 | crossed entry quote aborted the run | spread marked unavailable |
| F8 | intrabar exit time looked exact | flagged as an upper bound |

Limits that remain (not hidden): `real_market` and `HoldoutUnlock` are still *declarations* -
the checks reject obvious mislabelling and make every use visible, but only a reviewer can confirm
the data source and the authority. `HoldoutAccessLog` is in memory; persist `entries` with the
report. The synthetic-marker check is a string heuristic.

## Data-quality note

`max_latency` bounds `received_at - event_time`. For bars `event_time` is the bar open, so allow
the bar length plus a tolerance.

## Unresolved decisions (not decided here)

* **QUANT DECISION REQUIRED**: which target (TP1 vs TP2) the baseline uses; partial closes;
  stop movement; position sizing; time-stop rule; one-position vs overlapping exposure; whether same-bar ambiguity is booked as
  stop-first in the headline result.
* **QUANT DECISION REQUIRED**: pinned production values (box/ATR, resolutions, cooldown, expiry,
  stale, ADR-010 regime thresholds) — see EDGE-1 open items.
* ADR-030 Q-V2–Q-V5 remain open and are neither assumed nor defaulted.
* **DATA REQUIRED**: an approved, non-secret, non-production historical dataset with bid/ask and
  broker capability metadata; none exists in the repo or the cloud session.

## Tests (all synthetic; see `tests/edge_fixtures.py`)

`tests/test_edge_dataset.py`, `test_edge_splits.py`, `test_edge_costs.py`, `test_edge_exits.py`,
`test_edge_hardening.py` cover deterministic replay, look-ahead prevention, holdout isolation, walk-forward boundaries,
cost accounting, invalid / missing data and extreme numerics.
