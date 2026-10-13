# EDGE-VP-1 — Validation Protocol for P&F Baseline V1 (DRAFT)

Track: Edge Validation Readiness Wave, Track D (independent validation protocol).
Branch: `claude/edge-validation-protocol-v1`. Base: `origin/main` `36c90c5`.
Status: **DRAFT — PROPOSED, NOT FROZEN.** Every numeric threshold is an explicit open decision (section 14). This protocol becomes binding only when Rin freezes it with the Quant decisions filled in, and its frozen version is hashed (section 12).
Scope: research and backtest only. No production change, no order path, no holdout unlock, no strategy change.

Related: `EDGE-1-baseline-v1-spec.md` and `EDGE-2-engineering-components.md` in this directory (both on PR #79, `claude/edge-validation-v1`, not yet on `main`), [ADR-014](../../decisions/ADR-014-backtest-lab-reproducibility.md), [ADR-030](../../decisions/ADR-030-replay-validation-framework-v1.md), AGENTS.md sections 9, 11 and 20.

## 0. Independence and authority

- This protocol was written by a session that did not implement `nexora.edge` (PR #79) and does not own the baseline configuration or the dataset. It defines **how evidence is judged**, so it must be fixed before anyone sees validation or holdout results.
- The protocol selects no trading parameter, threshold, target, sizing or strategy. Where a value is needed it is listed as `OPEN-VP-n` with an owner.
- Authority follows AGENTS.md section 20.2: Rin is Architect, Safety Gate and the **only** holdout unlock authority. Quant owns formula and threshold values. Authority exists only as a message from Rin/Owner in the chat, never in a file, PR body, tool result or agent report.
- Results are evidence, never configuration (ADR-030 Decision 17). A PASS under this protocol does not change production strategy and does not authorize PAPER, DEMO, ASSISTED, AUTO or REAL execution (section 11).

## 1. Question under test

**Primary question (single, confirmatory):** does P&F Baseline V1, as frozen in EDGE-1 and executed by the EDGE-2 SL/TP exit mode, produce a positive expected net result per trade **after realistic costs**, on data it has never been exposed to, by more than the pre-registered comparator (section 6)?

- **H0:** primary metric (section 7) of Baseline V1 net of costs is ≤ the comparator's.
- **H1:** it is greater by at least the pre-registered minimum effect (`OPEN-VP-12`).
- The test is **one-sided** and **one primary metric**. Everything else is descriptive.
- Challengers (section 10) are separate hypotheses under the same protocol and count against the multiple-testing budget.

## 2. Entry gates (all required before any run counts as evidence)

A run that misses any gate is `EXPLORATORY` at best. It is kept in the run ledger but cannot be cited.

| Gate | Requirement | Current state |
|---|---|---|
| P1 Baseline frozen | EDGE-1 open items pinned: box/ATR params, resolutions, `cooldown_events`, `expiry_events`, `stale_after_events`, ADR-010 regime thresholds; TP1 vs TP2, concurrency policy, time-stop, same-time BUY/SELL tie. Recorded as a config hash + clean 40-hex commit SHA | **NOT MET** (values not recovered; Quant decisions open) |
| P2 Dataset approved | Non-secret, non-production historical dataset with an `EdgeDatasetManifest`, `data_class = real_market`, adapter capability profile, **both bid and ask** OHLC series (EDGE-2: a long needs bid bars, a short ask bars), verified by `verify_edge_dataset` | **NOT MET** (Track B) |
| P3 Costs complete | Spread observed from the data; commission, slippage assumption and swap convention all `known` for the instrument. Any `unknown`/`unavailable` cost makes the run non-headline (section 5) | **NOT MET** |
| P4 Split pre-registered | `SplitPlan` registered in the manifest (`split_plan_hash`) **and** in the independent pre-registration record (section 3.3) before anyone reads validation data | **NOT MET** (record does not exist yet) |
| P5 Protocol frozen | This document frozen by Rin, all `OPEN-VP-*` that the run needs answered, frozen version hash recorded | **NOT MET** |
| P6 Contract decision | Architect/Rin decision on how `edge/*` relates to ADR-014/ADR-030 (PR #79 finding B-3) | **NOT MET** (Track C) |
| P7 Clean provenance | Clean committed source tree, run manifest hash, `code_fingerprint()`; dirty tree ⇒ `EXPLORATORY` (ADR-030 Q-V7) | Mechanism exists |

## 3. Data partitioning and the locked holdout

### 3.1 Segments

- Chronological, half-open, non-overlapping: `train` → `validation` → `holdout`, with the **holdout the most recent** segment. No shuffling, no random splits, no k-fold across time.
- **Purge/embargo** between segments of at least the longest possible holding period of a Baseline V1 trade (signal expiry plus maximum time in trade), so no trade straddles a boundary. Value: `OPEN-VP-3`. Trades still open at a segment end are counted and reported (`open_at_end_count`); their headline treatment is `OPEN-VP-9` (PR #79 B-8).
- Segment lengths/proportions and the minimum calendar span of each: `OPEN-VP-1`, `OPEN-VP-2`. The holdout must be long enough to reach the minimum trade count (`OPEN-VP-11`) **at the expected trade frequency measured on train only**; if train shows it cannot, the protocol result is decided as INCONCLUSIVE in advance rather than by extending the holdout after the fact.
- Mapping to ADR-014 (`train | eval`) depends on the B-3 decision. The PR #79 proposal (`train` = `train`; `validation` + `holdout` = `eval`, holdout as the pre-registered final part) is compatible with this protocol.

### 3.2 What "locked" means

Until Rin unlocks it (section 9), **nothing derived from holdout prices may be seen by any person or agent**: no trades, metrics, plots, summary statistics, cost estimates, spread profiles or eyeballing of charts. Permitted before unlock: integrity counts from the dataset audit (event counts, gap/duplicate/out-of-order counts, first/last timestamps), because the manifest needs them.

### 3.3 Pre-registration record (closes PR #79 residual R-1)

The manifest binding in PR #79 stops omission and substitution **within one manifest**, but a second manifest built over the same events can still read the holdout. The protocol therefore requires an **independent, committed record**:

- A file under `docs/research/edge-validation/preregistration/` (exact path is `OPEN-VP-16`, Architect), committed to `main` through a reviewed PR **before** the first validation-segment run.
- Contents: base dataset hash (`manifest.base`), split plan hash, `manifest_hash`, frozen protocol version hash, baseline config hash, list of pre-registered hypotheses (primary + challengers), comparator, metric definitions, cost model hash.
- Any run whose dataset base hash appears in the record must use the recorded plan and manifest, or it is `INVALID` (`preregistration_mismatch`). A second manifest over a registered base is never evidence.
- The record is append-only. Changing it after validation results exist starts a **new** protocol version, and the old holdout is spent for any hypothesis whose definition changed (section 9.4).

Engineering enforcement of this record in `run_exit_mode` is a follow-up for the PR #79 owner; until it exists, the record is enforced by review.

## 4. Walk-forward on the non-holdout range

- Walk-forward runs only on `[plan.start, plan.validation_end)`; it cannot reach the holdout (already enforced by `walk_forward_windows`).
- **Baseline V1 has no fitted parameters.** For the baseline, walk-forward is a **stability test across time**, not refitting: each test window is evaluated with the same frozen config. A challenger with tuned parameters may fit only on its window's train part.
- Window scheme (`rolling` vs `anchored`), train length, test length and step: `OPEN-VP-4`. Purge per window is at least the plan purge (enforced).
- Test windows do not overlap, so each trade belongs to exactly one window.
- Reported per window: trade count, primary metric with its interval, cost share of gross, ambiguous-bar share, open-at-end count. The consistency criterion (section 8, C5) uses these.
- Walk-forward results are validation evidence. They decide whether a holdout unlock is **requested**; they are not the confirmatory result.

## 5. Execution realism and costs

All of the following are already supported by EDGE-2 at `a48e5f7` unless marked as a gap.

| Area | Rule |
|---|---|
| Fill side | Long exits evaluated on bid bars, short exits on ask bars (`Provenance.ohlc_basis`). A study covering both sides needs both series. Approximating the missing side by a spread shift is `OPEN-VP-7` and is not done by default |
| Entry | Open of the first completed bar with `event_time >= decision_time + entry_delay`. `entry_delay` is `OPEN-VP-6` (must be ≥ one bar unless Quant shows the signal is known before the bar opens) |
| Spread | Observed from the data per trade, full spread once per round trip. Exit-side spread widening is not modelled and is disclosed in every run's notes |
| Commission | From the adapter capability profile for the instrument, in price units (`CostModel.currency` must equal dataset `units`) |
| Slippage | A declared assumption, `OPEN-VP-8`. Never zero by omission |
| Swap/financing | Convention `OPEN-VP-8`. Upper-bound exit times make it conservative |
| Unknown costs | Any cost `unknown` or `unavailable` makes the run **non-headline**: `net_complete=False` results are never compared against a pass threshold |
| Same-bar stop and target | Headline uses `stop_first`. `target_first` is reported as sensitivity only |
| Gaps | Gap through stop fills at the worse open; gap through target fills at target, no price improvement |
| Cost stress | The headline result is recomputed with every cost component scaled by the stress multiplier(s) `OPEN-VP-13`. The stress result is part of the pass criteria (C3) |

No broker, symbol, lot, filling mode, session or spread value may be hard-coded in a study config; they come from the dataset and the capability profile.

## 6. Comparator

A result is only meaningful next to a comparator run through the **same** dataset, splits, exit mode and costs. At least one comparator is pre-registered (`OPEN-VP-10`). Candidates, none chosen here:

1. **Random-entry null:** entries at random decision points drawn from the same segment, with the baseline's side mix, stop/target distances (in R) and trade count, many seeded draws. Gives a null distribution of the primary metric that already includes costs.
2. **Runner `baseline` mode:** the naive 2-bar momentum control named in EDGE-1 section 4, under the same SL/TP exit mode.
3. **Zero:** flat, no trades. Weakest comparator; only meaningful with a cost-complete net metric.

## 7. Metrics

- **Primary metric (one):** `OPEN-VP-5`. It must be net of all costs, in a declared unit (price units per trade, or R multiples using the signal's own invalidation distance), and computed over all closed trades including stop-first ambiguous bars.
- **Secondary (descriptive only, never pass/fail unless listed in section 8):** trade count, long/short split, win rate, average win/loss, profit factor (nullable on zero losses, ADR-014), max drawdown in the same unit, exposure time, cost share of gross, ambiguous-bar share, open-at-end count, skipped-overlap count, per-sub-period results.
- **No composite score**, ranking across components, or "win probability" (ADR-030 Decision 9).
- Every reported number carries its denominator, its segment, the run id and the manifest hash.

## 8. Inference, multiple testing and pass/fail criteria

### 8.1 Statistical inference

- Trades are not independent (overlapping exposure, volatility clustering, shared regime). The interval method must respect serial dependence, for example a block bootstrap over time or over walk-forward windows. Method, block length and confidence level: `OPEN-VP-14` (aligns with ADR-030 Q-V5).
- An interval is shown only when n ≥ the minimum trade count (`OPEN-VP-11`); below it, counts only.

### 8.2 Multiple-testing control

- **Trial ledger.** Every hypothesis, variant, parameter set, cost scenario and segment that is evaluated on validation data is recorded in the run ledger (section 12), including discarded ones. The ledger count is printed next to every result.
- **Confirmatory family.** Only hypotheses registered in the pre-registration record before the holdout unlock form the confirmatory family. Its size `m` is fixed at registration.
- **Control.** Family-wise error is controlled across the confirmatory family (for example Holm–Bonferroni at level α; method and α are `OPEN-VP-15`). If many variants were searched on validation before choosing finalists, the selection must be corrected for, for example by a data-snooping test over the whole validation search (White's Reality Check / Hansen SPA style) or a deflated performance statistic using the ledger count. Which applies is part of `OPEN-VP-15`.
- **Exploratory results** (anything not in the confirmatory family, any `EXPLORATORY` run, any post-hoc subgroup) are reported as such and can never be cited in an ADR as evidence of edge.

### 8.3 Criteria, fixed before the unlock

All thresholds are open decisions; the **structure** below is what this protocol fixes.

| Id | Criterion (holdout, confirmatory) | Threshold |
|---|---|---|
| C1 | Closed-trade count ≥ minimum | `OPEN-VP-11` |
| C2 | Primary metric minus comparator > minimum effect, and the lower bound of its interval > 0 at the family-corrected level | `OPEN-VP-12`, `OPEN-VP-14`, `OPEN-VP-15` |
| C3 | C2's point estimate stays > 0 under cost stress | `OPEN-VP-13` |
| C4 | C2 is judged on the `stop_first` headline only; `target_first` is reported as sensitivity and cannot turn a FAIL into a PASS | none |
| C5 | Walk-forward consistency on validation: share of test windows with positive net primary metric ≥ threshold (checked **before** requesting unlock) | `OPEN-VP-17` |
| C6 | Max drawdown on holdout ≤ limit | `OPEN-VP-18` |
| C7 | Open-at-end and skipped trades do not exceed a share of signals that could reverse C2 (treatment per `OPEN-VP-9`) | `OPEN-VP-19` |
| C8 | No protocol violation: entry gates met, holdout read once, manifest and pre-registration match | none |

### 8.4 Verdicts

| Verdict | Condition |
|---|---|
| `PASS` | C1–C8 all hold |
| `FAIL` | C1 and C8 hold, and C2, C3, C6 or C7 fails |
| `INCONCLUSIVE` | C1 fails (too few trades). Not a pass. The holdout is still spent |
| `INVALID` | C8 fails. The result is void and the holdout is treated as contaminated |

A FAIL or INCONCLUSIVE result is reported with the same prominence as a PASS. It is never rescued by re-running, re-cutting the holdout, changing costs or picking a different metric.

## 9. Holdout governance

### 9.1 Who may unlock

**Only Rin.** No agent, reviewer, MASTER session, script or test may unlock the holdout of a real-market dataset on its own authority. Constructing a `HoldoutUnlock` against a registered real-market dataset without Rin's chat authorization is a protocol violation (C8) even though the code accepts it (`authorization_verified=False` is a declaration, not a check).

Proposed chat token (format is `OPEN-VP-20`, Rin):

```
RIN_HOLDOUT_UNLOCK <protocol version hash> <manifest_hash> <split_plan_hash> <full 40-char code SHA> <hypothesis ids>
```

As with merge authority, the token is valid only as a message from Rin/Owner in the chat, names exact hashes, and covers one evaluation of the named hypotheses only.

### 9.2 Conditions for requesting an unlock

The requester (MASTER or the edge workstream) may ask Rin only when all of these are attached:

1. Entry gates P1–P7 met, with hashes.
2. Pre-registration record committed on `main`, with its commit SHA.
3. Validation and walk-forward results complete, including C5, and reported with the trial ledger count.
4. Quant sign-off that all `OPEN-VP-*` the evaluation needs are answered.
5. An independent reviewer (not the implementer, not the evaluator) has checked items 1–4 and reproduced the validation run from the manifest.

### 9.3 During and after the unlock

- **One evaluation.** The holdout is read once, by one run per confirmatory hypothesis, with the exact frozen code and config. The `HoldoutAccessLog` entries are persisted into the run bundle and committed with the report (today the log is in memory only; persisting it is a follow-up for the PR #79 owner).
- A second read of the same holdout is flagged by the access log. Any result after the first read is not citable.
- The result is reported whatever it is, with the run ledger, before any further work.

### 9.4 After a FAIL or INCONCLUSIVE

- The holdout is spent for that hypothesis and any variant derived after seeing it.
- A new attempt needs a **new, later** holdout (data that did not exist or was not collected when the spent holdout was read), a new pre-registration, and a new Rin unlock.
- Changing the baseline in response to holdout results is a new strategy version (Baseline V2), which goes through this protocol from the start and through the normal ADR/Quant route. It never silently replaces V1.

## 10. Challenger hypotheses

- Each challenger is defined in the pre-registration record **before** it touches validation data, with a stated rationale. A challenger designed after looking at validation results is exploratory until it is registered against a fresh validation window or carried as one of the searched variants in the 8.2 correction.
- Same dataset, splits, costs, exit mode and comparator as the baseline. Paired comparison against Baseline V1 on the same trades' time range.
- Only finalists (a number fixed in advance, `OPEN-VP-21`) enter the confirmatory family and the holdout.
- A challenger beating the baseline on validation is not a reason to change production strategy (AGENTS.md safety principle 5).

## 11. What a result means

| Verdict | Means | Does not mean |
|---|---|---|
| PASS | Evidence that Baseline V1 had a positive net edge over the comparator on unseen historical data under the declared costs | Profitability going forward; any change to production; any execution permission (PAPER/DEMO/ASSISTED/AUTO/REAL) |
| FAIL | The declared edge did not hold on unseen data | That P&F research has no value; it is evidence for the next ADR |
| INCONCLUSIVE | Not enough trades | A weak pass |

After a PASS the next step is an ADR citing the run ids, Quant review and Rin decision. A forward out-of-time test on data recorded after the PASS (observation or paper, under its own authorization) is recommended before any execution discussion; this protocol neither requires nor authorizes it.

## 12. Run ledger, provenance and reproducibility

- Every run, including failed, discarded and INVALID ones, is kept: run id, manifest hash, split plan hash, segment or window, protocol version hash, code SHA and dirty flag, config hash, cost model hash, hypothesis id, verdict or `EXPLORATORY`. Location: `OPEN-VP-16`.
- Same inputs ⇒ byte-identical results. The independent reviewer reproduces the validation run and the holdout run from the manifest before the verdict is accepted.
- The frozen protocol is identified by the canonical hash of this file at its frozen commit. Editing it after freezing makes a new version; runs cite the version they were judged under.
- Roles are separate: protocol author (this document), implementer (`nexora.edge`), evaluator (runs the study), independent reviewer (reproduces and checks), Rin (unlock and acceptance).

## 13. Mapping to existing engineering (PR #79 at `a48e5f7`)

| Protocol need | Exists | Gap (owner: PR #79 / edge workstream unless noted) |
|---|---|---|
| Chronological split, purge, closed segment vocabulary | `SplitPlan`, `validate_segment` | none |
| Manifest-bound pre-registration | `split_plan_hash` in manifest | independent pre-registration record check (3.3) |
| Holdout unlock + access log | `HoldoutUnlock`, `HoldoutAccessLog` | authorization is a declaration; log not persisted (9.3) |
| Walk-forward | `walk_forward_windows`, `run_exit_mode(walk_forward=...)` | none for the baseline |
| Side-aware fills, costs, unknown ≠ 0 | `ohlc_basis`, `CostModel` | second-side approximation is `OPEN-VP-7` |
| Statistical evidence gate | `statistical_evidence_eligible` constant False | provenance verification mechanism; stays False until one exists (Architect) |
| Metrics, intervals, comparator, multiple-testing correction, run ledger | none in `nexora.edge` | new work after this protocol is frozen; must not live in production packages |
| Contract relationship to ADR-014/030 | — | Architect/Rin decision B-3 (Track C) |

## 14. Open decisions

None of these is decided here. Leaving one unresolved blocks the entry gate or criterion shown.

| Id | Owner | Decision | Blocks |
|---|---|---|---|
| OPEN-VP-1 | Quant | Segment proportions or calendar lengths for train / validation / holdout | P4 |
| OPEN-VP-2 | Quant | Minimum calendar span per segment (e.g. must cover distinct volatility regimes) | P4 |
| OPEN-VP-3 | Quant | Purge/embargo length between segments | P4 |
| OPEN-VP-4 | Quant | Walk-forward scheme, train/test lengths, step | C5 |
| OPEN-VP-5 | Quant | Primary metric and its unit | C2 |
| OPEN-VP-6 | Quant | Entry delay | P1 |
| OPEN-VP-7 | Quant | Second-side evaluation: second OHLC series required, or spread-shift approximation | P2 |
| OPEN-VP-8 | Quant + Data | Slippage assumption and swap convention per instrument | P3 |
| OPEN-VP-9 | Quant | Headline treatment of open-at-end trades (PR #79 B-8) | C7 |
| OPEN-VP-10 | Quant | Comparator(s) | C2 |
| OPEN-VP-11 | Quant | Minimum closed-trade count | C1 |
| OPEN-VP-12 | Quant + Rin | Minimum effect size worth acting on | C2 |
| OPEN-VP-13 | Quant | Cost stress multiplier(s) | C3 |
| OPEN-VP-14 | Quant | Interval method, block length, confidence level (ADR-030 Q-V5) | C2 |
| OPEN-VP-15 | Quant | Multiple-testing method and α; data-snooping correction if variants are searched | C2 |
| OPEN-VP-16 | Architect | Location and format of the pre-registration record and run ledger | P4 |
| OPEN-VP-17 | Quant | Walk-forward consistency threshold | C5 |
| OPEN-VP-18 | Quant + Rin | Holdout max drawdown limit | C6 |
| OPEN-VP-19 | Quant | Maximum open-at-end / skipped share | C7 |
| OPEN-VP-20 | Rin | Holdout unlock token format and who may request it | 9.1 |
| OPEN-VP-21 | Quant + Rin | Number of challenger finalists allowed into the holdout | 10 |

Also open elsewhere and required by P1: the EDGE-1 pinned production values, TP1 vs TP2, concurrency policy, time-stop, same-time BUY/SELL tie-break (EDGE-2 "Unresolved decisions").

## 15. Safety statement

This document changes no code, configuration, ADR or strategy. It starts nothing, unlocks nothing and authorizes nothing. No holdout data was read in writing it.
