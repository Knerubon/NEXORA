# P&F Baseline V1 Candidate — DRAFT

**NOT FROZEN / NOT EXECUTABLE.** Source identity is established; an approved executable trading contract is not. This package supplements PR #104 and the existing readiness candidate; it does not duplicate their audit/tests or replace their registers.

## Provenance and ownership

- Main/base/source: `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`. PR #104 reference HEAD: `1d6b47ea213b5e681647b60d9e78fa5fdf45fa99`, open draft, unmerged at inspection.
- Owner: Codex cloud session `5e7f30c1a6ab`; branch `codex/pnf-executable-spec-v1`; isolated checkout `/workspace/scratch/5e7f30c1a6ab/nexora`. Initial working tree clean at base. Branch absent before creation. Owned paths: this new directory only. Existing report paths and source branches untouched.
- Acting roles: specification compiler / Architect analysis, then QA self-review. No independent review claimed.
- Source SHA-256 inventory: all tracked Python files under `packages/nexora/`; excludes payloads. Dependency fingerprint: `01cc10db48dac435250520cc384ae768d55bb1739596e8019093a28ffafd8b5f`. Individual raw-byte hashes of `uv.lock`, `pyproject.toml`, web package lock/manifest are in [inventory](SOURCE-AND-CONFIG-INVENTORY.json). This attests bytes, not installed runtime/dependency compatibility. No dependencies installed or runtime tests claimed.

## Existing decisions retained

| Accepted source / scope | Already resolved; do not ask again | Remaining boundary |
|---|---|---|
| ADR-004/019 | UTC and explicit declared feed corrections; never infer universal +10800 offset | Reconcile canonical normalized source order with pipeline monotonicity; historical availability mapping required |
| ADR-005/006 | Inclusive P&F seed/extension/reversal; causal per-event delta-ATR proxy, no rewrite | Required config values and observation provenance missing |
| ADR-008/009/010/011 | Independent resolutions, transition-pivot confirmation, regime priorities, research signal evidence/dedup | Numeric regime/cooldown/stale settings need attestation |
| ADR-014 | Timed-entry/holding comparison runner and hash contract | A stop/target portfolio harness needs a distinct accepted contract |
| ADR-015/016 | Risk budgets, fail-closed validation, reservation/release; paper consumes Risk | Historical binding, account units and ledger integration not implied |
| ADR-020/021 | Trendline formulas and exhaustive Readiness table | Whether/how historical entry waits for READY is not decided here |
| ADR-023/024 | Optional feature registry; PatternEngine DISABLED/SHADOW, legacy Signal unchanged | No classic pattern promotion or source switch |
| ADR-025 | Adapter/canonical instrument boundary | No broker/symbol/lot assumptions in core; Q-Q1 grid-to-box policy remains open |
| ADR-026/030 | Analytical/replay boundaries; canonical-index causality, clean-source qualification | Q-M3/4/8 and Q-V2–5 remain open in their own scopes |
| ADR-034 | Execution contract shapes FROZEN | No operational unlock; ADR-033/035 proposals are not blanket acceptance |

See pinned decision links below. ADR-035 only RESOLVED_BY_RIN clauses count as decided; this task changes none.

## Complete configuration envelope

No operator configuration was supplied or located as an approved baseline artifact. `docs/examples/baseline-config.json` says `example-only-v2` and mode `baseline`; its numbers are not substituted. Every constructor field is explicitly inventoried, default expression shown, approval value null. A later candidate must fill **all** these fields, including version IDs and all nested resolution fields; no inherited runtime/env defaults.

| Group | Required resolved content | Current status |
|---|---|---|
| Identity | canonical instrument/binding; observed source SHA/config interval; config, feature and information-cutoff attestations | UNRESOLVED |
| Pipeline | version; ≥3 ordered resolutions; each PnfConfig and AdaptiveBoxConfig field; structure_resolution; every RegimeConfig and SignalConfig/weights field; stale_after_events | UNRESOLVED (inventory lists exact fields) |
| Mode | fixed_pnf versus adaptive_pnf; no automatic forced-mode surprise; sizing/pnf box mismatch checked explicitly | UNRESOLVED |
| Defaults | Signal weights20/25/20/20/15/10/15; wait49/action65/gap8; tolerance.8; half-width.5; RR1.5/2.5. ATR period14/multiplier1/min.1/max50. Risk timezoneUTC/age10/TTL10. Backtest lookback2, pipelineNone. | IMPLEMENTED constructor values only; **not approved settings** |
| Features | backtest features omitted⇒optional PatternEngine DISABLED, all8 units disabled; registry ceiling SHADOW for it. Trendline/Readiness/legacy scoring not lifecycle toggles. M30/Experience/AI/news excluded from decision chain | CONFIGURED current behavior; candidate scope approval missing |
| Historical entry | event/availability mapping; clock/order; signal eligibility/expiry; Readiness timing; order type/zone; latency; fill-side/quote freshness; cancellation/revalidation | UNRESOLVED D2 |
| Exit | stop source/activation; target/partials; time and opposite handling; gap/ambiguous bars; end-window carry/close/accounting | UNRESOLVED D3 |
| Account/Risk | starting capital/currency; every RiskPolicy field; requested sizing/contract conversion; account snapshots; concurrency/netting; risk reservation once; margin/sessions | UNRESOLVED D4 |
| Costs/arithmetic | effective-dated commission/slippage/swap/rollover/conversion; quote-side spread accounting; Decimal precision/rounding per step | UNRESOLVED D5; unknown≠zero |
| Data/protocol | instrument/provider; development UTC bounds; protected intervals across equivalent streams; warmup/purge; exposure/cutoff; quality/calendar; split registry; reporting eligibility | UNRESOLVED D6 |

The listed constructor defaults resolve the **source inventory**, not a run configuration. No config hash is asserted for a null approved_configuration.

## Event and execution contract candidate

PROPOSED pending C decisions: consume canonical event index; process only available information; freeze decision artifact after `process(e_k)` before `e_(k+1)`; retain occurrence/confirmation/availability separately. Historical retrieval time must not impersonate original received_at. Same timestamp uses source canonical index, never a newly sorted price path.

Bind each opportunity to exact SignalDecision, Readiness, RiskDecision/account/quote and immutable source/config hashes. Long fills ask then exits bid; short bid then ask. A quote used for a decision does not by itself prove that a same-event fill is possible. No earlier bar open fill from its later known close. Entry zone is geometry until order-type choice is approved. Stop/target geometry remains at T0 unless an approved rule explicitly changes it; never recompute a past trade from later snapshots.

Each fill, rejection, cancellation, partial, open-at-end and cost component must carry canonical index, timing, quantity, side/currency, reason and source refs. Portfolio ledger required if capital-dependent Risk is selected. No double-counted spread or risk reservation; no missing-cost fallback. Exact handling is **PROPOSED**, not implemented by this document.

## Deterministic replay and freeze blockers

Pin source/config/features/data/adapter/capabilities/costs/protocol/locks, actual interpreter/platform, Decimal context, tie ordering and state-reset/carry policy. Equal immutable inputs must give equal decisions, Readiness, Risk, fills, ledger and metrics across fresh process, prefix replay, restart/checkpoint and repeated runs. Source fingerprint alone cannot certify these properties. PR #104's direct PnfEngine seed/no-transition dedup restore issue remains open; whole-pipeline causality/ATR/restart require end-to-end tests.

Freeze prohibited until D1–D6 required fields have signed, dated resolutions with units/examples, all config nulls filled, accepted contract scope and implementation/tests reviewed, approved data manifest and protected ranges pinned, and cost/quality/protocol gates met. Evidence-grade inference additionally needs existing OPEN-VP/Q-V decisions; development permission does not remove those gates. No profitability verdict follows.

## Draft dependencies (read-only references)

Existing #79 ADR-037/edge is a **separate research contract proposal**, as already recorded in #81; formal ADR acceptance still pending. #81 EDGE-VP-1 and #87 prior candidate remain draft. Do not re-open separate-vs-overwrite as an undecided preference: retain separate contract and obtain its formal acceptance. #94→#97→#100 Quant MD-6 remains proposal/PENDING. #96→#99→#102 custody and #95→#98→#101→#103 Windows tracks remain separate draft dependencies, not merged behavior. [Exact inspected workstream manifest](WORKSTREAMS.json) is a timestamped snapshot, not live approval.

Read source decisions at main:
- [docs/decisions/ADR-005-pnf-fixed-box-rules.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-005-pnf-fixed-box-rules.md#L1)
- [docs/decisions/ADR-006-adaptive-box-sizing.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-006-adaptive-box-sizing.md#L1)
- [docs/decisions/ADR-008-multi-resolution-matrix.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-008-multi-resolution-matrix.md#L1)
- [docs/decisions/ADR-009-market-structure-lifecycle.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-009-market-structure-lifecycle.md#L1)
- [docs/decisions/ADR-010-market-regime-thresholds.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-010-market-regime-thresholds.md#L1)
- [docs/decisions/ADR-011-signal-evidence-policy.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-011-signal-evidence-policy.md#L1)
- [docs/decisions/ADR-014-backtest-lab-reproducibility.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-014-backtest-lab-reproducibility.md#L1)
- [docs/decisions/ADR-015-risk-engine-policy.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-015-risk-engine-policy.md#L1)
- [docs/decisions/ADR-016-paper-trading-simulator-boundary.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-016-paper-trading-simulator-boundary.md#L1)
- [docs/decisions/ADR-020-pnf-trendline-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-020-pnf-trendline-v1.md#L1)
- [docs/decisions/ADR-021-entry-readiness-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-021-entry-readiness-v1.md#L1)
- [docs/decisions/ADR-023-feature-lifecycle-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-023-feature-lifecycle-v1.md#L1)
- [docs/decisions/ADR-024-pnf-pattern-engine-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-024-pnf-pattern-engine-v1.md#L1)
- [docs/decisions/ADR-025-mt5-instrument-resolution-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-025-mt5-instrument-resolution-v1.md#L1)
- [docs/decisions/ADR-026-m30-next-candle-bias-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-026-m30-next-candle-bias-v1.md#L1)
- [docs/decisions/ADR-030-replay-validation-framework-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-030-replay-validation-framework-v1.md#L1)
- [docs/decisions/ADR-033-autonomous-trading-contracts-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-033-autonomous-trading-contracts-v1.md#L1)
- [docs/decisions/ADR-034-execution-contracts-v1.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-034-execution-contracts-v1.md#L1)
- [docs/decisions/ADR-035-execution-integration-safety-amendment.md:1](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-035-execution-integration-safety-amendment.md#L1)

Additional inspected contracts: [ADR-004](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-004-market-data.md#L1), [ADR-019](https://github.com/Knerubon/NEXORA/blob/36c90c59e2aa4f0d221c9c468a8379c5f57cbb87/docs/decisions/ADR-019-explicit-feed-time-correction.md#L1). ADR-025 Q-Q1 remains open: validated price grid does not authorize automatic box-size/precision rules. ADR-004 replay sorting and ADR-030 canonical-index causality must be reconciled in the normalization contract; never sort execution quotes retrospectively to improve results.
