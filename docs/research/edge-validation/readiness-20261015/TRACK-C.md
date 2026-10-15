# Track C — Quant Decision Register

Status: **READY FOR RIN REVIEW; no decision frozen, no evaluation authorized.**
Date: 2026-10-15. Owner session: `/root/track_c` (Quant register compiler, not Quant decision authority).
Branch: `codex/edge-readiness-c-20261015-v1`. Worktree: `readiness-c`.
Base/inspected main HEAD: `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`.
PR #79 inspected HEAD: `196a962fd6fe2be41e0acfd70f6f7ec3b7a240db`.
PR #81 inspected HEAD: `49bd2d6c9d8cf11c0e78a1e209ccc0cbb5eb4282`.

## Scope and source authority

This is an audit and decision-request artifact, not a strategy specification. All rows below remain **UNRESOLVED**, unless explicitly described as an observed implementation fact. No numeric trading or statistical value is chosen here. Assigned owners are required decision roles, not assertions that those roles have accepted or completed the work. Read-only inspection preceded creation of the fresh branch; branch/path absence and a clean checkout at the base SHA were verified. Existing PR #79/#81 branches remain owned by their original sessions and were not edited.

Inspected sources:

- Main: `AGENTS.md` sections 3, 5–9, 12–16, 20; `docs/requirements.md` FR-02/03/06/09; `docs/architecture.md`; `agents/quant/AGENT.md`.
- Main: `docs/decisions/ADR-014-backtest-lab-reproducibility.md` (accepted), `docs/decisions/ADR-030-replay-validation-framework-v1.md` (approved architecture, Quant Q-V2–Q-V5 open).
- PR #79: `docs/research/edge-validation/EDGE-1-baseline-v1-spec.md`, `EDGE-2-engineering-components.md`, proposed `docs/decisions/ADR-037-edge-validation-research-contracts-v1.md`, and `packages/nexora/edge/exits.py` (`ExitPolicy`, `TradePlan`, `plan_from_signal`, exit assumptions).
- PR #81: `docs/research/edge-validation/EDGE-VP-1-validation-protocol.md`, revision 3, including P1–P7, C1–C9 and all OPEN-VP-1 through OPEN-VP-21.
- Issue #77 objective/ownership context supplied by MASTER after its GitHub audit: readiness and edge validation; no comment decisions reported. This track did not independently retrieve Issue #77 or inspect any runtime config/data.

ADR-037 is Proposed, and EDGE-VP-1 is DRAFT. Their contents describe proposals, not authorization. Main ADR-014 `train|eval` and ADR-030 causal/qualification contracts remain authoritative. No production-observed config or config information cutoff was supplied to this session. EDGE-1's title says “frozen at bc1712f” while its body says config is not pinned: this is a reconstruction code reference, not proof of a complete accepted baseline freeze.

## Required decision record format and ordering

Each resolution must record: decision ID; exact choice/formula and units; scope and version; source evidence SHA/hash; rationale independent of validation/holdout results; data dates seen; latest information cutoff; Quant signature and Rin/Architect acceptance where required; deterministic expected examples; implementation coverage; and entry gate unlocked. Empty fields remain unresolved. Code defaults and fixture parameters cannot fill those fields.

Order: recover observed config and data knowledge history → resolve finite holding/end-window semantics and costs → register an untouched range and its provenance → train-only exploratory frequency/variance if permitted by an accepted protocol → freeze all needed rules and thresholds with confirmatory record → independent validation reproduction → RIN REVIEW GATE. This wave stops at review; it runs none of those evaluations and unlocks nothing. A registration step requiring merge is deferred because this wave forbids merge.

A parameter change motivated by validation/holdout performance is not a resolution of this register. It requires a new declared strategy/protocol version and fresh eligible unseen data under the accepted governance; previous results and discarded trials remain visible.

## Baseline and trading-policy register

All rows have status **UNRESOLVED / evidence-grade run blocked**. “Observed” describes the audited implementation/proposal only.

| ID | Required decision and observed facts | Decision owner | Evidence required before resolution | Blocks / mapping |
|---|---|---|---|---|
| Q-B01 | Exact baseline config and versions: fixed/ATR mode, per-resolution boxes/reversal/ATR rules, price basis, fast/medium/slow and structure resolution, structure/pattern/regime/signal feature modes and version IDs, scoring/cooldown/expiry/stale values, ADR-010 thresholds. EDGE-1 reconstructed code/defaults are not actual observed configuration. | Quant + baseline owner; Architect checks contract | Non-secret operator-provided observation config, bound code/config/feature hashes, observation interval and provenance; reconcile every effective override against code and Track A inventory | P1 |
| Q-B02 | Baseline information cutoff: latest market date used in any baseline or OPEN-VP decision, including discretionary observations and earlier experiments. Unknown cannot mean “early.” | Quant + all decision contributors | Dated knowledge/exposure declarations; dataset/range history; cutoff before eligible validation and holdout bounds | P1/P4/C6/C9 |
| Q-T01 | TP1 versus TP2, or an explicit multi-stage exit strategy. `plan_from_signal` requires a named target; EDGE-1 reports generated TP1/TP2 distances, not a chosen exit policy. | Quant | Versioned target selection rationale; signal-to-plan examples, target existence/rejection tests; pre-results signature | P1; no OPEN-VP substitute |
| Q-T02 | Partial close enabled or disabled; if enabled, proportions, trigger order, rounding, minimum/residual volume rules and costs per leg. Existing single-target exit mode does not implement this. | Quant + capability owner; Architect for extension | Explicit policy even if disabled; hand-worked multi-leg examples, broker-agnostic capability mapping, implementation/review evidence | P1; sizing/cost/concurrency dependencies |
| Q-T03 | Initial stop source and movement: retain signal invalidation or other approved rule; break-even/trailing enabled or disabled, triggers, update timing and precedence. Existing `TradePlan` has a static stop. | Quant | Decision-time invalidation provenance; side/crossed-entry/gap examples; update causal contract if movement required | P1, Q-T05, VP-3 |
| Q-T04 | Sizing/exposure unit and aggregation. `unit_size` is only a research multiplier, not account sizing; no contract-size/tick-value/account-currency conversion exists. | Quant + capability/Data; Rin risk acceptance | Decide per-unit evidence versus account-level simulation; dimension analysis, capability contract size/tick value/currency/lot constraints and effective dates if needed; no hard-coded broker rules | P1/P3, VP-5/18 |
| Q-T05 | Time stop: finite maximum holding duration or an explicitly approved unbounded alternative and boundary design. `max_hold_bars=None` means no time stop; no default is a freeze. Specify bar count convention, expiry versus position close, and competing exit priority. | Quant | Clock/bar interval and causal examples; finite exposure bound if purge derives from maximum holding; implementation coverage | P1, VP-3/9, C8 |
| Q-T06 | Concurrency `one_position` or `allow_overlap`; combined BUY/SELL and bid/ask run scheduling; total exposure cap if relevant. Independent per-side studies cannot silently stand in for a combined one-position portfolio. | Quant + Architect for orchestration | Unified timeline, overlapping/opposite-side examples, unresolved positions blocking entries, exposure accounting and deterministic tie cases | P1, VP-19 |
| Q-T07 | Same-time conflicting BUY/SELL and multiple-plan tie policy. Current one-position mode fails closed on conflicting same-time signals; no preference is selected. | Quant | Stable deterministic ordering/reject rule, identical-time examples and tests | P1, Q-T06 |
| Q-T08 | Headline ambiguity policy and target-touch fill assumption. Code defaults to stop-first; draft protocol proposes stop-first headline and target-first sensitivity only. Quant must explicitly accept; a code default is not acceptance. | Quant | Conservative fill contract, tick-order limitations, target-touch/gap cases; model limitation disclosure | P1/C5 |
| Q-T09 | Decision and entry knowledge clock: signals must use only available input; completed-bar availability, latency and entry delay must be causally compatible. Draft requires at least one bar unless knowledge-before-open is demonstrated. | Quant + Data + reviewer | Bar open/close/received-at contract, signal availability trace, prefix/future-mutation evidence, no use of closing price before known | VP-6; P1/C9 |
| Q-T10 | End-window open/no-entry/rejected/skipped trades: define denominator, mark-to-market/liquidation/censoring treatment and stress/bounds that prevent favorable deletion. Existing mode counts open-at-end but excludes them from closed ledger. | Quant | Boundary and loss-tail examples; complete signal-status reconciliation; explicit policy before results, engineering support if changed | VP-9/19; C8 |
| Q-T11 | Gaps, outages, stale quotes and sampled-path incompleteness: censor/reject/measure-through rule; tolerances and whether each claim is tick- or bar-based. Never repair missing observations by fabricated paths. | Quant + Data | Quality report, effective UTC/session metadata; hand examples and declared coverage constraints | ADR-030 Q-V4, P2/C9 |

## OPEN-VP mapping — complete, with evidence and gate

Every row remains **UNRESOLVED**. Identifiers preserve PR #81 revision 3 exactly; this artifact does not create competing defaults.

| ID | Decision required | Owner per draft | Evidence / acceptance required | Blocked gate |
|---|---|---|---|---|
| OPEN-VP-1 | Chronological train/validation/holdout calendar boundaries or proportions | Quant | Untouched range inventory, timestamps, frequency feasibility from permitted train only; exact half-open UTC boundaries committed before reads | P4 |
| OPEN-VP-2 | Minimum calendar span per segment | Quant | Ex-ante regime/coverage rationale and minimum-span rule independent of unseen returns | P4 |
| OPEN-VP-3 | Purge/embargo length | Quant | Bound on signal expiry plus maximum holding; Q-T05/Q-T10 first. If holding is unbounded, no finite purge can be justified using the draft's stated bound; resolve explicitly | P4 |
| OPEN-VP-4 | Rolling/anchored walk-forward, lengths and step | Quant | Non-overlapping tests, purge and cutoff constraints; exact versioned window construction and expected examples | C6 |
| OPEN-VP-5 | One primary net metric and unit | Quant | Formula, complete denominator, R reference if used; cost/sizing dimension agreement; no composite score | C2/C3 |
| OPEN-VP-6 | Entry delay | Quant | Q-T09 causal knowledge proof; no default-zero adoption; delay/bar-clock examples | P1 |
| OPEN-VP-7 | Second quote-side evaluation | Quant | Current user requirement is real bid/ask. Obtain both series with aligned provenance; a spread-shift proposal cannot waive that requirement. No approximation selected | P2 |
| OPEN-VP-8 | Slippage and swap conventions per instrument | Quant + Data | Effective-dated capability/fee evidence, financing calendar/clock, conservative latency assumptions and uncertainty; unknown never zero | P3 |
| OPEN-VP-9 | Headline open-at-end treatment | Quant | Q-T10 boundary accounting, loss-tail bounds, explicit implementation coverage and denominators | C8 |
| OPEN-VP-10 | Comparator(s), seeds/draw count if random null | Quant | Same dataset/rules/costs and stress; seed commitment; attainable finite-sample p-value resolution under chosen family adjustment | C3 |
| OPEN-VP-11 | Minimum closed-trade count | Quant | Power calculation using train-only variance, chosen minimum effect and adjusted level; insufficient predeclared coverage yields INCONCLUSIVE, never post-hoc extension | C1 |
| OPEN-VP-12 | Minimum meaningful effect | Quant + Rin | Economic/risk rationale and unit, selected independently of validation/holdout results | C3 |
| OPEN-VP-13 | Cost stress multipliers | Quant | Predeclared plausible uncertainty scenario rationale; apply every component symmetrically to baseline and comparator | C4 |
| OPEN-VP-14 | Dependence-respecting interval method, block length, confidence; finite-sample random-null p-value convention | Quant | Serial dependence rationale, train-only method calibration where necessary, deterministic seed/method fixtures and attainable level check | C2/C3 |
| OPEN-VP-15 | Multiplicity method/alpha and selection/data-snooping correction | Quant | Full trial ledger and fixed confirmatory family with absolute and relative tests; declare how validation searches are counted/corrected | C2/C3 |
| OPEN-VP-16 | Registry, pre-registration record and run-ledger locations/formats | Architect | Reviewed append-only schema, two-entry sequence, independent hash trust root, access/provenance/retention checks; no merge in this wave | P4 |
| OPEN-VP-17 | Walk-forward positive-window consistency threshold | Quant | Ex-ante rationale and eligible-window denominator after known information cutoff | C6 |
| OPEN-VP-18 | Max drawdown limit | Quant + Rin | Metric/account unit agreement with Q-T04, risk rationale and open-position treatment | C7 |
| OPEN-VP-19 | Maximum open-at-end/skipped share | Quant | Q-T06/Q-T10 denominator plus conservative impact bounds demonstrating exclusions cannot reverse conclusions | C8 |
| OPEN-VP-20 | Exact Rin holdout-unlock token format | Rin | Separately reviewed authority design; binds protocol/data/plan/registry/record/code/hypotheses; this report grants no token or unlock | Holdout 9.1 |
| OPEN-VP-21 | Challenger finalist count | Quant + Rin | Fixed family size and selection rule before validation; multiplicity/power budget; post-selection experiments labelled exploratory | Protocol 10 |

## Costs and compatibility decisions that must not disappear between rows

Spread must be observed from real bid/ask; commission needs an effective-dated capability fee schedule; slippage must be declared rather than omitted; swap needs its financing convention and session calendar. Each component must be `known` for headline evidence, dimensionally compatible with price units and trade multiplier; fees per partial-close leg need separate accounting if partial closes are approved. Zero is accepted only with affirmative evidence. Record quote timing and unmodelled exit-spread widening; a stress multiplier cannot repair a structurally missing fill model. Capability metadata remains adapter-supplied and configurable, never a fixed broker/symbol/lot/session assumption.

ADR-030 Q-V2 (outcome definitions/baseline anchors), Q-V3 (Adaptive Box unit policy), Q-V4 (gaps) and Q-V5 (statistical reporting) stay open within their own accepted scope. VP-5/11/14 overlap measurement concerns but do not silently resolve those ADR decisions. ADR-030 excludes trade PnL and bars in its own V1; accepting ADR-037's separate-contract mapping is the explicit dependency for EDGE research, not permission to overwrite VALID-1 semantics.

## Findings and blockers

1. Baseline config, observation code identity, all policy choices above and the information cutoff are unverified; no complete baseline freeze or evidence-grade run is possible.
2. Finite purge depends on time stop and boundary semantics. An unlimited holding setting cannot satisfy the draft “at least maximum possible holding period” rule with a guessed finite purge.
3. Partial close, dynamic stops and account sizing are not represented by the audited static single-target exit contract. Explicitly disabling them requires a Quant decision; enabling them requires new scoped implementation/review, not an undocumented simulation proxy.
4. Combined long/short concurrency needs a joint timeline; separate bid and ask runs can otherwise bypass a portfolio one-position limit. This register requests a decision, not an implementation.
5. P2/P3 are blocked by missing verified real data, both quote sides, full costs and capability evidence. Synthetic test fixtures provide no profitability evidence.
6. ADR-037 acceptance and protocol freeze are pending. Committed independent registry and two-entry record do not yet exist in inspected sources; library caller-provided hashes are not an independent trust root.
7. PR #81 itself reports missing edge code SHA/dirty-tree provenance, persistent holdout access/reproduction labels, metrics/inference/comparator/ledger tooling. Evidence eligibility remains constant false. Documentary decisions alone cannot make a run citable.

## Validation performed and review gate

- Read-only committed-source audit at the full SHAs above; no runtime, data extraction or tests using real data.
- Fresh branch/worktree identity and initial `git status --short` verified clean.
- Documentation checks: `git diff --check`; verify all 21 OPEN-VP rows occur exactly once in the mapping; check main-source paths against the base and PR-only paths against their recorded SHAs; exact staged changed-file scope check.
- Application tests: **not_run** — documentation-only register; no code, trading formulas or config changed. Self-review only; independent review pending.

Next gate: **RIN REVIEW GATE**. Quant supplies signed, evidence-backed choices; Track A verifies observed config/cutoff; Track B verifies genuine data/provenance/cost capabilities; Architect/Rin resolve proposed contract/protocol governance; independent reviewer checks exact resulting SHA and decision coverage. No merge, holdout unlock or execution authorization follows from this artifact.

Safety confirmation: NO MERGE; NO PROD START; NO LIVE/DEMO/PAPER/AUTO ORDER SUBMISSION; NO HOLDOUT READ OR UNLOCK; NO HOLDOUT TUNING; NO BROKER/SYMBOL ASSUMPTION; NO PROFITABILITY CLAIM. Only this report path is changed. PR not created; no other agent work overwritten.
