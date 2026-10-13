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
* **Exit-side quotes.** Stops, targets and time stops are evaluated on the side that fills the
  exit: a long sells at the bid, a short buys at the ask. The bars' quote side is declared in
  `Provenance.ohlc_basis` (hashed into the manifest; `NormalizedPriceEvent.price_source` cannot be
  used because for bars it only names which OHLC field became `price`). A long needs `bid` bars and
  a short needs `ask` bars; any other combination, or an undeclared basis, is refused. One dataset
  therefore runs one side; a BUY+SELL study needs a bid series and an ask series. Approximating the
  missing side (e.g. shifting by an observed spread) is an open Quant decision and not done.
* The entry price is the bar open on the declared basis; the half spread to the other side is
  charged as a cost, not as a price shift. The quote timing of a bar's `bid`/`ask` inside the bar is
  undeclared and exit-side spread widening is not modelled (both recorded in every run's notes).
* `CostModel.currency` must equal the dataset's price `units`: gross PnL is in price units, and no
  conversion to an account currency (contract size, tick value) exists.

## Run identity and dataset binding

`run_exit_mode` takes the full event tuple plus its `EdgeDatasetManifest` and the expected
manifest hash, and re-verifies hashes, integrity and the quality report before using any bar. The
run records `manifest_hash`, `statistical_evidence_eligible`, the segment, the split-plan hash and
any holdout access. With `split` + `segment` only that segment's *completed* bars are used (a bar
finishing after the segment ends is excluded) and every plan must decide inside the segment;
without them the run is labelled `unsplit` and is not valid out-of-sample evidence.

**Pre-registration.** `build_edge_manifest(..., split_plan=...)` stores the plan hash in the manifest
(and so in `manifest_hash`, which comes from an independent record). The plan must cover every
event and have a non-empty holdout. A run over a manifest with a registered plan must use exactly
that plan (`split_plan_hash_mismatch` otherwise) and can never be unsplit
(`registered_split_requires_split_run`); a manifest without one can never be split
(`split_not_registered_in_manifest`). Walk-forward evaluation runs through
`run_exit_mode(split=..., walk_forward=(window, "train" | "test"))`; `window_bounds` checks that the
window lies in `[plan.start, validation_end)` and keeps at least the plan's purge.

This closes the omission and substitution bypasses inside one manifest. It cannot stop someone
building a second manifest over the same events with no plan; that is visible only because the
two manifests have different hashes and the independent record names one of them.

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

## Hardening Round 2 (PR #79)

| Finding | Resolution |
|---|---|
| P0 holdout segment bypass | `SEGMENTS` is a closed vocabulary; `validate_segment` accepts only an exact `str` in it. Every entry point (`bounds`, `select_segment`, `select_completed_segment`, `run_exit_mode`) validates first; "Holdout", "oos", "", padded or non-str values raise `invalid_segment` and read nothing. |
| P0 dataset policy not re-applied | `apply_dataset_policy` is the single policy used by build AND `verify_edge_dataset` (blocking issues, acceptable/absent accepted issues, UTC, durations, real_market attestation, sorted accepted state, status match, report match). Forged manifests that used to verify now fail closed. |
| P0 evidence authorization | `evidence.py` separates integrity / provenance / holdout authorization / eligibility. Provenance and authorization are constant False (no independent mechanism exists, none invented); eligibility is therefore always False. Labels, adapter refs, `HoldoutUnlock` and the in-memory log are declarations only (`authorization_verified=False`). |
| P1 sub-second purge hash | plan and policy hashes use integer microseconds. |
| P1 straddling bars | `select_completed_segment`: an item belongs to a segment only if its whole [open, available] interval does. |
| P1 malformed SplitPlan | type validation raises `SplitError`. |
| P1 OHLC / quote validity | `invalid_ohlc` and non-finite/non-positive quotes block datasets and runs; `bar_received_before_close`. |
| P1 side / action | `invalid_side`, `signal_action_side_mismatch`, `signal_not_actionable`. |
| P1 one-shot iterators | inputs are materialised once. |
| P1 feed-latency concurrency | `ExitPolicy.bar_interval` (required); holding/overlap/swap use bar close, not `received_at`. |

API changes: `ExitPolicy(version, concurrency, bar_interval, ...)`; manifest durations are
`*_us`; `EdgeExitRun.gates` replaces the boolean eligibility; `run_exit_mode` raises
`conflicting_same_time_signals` under `one_position`.

Still open and NOT decided here (Quant): same-time BUY/SELL tie-break (the run fails closed),
open trades at a segment boundary (excluded and flagged), swap convention, target-touch fill
assumption, overlapping-exposure semantics. `select_segment`'s own validation is redundant with
`bounds()` by design (defence in depth).

## Hardening Round 3 (PR #79, independent review by Agent B)

Regression tests in `tests/test_edge_hardening_r3.py`; each finding was first reproduced against
`ec54201`.

| Finding | Was | Now |
|---|---|---|
| B-1 MAJOR | short stops/targets evaluated on bid OHLC: short stops fired late, targets early | `Provenance.ohlc_basis` declared and hashed; exits evaluated only on exit-side bars (long=bid, short=ask); other combinations and undeclared basis refused |
| B-2 MAJOR | holdout readable by running unsplit or with a second plan | split plan pre-registered in the manifest; runs must use exactly it; unsplit forbidden once registered |
| B-3 MAJOR | parallel split / cost / run contracts with no ADR | **not fixable in code: Architect/Rin decision required** (below) |
| B-4 | two bars for the same period passed the audit | counted as `duplicate` (blocking) |
| B-5 | bars closer than `bar_interval` accepted | `bars_overlap_interval` |
| B-6 | mixed `price_source` / `units` passed the audit | new blocking code `mixed_price_basis_or_units` |
| B-7 | cost currency not reconciled with price units | `cost_units_mismatch` unless `CostModel.currency` equals the dataset units |
| B-8 | open-at-end trades only listed | `EdgeExitRun.open_at_end_count`; still excluded (Quant decision open) |
| B-9 | walk-forward purge defaulted to 0; windows not runnable | purge defaults to the plan purge and may not be smaller; windows runnable via `walk_forward=` |
| B-10 | `evidence_gates(dataset_integrity_verified=True)` self-asserted | `evidence_gates()` takes no argument and reports unverified; `verified_evidence_gates()` runs the verification |
| B-12 | quote timing undeclared | recorded in every run's notes |

API changes: `simulate_exit(..., ohlc_basis=...)` (required keyword); `Provenance.ohlc_basis`;
`build_edge_manifest(..., split_plan=...)`; `EdgeDatasetManifest.split_plan_hash`;
`run_exit_mode(..., walk_forward=...)`; `EdgeExitRun.open_at_end_count`;
`walk_forward_windows(purge=None)`; `EdgeDatasetManifest.evidence_gates()` has no parameter.

### Architecture decision required (B-3, not decided here)

`edge/*` adds its own split vocabulary (`train | validation | holdout`), cost model (`CostModel`
with unknown/unavailable states) and run identity (`EdgeExitRun.run_id`). The frozen ADR-014
contract is `BacktestConfig.split: Literal["train", "eval"]` with `CostPolicy`, and ADR-030 reuses
that split concept (Decision G9 pre-registration). AGENTS.md section 3 forbids incompatible versions
of a shared contract. Options for the Architect/Rin:

1. Record `edge/*` as a separate research-only contract with no consumers, and state the mapping
   (`train` = ADR-014 `train`; `validation` + `holdout` together = ADR-014 `eval`, with `holdout`
   the pre-registered final segment).
2. Extend ADR-014 (`split` gains `validation` / `holdout`; `CostPolicy` gains explicit
   unknown/unavailable states) and have `edge/*` use the extended contract.
3. Keep `edge/*` unmerged until VALID-1 / Backtest Lab consumers are designed.

Until a decision exists, nothing outside `nexora.edge` may consume these types.

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
* **QUANT DECISION REQUIRED**: how to evaluate the side the bar basis does not cover (second
  OHLC series vs spread shift); open trades at a segment end (B-8).
* **ARCHITECT / RIN DECISION REQUIRED**: relationship of `edge/*` to ADR-014 / ADR-030 (B-3); confirm
  the manifest pre-registration binding (B-2) as the holdout mechanism.
* **DATA REQUIRED**: an approved, non-secret, non-production historical dataset with bid/ask and
  broker capability metadata; none exists in the repo or the cloud session.

## Tests (all synthetic; see `tests/edge_fixtures.py`)

`tests/test_edge_dataset.py`, `test_edge_splits.py`, `test_edge_costs.py`, `test_edge_exits.py`,
`test_edge_hardening.py`, `test_edge_hardening_r2.py`, `test_edge_hardening_r3.py` cover deterministic replay, look-ahead prevention, holdout isolation, walk-forward boundaries,
cost accounting, invalid / missing data and extreme numerics.
