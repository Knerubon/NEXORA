# Owner Decision Sheet

DRAFT / every recommendation **PROPOSED pending Owner approval**. Choices are about faithful specification, not improving results. No decision is selected by writing this sheet. Resolve existing accepted contracts first (B); source facts are in A. Existing Q-* / OPEN-VP IDs remain authoritative; D1–D6 group dependent questions without renumbering those records.

Minimum response is six grouped records, each with an attached explicit configuration/policy where required. A group label alone does not approve missing numbers. Record choice, exact values/units, rationale independent of results, viewed-data dates/information cutoff, Quant/Architect acceptance and deterministic examples. Existing authorization to inspect/specify and use development data is already granted; no repeat permission requested.

## D1 — Recover the existing baseline (Q-B01, Q-B02 / earlier master Q-B01, Q-D01/02)

Why: boxes, sampling, regime thresholds and warmup alter the entire signal stream. Selecting fixture values creates a different strategy.

| Missing choice | Concrete options | Recommendation / tradeoff |
|---|---|---|
| Configuration authority | A: supply non-secret actual observation config + source/config/features hashes + observation interval; B: declare existing config unrecoverable and authorize a newly specified V1 with full values | A: faithfully reproduces existing behavior. B is honest but a new specification, not recovered observation |
| Fixed/ATR scope | A: one attested fixed configuration; B: one attested ATR configuration; C: both as separately registered baselines/trials | Attested historical mode. If no attestation, choose explicitly; do not assume fixed means selected. Both increases inference/trial obligations |
| Warmup/state boundary | A: reproduce known run seed and history; B: predeclared finite development-only warmup plus fresh run; C: verified historical checkpoint with exact lineage | A if available. B reproducible but changes initialization; exact duration/seed/signal-permission cutoff must be provided before run. C adds recovery evidence |

Artifact: fill every inventory field (including precision, units, ordered resolutions, sizing and P&F box precedence, all version/cooldown/expiry/stale/regime values), declare feature config and information cutoff. Optional PatternEngine stays DISABLED/SHADOW and legacy scoring stays unchanged under accepted ADR-024; no new promotion choice requested.

## D2 — Translate an actionable signal to an entry (Q-T / OPEN-VP-6, Q-B01)

Why: raw Signal, a later READY state and a zone order can enter on different events/prices or never enter. ADR-021 fixes Readiness's table, not the historical scheduler.

| Missing choice | Options | Recommendation / tradeoff |
|---|---|---|
| Strategy boundary | A: raw Signal research-only comparison; B: Signal + READY + Risk; C: Signal + READY, fixed-size opportunity study explicitly excluding account Risk | B for the full intended strategy. A/C remain separately labelled diagnostics and cannot claim full risk-controlled net edge |
| Readiness lifecycle | A: eligible only if READY on signal emission, otherwise discard; B: keep immutable signal pending until READY before configured expiry, cancel on invalidation/opposition; C: require a newly emitted signal after READY | A is smallest and avoids invented queued-entry behavior; B/C change entry population and need full cancellation/revalidation rules |
| Entry type | A: market at first eligible post-decision quote; B: zone limit order with declared price, TTL and trigger side; C: stop-entry at explicitly named trigger | A smallest executable mapping; ignores zone as a limit condition and must be approved. B/C preserve different intended semantics but need order-specific fill/gap rules |
| Availability / latency | A: next canonical quote after decision plus fixed declared latency; B: same-event fill only with documented executable-quote/decision ordering; C: next eligible completed-bar boundary using synchronized quote data | A avoids reusing the decision event as a guaranteed fill. Specify latency, maximum quote age, expiry/revalidation and tie order. C coarser and loses tick path |

Keep original signal id/decision geometry. Define whether Risk is evaluated at T0 or at actual order eligibility (recommend at eligibility with unchanged signal provenance and valid freshness; a changed decision-time contract requires review, never relabel an old signal's timestamp). TTL expiry before a delayed fill must reject/revalidate under a declared binding contract. No fabricated `live` status or account freshness for historical inputs.

## D3 — Exits and dataset boundaries (Q-T01/02/03/05/08, Q-B02 end-window in master, OPEN-VP-9)

Why: targets are geometry; different allocations, stop activations and boundary treatment materially change PnL and purge length. Position Supervisor implements mechanics only.

| Missing choice | Options | Recommendation / tradeoff |
|---|---|---|
| Stop / target plan | A: freeze signal invalidation and TP1, full exit; B: freeze invalidation and TP2, full exit; C: TP1 partial then TP2 with explicit proportions/rounding/residual rules | A smallest single-target implementation; no assertion it is the actual historical choice. B equally small, longer exposure. C adds staged state/costs; provide actual intended rule |
| Dynamic stop | A: no movement; B: explicit break-even/trailing formula and priority | A smallest; B needs separate Quant formulas (not supplied by supervisor mechanics) |
| Opposite signal / time stop | A: ignore opposite signals, fixed finite maximum hold; B: close at next executable quote on opposite qualifying signal, plus finite cap; C: no time stop, hold until SL/TP | A isolates static plan and bounds purge; specify exact duration and clock. B requires definition of qualifying opposite signal/Readiness/Risk and same-event priority. C leaves finite purge unresolved |
| Stop gap / target gap | A: stop-market uses first available exit-side quote with adverse slippage; target limit no better than target, explicit quote-touch fill assumption; B: use actual provider fill evidence if available | A reproducible conservative simulation, no guaranteed stop price. B more realistic but historical fills usually unavailable; target liquidity/touch assumption must stay labelled |
| Both SL and TP in a bar | A: reject bar-only trade evidence and require ordered ticks; B: stop-first headline with both bounds and ambiguity count | A recommended when ticks feasible; B conservative declared approximation, requires Quant approval and separate ambiguity sensitivity; never choose favorable path post-result |
| Open at dataset/segment end | A: report open positions marked on last valid side quote, separately from closed-trade expectancy; B: explicit forced liquidation at boundary quote; C: carry to an approved continuation within development | A preserves censoring and portfolio equity, with explicit headline treatment. B invents a boundary exit but can define a separate strategy. C may cross protected data; never auto-extend |

Specify entry-at/through-stop rejection, stop activation instant, partial-leg costs if C, timeout order, same-index exit/entry priority and maximum exposure lifetime. No fixed numeric hold, allocation or purge is inferred from examples.

## D4 — Risk and account portfolio (Q-T04/06/07, OPEN-VP-18)

Why: independent signal trades do not show implementable account returns; contract size/FX errors can rescale losses and make Risk budgets wrong.

| Missing choice | Options | Recommendation / tradeoff |
|---|---|---|
| Ledger scope | A: one account, one open position total across both sides for selected instrument, skip new signals while open; B: netting account with explicit reverse/reduce priority; C: hedged concurrent positions with aggregate caps | A smallest full-account baseline. B/C match other possible actual behavior, require more accounting. Do not silently combine independent long/short runs |
| Sizing units | A: unit-normalized instrument whose price×quantity is account money; B: adapter converts effective-dated contract/tick/FX economics with an explicitly accepted Risk input mapping | B for real broker instruments. A only if data/capabilities prove the identity; no implicit lot=unit or USD=account currency |
| Capital/budgets | A: recover actual capital and every RiskPolicy field; B: Owner prescribes a new research account with all values | A faithful; B distinct hypothesis. Supply currency, initial cash, requested size, risk/exposure/daily loss/DD limits, min stop, size step, calendar/timezone, freshness/TTL, margin/leverage/session policy |

Recommend one marked-to-market event-time ledger; close/release once before next eligible proposal according to a declared priority. Risk exposure is reserved loss-at-stop, not notional. Do not count the same position in both account.exposure_in_use and reserved_exposure; define conversion/reservation and release at partial/full exit. Accepted RiskEngine uses absolute budgets; do not reinterpret as percentages or modify its formulas silently. Monetary contract mapping can need an ADR amendment before integration.

## D5 — Costs and numeric contract (Q-T09–12 / OPEN-VP-8)

Why: unknown fees/swap or charging spread twice invalidates net edge. Current CostPolicy mixes a simple per-unit sum, without historical economics.

| Missing choice | Options | Recommendation / tradeoff |
|---|---|---|
| Quote-side costs | A: historical bid/ask fills, no additional spread charge; B: one-sided data with an explicitly approved historical spread reconstruction | A faithful and smallest ambiguity. B is approximation requiring independent coverage evidence; never zero-fill missing ask |
| Fee/funding source | A: effective-dated provider/broker schedules and account currency conversion; B: no authoritative schedule, retain unknown and restrict to gross/exploratory outputs | A for net claims; B allows engineering progress but blocks headline net evidence |
| Slippage | A: predeclared adverse fixed amount in explicit price units per fill; B: predeclared effective session/size schedule | A smallest transparent research assumption; value must be supplied independent of results. B needs more source evidence. Include latency and stress definitions |
| Arithmetic | A: pin existing Python Decimal context and explicit rounding/step order for compatibility; B: accept a separate local Decimal context descriptor for harness calculations | B isolates caller context and is reproducible, but requires formal arithmetic contract; never silently import draft edge context as accepted |

Supply per-side/round-turn fee basis, minimum/tier fees, currency, contract multiplier, effective dates; swap long/short, rollover timezone/triple day, holidays, FX series and missing-rate policy. Affirmatively evidenced zero is distinct from unknown. Cost stress cannot compensate for missing quote sides or undefined financing.

## D6 — Development data, quality and eligible evidence (Q-D*, MD-1…8, MD6, OPEN-VP, ADR-030 Q-V2…5)

Why: broad downloads can disclose protected holdout; external feed prices are not interchangeable with broker observations. Authorization is already granted for development use, but scope is missing.

| Missing choice | Options | Recommendation / tradeoff |
|---|---|---|
| Development source | A: custodian-supplied broker-agnostic bid/ask export; B: lawfully obtained provider historical export with verified layout/time/scale; C: separately operator-produced read-only MT5 export under integration-node procedure | A if available; B avoids terminal dependency but feed is a distinct market-data hypothesis. C cannot run in this task (no MT5 startup) |
| Bounds/custody | A: supply canonical instrument, provider alias, exact half-open development UTC range, warmup range and signed protected ranges/exposure registry; B: explicitly designate a new development-only range plus protected registry after cutoff review | A preferred if records exist. B valid only after custodian confirms exclusion of all protected/equivalent streams; an empty guessed holdout list is invalid |
| Gap policy | A: fail/censor unexplained gaps at approved threshold, retain observed positions/outage status; B: measure through under explicit MD-6 rules and exposure accounting | A conservative recommendation, exact gap/stale/closure threshold and re-entry policy still Owner/Quant values. B cannot silently alter ADR-030 Q-V4 |
| Evidence protocol | A: finish existing separate ADR-037 + EDGE-VP-1 / MD-6 decisions; B: development-only non-citable run until those decisions are accepted | A for eventual proof; B for bounded harness development after data gates, labelled appropriately. No inference threshold needed merely to check synthetic mechanics |

Protected bounds, source, actual configuration and cutoff are required inputs, not a request to re-authorize data use. No holdout-derived summary is needed. Existing protocol architecture choice to keep edge separate is retained; formal acceptance is outstanding. Statistical thresholds, minimum sample/effect, dependence-aware intervals/multiplicity, comparators and walk-forward remain in existing OPEN-VP/Q-V registers; no replacement numbers proposed here.
