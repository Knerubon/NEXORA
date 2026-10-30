# NEXORA Trading Edge Proof — authorized offline preparation

Date: 2026-10-30 (Asia/Bangkok). Issue: #77.
Verdict: **BLOCKED**. Positive net trading edge is **not proven**. This is not evidence of an unprofitable strategy either. No historical profit calculation was run.

## Scope and provenance

Audited implementation: `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87` (main).
Workstream: `codex/edge-proof-offline-20261030-v1`; isolated cloud checkout; owner root session.
Only new report/manifest/tests paths belong to this workstream. Existing edge implementations and PR #94/#95/#96 are not edited. No strategy parameters, production code, ADR status or gates are changed. No market payload, holdout, MT5, broker, orders or runtime service was accessed. Git tracked source/documentation was cloned; dataset-looking filenames were inventoried without opening payloads.

This package pins **source evidence**, not an approved executable trading configuration. An immutable Baseline V1 trading specification cannot truthfully be completed while trading decisions remain unresolved. `BASELINE-V1-SOURCE-PIN.json` records those unresolved fields explicitly; its SHA-256 is a byte-integrity fingerprint, not a freeze authorization. Never pass it to a trading runner as configuration.

Existing work remains authoritative in its own scope:

- PR #79 source `196a962fd6fe2be41e0acfd70f6f7ec3b7a240db`: separate draft edge research contracts, not main behavior.
- PR #81 protocol `49bd2d6c9d8cf11c0e78a1e209ccc0cbb5eb4282`: `EDGE-VP-1-validation-protocol.md`, DRAFT / NOT FROZEN, 21 OPEN-VP decisions.
- Readiness master branch `6c23616e9a06cce234e1bcbc20eca1563e7d1f77`: `BASELINE-V1-CANDIDATE.md` and freeze proposal already mark real baseline values unresolved.
- Market-history draft `d2e8ee9ef4f6eb438dc79c5e04ad43ed46de764d` (#99): custody and provenance engineering, not approved historical evidence.

The present package supplements these drafts; it neither supersedes them nor fixes their findings. Main findings below must not be misattributed to unmerged `nexora.edge` code.

## Actual implemented trading decisions

Paths below are relative to the audited SHA. Defaults describe implementation, **not approved trading parameters**.

| Stage | Implemented behavior and evidence |
|---|---|
| P&F | `pnf/engine.py:54`: quantize Decimal price; first event seeds grid; first full box creates X/O. Whole-box extensions are floored. Reversal occurs inclusively at extreme minus/plus box×reversal count, at the triggering event time; one multi-box event produces one transition. Required box/reversal/source/precision/version: `pnf/models.py:29`. |
| Structure | `structure/engine.py:61`: strict center extreme of three transition to-prices. Occurrence and right-neighbor confirmation are distinct. Levels confirmed at :89, invalidated on strict break at :111. These are transition pivots, not automatically classic P&F column extrema. |
| Matrix | `matrix/engine.py`: all resolutions require fresh transitions; all X bullish, all O bearish, otherwise mixed. Staleness follows source sequence distance. |
| Regime | `market_regime/engine.py:24`: unknown without suitable matrix/pivots. Width threshold first yields high volatility; endpoint slope threshold next yields trend; otherwise range; hysteresis depends on prior regime. |
| Signal evidence | `signals/engine.py:245`: default P&F/structure/SR/matrix/regime weights 20/25/20/20/15. Structure direction uses three-pivot equal-low/high or higher-low/lower-high tests, then two-pivot progression/fallback (:670). SR uses reached confirmed support/resistance, otherwise transition direction (:776). |
| Pattern scoring | Legacy `_patterns` (:852) detects three-pivot double bottom/top, six-pivot H&S/inverse or triangle, four-pivot failed breakout/breakdown. Absolute tolerance default .8. Dominant-side agreement +10 capped100, conflict −15 (:439). Pre-pattern ties favor BUY. |
| BUY/SELL/WAIT | `signals/engine.py:52`: cooldown or unavailable evidence -> WAIT; :75 rejects future evidence timestamps. :522 score=clamp(abs(buy−sell),0,100); zero evidence, gap<8, score≤49 or score<65 -> WAIT; otherwise stronger side wins. :101 mixed matrix, range or high volatility force WAIT. |
| Trade geometry | :536 reference latest transition; zone ±.5 box by default. BUY invalidation confirmed support or low pivot below zone low; SELL resistance/high pivot above zone high. Invalid/missing risk -> WAIT. Risk measured from near edge of zone; targets reference ±1.5R /2.5R by default. Geometry is not fill policy. |
| Lifecycle | :230 event-count cooldown inclusive through configured gap; expiry after configured event count. :965 dedup active signal by side/source references/reasons. |
| Entry Readiness | `entry_readiness/derive.py:34`: WAIT -> NOT_READY; BUY uses bullish support line, SELL bearish resistance. No aligned line/active/retest_failed -> READY; retesting -> DEVELOPING; broken/retest_held -> BLOCKED. Does not create a trade from a pattern. |
| Risk | `risk/engine.py:52`: provenance/time/currency/quality/price/account, kill switch, minimum stop, exposure, projected daily loss and absolute drawdown checks. Size=floor-to-step(min(requested,max-risk/stop-distance)); reserves distance×size. Required budgets; no approved default capital. No stop execution. |

PatternEngine V1 (`features.py`, ADR-024) is DISABLED by default and capped at SHADOW. Its parallel output is not the signal scoring source; `research/pipeline.py:121` does not pass it into evaluate. Do not conflate legacy double-top pivots with classic P&F breakout semantics.

## Backtest fidelity blockers reproduced or identified

1. **Wrong baseline identity:** `backtest/runner.py:121–151` mode `baseline` is close momentum, not P&F. `docs/examples/baseline-config.json` has that mode and `example-only-v2`; it cannot supply missing approved values.
2. **Incomplete strategy mapping:** P&F modes collect raw ResearchSignal (:153–167). Trades do not consult Entry Readiness, Risk, invalidation or targets (:170–209). Whether the intended baseline includes these filters requires a decision.
3. **Stop fidelity gap:** stop_distance is validated (:55) but unused by `_to_trade`; fixed elapsed-time exit (:179–200) can ignore a traversed stop. Tests retain this desired stop invariant visibly. ADR-014 currently specifies fixed holding; this is a fidelity blocker for a stop-based strategy, not a demonstrated violation of the accepted fixed-hold contract. No stop policy is accepted by this test.
4. **Execution/cost gaps:** selected event.price is used on both sides. Fees=(spread+commission+slippage)×size (:187); no side-aware bid/ask fills, varying spread, swaps, contract multiplier or FX conversion. Synthetic cost algebra can validate arithmetic, not market realism.
5. **Portfolio gaps:** each signal becomes an independent overlapping trade. No event-time capital/margin/netting model. Drawdown is accumulated in decision order (:239), not full marked-to-market portfolio equity.
6. **Statistical/provenance gaps:** train/eval is a label; no enforceable holdout registry in this runner. No CI/power/robustness metric. Environment label is a string, not interpreter attestation. Hashes do not independently prove signal causality or unseen history.
7. **Direct P&F restore risk:** `_MutableSymbolState.from_public` restores dedup identities only from transitions; seed/no-transition identities are absent. Scope is direct PnfEngine; ResearchPipeline has another guard. See synthetic results before interpreting restart claims.

## Baseline V1 freeze decision record

All remain UNRESOLVED until an authorized evidence/decision record resolves them:

- Exact strategy boundary (raw Signal versus Readiness+Risk), fixed/ATR, approved observation config, resolutions, box/reversal/source/precision, feature lifecycle and warmup/seed rules.
- Non-secret clean source SHA and actual config attestation; complete resolved defaults and feature document; information cutoff of every person/agent influencing parameters.
- Market/limit/zone entry, availability clock, latency, same-time ordering, tie handling, stale signal behavior.
- Stop/invalidation, TP1/TP2 allocation, partial closes, trailing/break-even, opposite signal/time exits, gap-through-stop, ambiguous SL/TP, end-window open trades.
- Initial capital, risk policy, sizing units, effective contract metadata, concurrency/netting/margin and sessions.
- Full costs, dataset custody, split registry, purge/embargo, confidence/power/robustness and drawdown policy.

Existing register IDs retained: Q-B01..03, Q-T01..12, Q-D01/02, Q-DQ01..06, OPEN-VP-1..21. This audit selects no numeric production values. At freeze, refuse any unresolved field; canonicalize explicit full config, features, execution/cost/arithmetic policies, dataset/splits and protocol; hash each; record reviewers and authority before any evaluation. Future changes create a new baseline version, never overwrite V1.

## Historical availability and provenance (no payload read)

Main's tracked filename inventory identified `tasks/evidence/FIX3-realtime-samples.csv` and `FIX3-sequence-matches.csv`; neither was opened. No tracked historical manifest/normalized dataset was identified by name. This does not prove datasets are absent from external storage. Their availability and unseen status are **unverified**.

Draft #99 README states trusted status requires independent custody/signature/pin and designated authority; real ticks, UTC offset and commission blocked; swap partial, calendar blocked, gap threshold open, historical depth unknown. These are **documentation claims**, not fresh verification of datasets. Dukascopy pilot FINDINGS reports acquisition blocked and timestamp/layout/price scale unverified; no acquisition or payload inspection was performed here.

A future custodian must attest source/license, acquisition timestamps, raw and normalized hashes, UTC mapping, bid+ask coverage, effective-dated instrument capabilities, gaps/duplicates/order, quarantine/re-entry and exposure lineage. A hash establishes integrity only, not source truth or absence of leakage. No price-derived holdout summaries may be opened under this task.

## Reproducible historical test specification (proposal)

This is a completion checklist for existing EDGE-VP-1, not a new approved protocol.

1. Pre-register exact clean SHA, all explicit configuration and feature hashes, execution/cost/arithmetic contracts, dataset manifest and parent identity, dependency lock and actual interpreter/OS, random seeds, primary hypothesis/metric and trial ledger. Freeze information cutoff before eligible windows.
2. Independent custodian registers source-equivalent chronological train -> validation -> locked holdout, including renamed/overlapping copies, content/exposure lineage and prior viewed samples. No random temporal splits. Lock holdout before any result. Current authorization does not permit opening even non-holdout market data.
3. Fixed-config walk-forward uses registered non-overlapping evaluation windows after cutoff; no refit/optimization. Quant selects window lengths/step and minimum span before results. Purge/embargo must cover signal lifetime and maximum trade exposure; unbounded holding policy blocks a finite purge choice. Specify permitted past-only warmup and state carry; never initialize from future windows.
4. Each event separates occurrence, availability, decision, submission and earliest executable fill. Future/late data cannot retroactively change issued signals. Bar close cannot fill at a prior open. Deterministic same-time ordering and missing-data rejection are explicit.
5. Long enters ask/exits bid; short enters bid/exits ask, with approved adverse slippage and latency. Avoid double-charging spread when already in quote-side fills. Apply commission per effective schedule, financing/swap/rollover and currency/contract conversions. Unknown costs make headline result ineligible, never zero. Stress costs using predeclared scenarios.
6. Execute the frozen stop/target/time/concurrency policy event by event. Gap-through-stop uses approved available executable price, not a fictitious guaranteed stop fill. With bar ambiguity retain both bounds and use frozen conservative rule; report ambiguity/open/unfilled/skipped counts. No favorable post-result selection.
7. Track portfolio cash, exposure and marked-to-market equity through time, with all fees and open positions. Persist immutable decision/fill/ledger references and deterministic output hash. Reject data quality failures under approved policy, retain failed attempts in ledger.
8. Freeze protocol/thresholds and pass independent synthetic adversarial review before authorized validation. Later holdout evaluation requires separate explicit authorization and passes existing G1/G2/G3 controls; this report authorizes none. Reproduce the same ledger independently, including exact resulting SHA.

## Proposed acceptance metrics and decision logic

Threshold values remain **UNRESOLVED**; selecting them after results invalidates confirmatory use.

| Metric | Definition / proposed requirement | Decision needed |
|---|---|---|
| Net expectancy | Sum of realized net P&L / closed trades, plus predeclared account-currency or initial-risk unit. Positive lower dependence-aware CI bound above zero for a net-positive claim. | OPEN-VP-5/12/14/15: primary unit/effect, confidence level, interval, dependence/block method, multiplicity family/alpha |
| Relative edge | Paired net difference against preregistered comparator, minimum economic effect; beating a losing comparator alone is insufficient. | OPEN-VP-10/12/14/15; comparator, random seeds/draw count and trial family |
| Profit factor | Sum positive net P&L / abs(sum negative net P&L), with uncertainty; no losses -> undefined, never an automatic pass. | Supporting versus gating role and minimum/CI policy; no number chosen |
| Drawdown | Peak-to-trough marked-to-market equity, currency and % of defined capital; duration/time under water. | OPEN-VP-18; capital, limit, floating/closed equity convention |
| Sample adequacy | Closed/open/skipped/ambiguous trades, effective independent sample and time/regime coverage. Train-only power for predeclared effect; clustered trades are not independent. | OPEN-VP-1/2/11/19; no arbitrary 100-trade default |
| Robustness | Net/gross cost decomposition, registered cost stress, long/short/regime/window breakdown, concentration and walk-forward stability. Sensitivity is descriptive with variants logged; no parameter winner selected. | OPEN-VP-4/13/17; stress severity and stability criterion |
| Reproducibility | Same inputs -> same decisions, fills, ledger and metrics; source/config/data/dependency fingerprints match across independent run. | Accepted arithmetic and tolerance/exactness rules, run ledger |

Mapped verdicts: **BLOCKED** when required policy/data/harness gates prevent a valid test (current state). **INCONCLUSIVE** for a valid but underpowered/too-uncertain result under frozen rules. **NOT SUPPORTED** for an adequately powered valid test failing the registered edge criteria. **PROVEN** only means supported within the frozen tested historical domain after independent reproduction and all criteria; never a guarantee of future profit. No present synthetic pass can advance profitability to PROVEN.

## Validation and independent review

See `VALIDATION.md` for commands/results and `INDEPENDENT-REVIEW.md` for the separate reviewer findings. Original code remains unchanged, including fidelity gaps and the direct-engine restore issue. Expected failures are explicit unresolved failures of desired invariants, not passing safety evidence. Broader existing tests do not override the fidelity blockers.

### Coverage limits

The new P&F prefix test checks identical streaming prefixes and append-only transition history; it does not prove full SignalEngine/Pattern/Structure/Regime/Readiness/Risk causality. The future-suffix signal test covers legacy momentum only. Fixed-box snapshot tests do not certify ATR sizing, late data, all market paths, or the ResearchPipeline persistence boundary. Repeat execution asserts dataclass equality in one process, not canonical byte identity or independent process reproduction. Existing regression passes are supporting engineering evidence, not independent tests of every adversarial scenario in the proposed historical specification. Untested requirements remain blockers before historical evidence can count.

## Next required action

Resolve the existing baseline/trade/cost/statistical decisions with Quant/Architect/Owner and freeze the faithful executable policy. Route reproduced code defects to their owning implementation workstream for fixes plus independent re-review. Obtain separately authorized non-holdout dataset custody/cost evidence; complete G1/G2/G3 without granting them here. Only then run frozen walk-forward; keep holdout locked until separately authorized. Do not optimize to make the result pass.

MERGE: NOT PERFORMED. G1/G2/G3: NOT AUTHORIZED. No PAPER/DEMO/AUTO/LIVE unlock, PROD change or release.
