# Track A — P&F Baseline V1 readiness audit

Status: **READY FOR RIN REVIEW; FULL BASELINE FREEZE BLOCKED.** This is an inventory/proposal, not a freeze or profitability result.

## Identity, scope and information cutoff

- Owner: Codex Track A session `/root/track_a`; role DEV-PNF / audit author (self-review only).
- Base / audited main: `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`.
- Own branch: `codex/edge-readiness-a-20261015-v1`; own worktree: `readiness-a`.
- Sole owned path: this report. Branch absence was checked locally and with `git ls-remote --heads`; new worktree was clean at the base SHA. Existing PR owners and their branches were read only.
- PR #79 inspected at `196a962fd6fe2be41e0acfd70f6f7ec3b7a240db`; PR #81 at `49bd2d6c9d8cf11c0e78a1e209ccc0cbb5eb4282`.
- Audit date: 2026-10-15. This date is **not** the config information cutoff. Latest market information seen by every human/agent who selected parameters or validation thresholds is **UNKNOWN**; no evidence establishes an eligible unseen interval. Do not substitute commit/date-of-audit for this cutoff.
- No runtime, production storage, local secret config, market data or holdout prices were accessed. No branch belonging to another owner was edited.

## Sources and why inspected

All paths below are relative to the repository and refer to main above unless a PR SHA is specified.

| Source | Relevant lines / purpose |
|---|---|
| `AGENTS.md` | sections 0, 3, 5–12, 14–20: ownership, pure engine, Quant authority, isolation, docs validation and Rin gate |
| `docs/requirements.md` | 4–36, 53–57, 69: deterministic P&F, explicit evidence/versions, research comparison; no assumed OX box semantics |
| `docs/architecture.md` | 38–44: same pure calculation engine and reproducible stored raw inputs/config |
| `docs/decisions/ADR-014-backtest-lab-reproducibility.md` | 13–28: versioned manifest/config, canonical hashes, deterministic delay/hold simulator |
| `docs/decisions/ADR-030-replay-validation-framework-v1.md` | 93–106, 196, 324–330, 407–419, 513–518: isolation, preregistration, version inventory, no production rule changes, open Quant choices |
| `packages/nexora/pnf/models.py`, `engine.py` | config 26–47; quantized seed/extension/reversal 65–126, 161–371 |
| `packages/nexora/adaptive_box/models.py` | 21–30: fixed/ATR configuration and constructor defaults |
| `packages/nexora/research/pipeline.py` | 23–47, 50–140: full pipeline contract, feature input and consumer order |
| `packages/nexora/research/runtime.py` | 28–34, 49–53: runtime version and optional feature resolution |
| `packages/nexora/signals/models.py`, `engine.py` | weights 16–24, config 88–100; scoring 522–534, setup 536–581, evidence versions 863–958 |
| `apps/api/nexora_api/research.py` | 66–95: effective startup config loaded from external paths, not repository examples |
| `packages/nexora/features.py` | default feature construction 182 onward; effective resolved feature hash is separate from pipeline config |
| `docs/examples/research-config.json`, `baseline-config.json` | complete illustrative config and legacy comparator; examples are not production observations |
| `config/production.env.example` | external approved research JSON and optional feature config; example is not a process/config attestation |
| `packages/nexora/backtest/runner.py` | 121–160, 169–200: baseline comparator, mode override, delayed price exits and flat fees |
| PR #79 `docs/research/edge-validation/EDGE-1-baseline-v1-spec.md` | reconstructed draft explicitly leaves numeric production config unpinned; old title says frozen at bc1712f |
| PR #79 `docs/decisions/ADR-037-edge-validation-research-contracts-v1.md` | Proposed, not accepted; separate edge contracts, interim no-consumer/no-evidence rule |
| PR #81 `docs/research/edge-validation/EDGE-VP-1-validation-protocol.md` | sections 2–4: freeze/data/cost prereqs, committed registration and confirmatory records, config information cutoff |

## Exact source configuration/version inventory

The following values are verified **source facts**, not attested production values. There is no verified production configuration in the inspected evidence.

| Component | Exact source fact | Production-observed value |
|---|---|---|
| P&F required fields | symbol, box_size, reversal_boxes, price_precision, price_source, version; no constructor defaults | UNVERIFIED for every resolution |
| Adaptive box | required mode, fixed_box_size, price_precision, rule_version; defaults atr_period=14, atr_multiplier=1.0, min_box_size=0.1, max_box_size=50.0 | UNVERIFIED |
| Pipeline | required version, resolutions, structure_resolution, regime, signals, stale_after_events; requires >=3 unique resolutions and matching symbol/source | UNVERIFIED |
| Runtime | required pipeline + units; paper=None; implementation_version=`research-pipeline-v2` | UNVERIFIED |
| Signal required | symbol, cooldown_events, expiry_events, version | UNVERIFIED |
| Signal weights | reversal 20, structure 25, S/R 20, matrix 20, regime 15, pattern confirmation 10, conflict penalty 15 (subtracted) | UNVERIFIED |
| Signal thresholds | wait_score_max=49, action_score_min=65, action_gap_min=8 | UNVERIFIED |
| Signal geometry | pattern_price_tolerance=0.8; entry_zone_half_width=0.5 box; target_rr_tp1=1.5; target_rr_tp2=2.5 | UNVERIFIED; not an exit-management decision |
| Legacy pattern version | `p8a-pattern-v1` and `p8b-pattern-v2` emitted by legacy SignalEngine pattern evidence | Observed artifact versions UNVERIFIED |
| Optional analytical features | separate resolved feature config/hash; startup unset uses disabled defaults | Effective feature document/hash and algorithm descriptor versions UNVERIFIED |

Exact **example** inventory (`research-config.json`; never promote these values to production): symbol XAUUSD, units USD/oz, pipeline `example-only-v2`; structure_resolution fast; stale_after_events 100; resolutions fast/medium/slow with box_size and fixed_box_size 0.5/1/2 respectively; each price_precision 1, price_source close, reversal_boxes 2; P&F version and sizing rule_version fast-v2/medium-v2/slow-v2. Each sizing mode fixed, atr_period 2, atr_multiplier 1, min 0.1, max 5. Regime `regime-v2`: lookback 4, trend_min_slope 2, high_volatility_min_width 8, hysteresis 0.5. Signals `signal-v2`: cooldown 1, expiry 20; omitted optional signal fields inherit the defaults above. paper null; omitted runtime implementation version inherits research-pipeline-v2.

Exact **legacy comparator example** (`baseline-config.json`): same example pipeline; mode baseline, baseline_lookback 2, seed 7, split eval, engine_version p10-v1, signal_version p8-signal-v1; cost-v1 commission 0.05, currency USD, slippage 0.02, spread 0.1; exec-v1 entry_delay_seconds 2, exit_delay_seconds 5, sizing_assumption 1, stop_distance 1.5. These are assumptions/examples, not verified costs, broker capabilities or executable baseline policy. Symbol spelling here documents existing source only and must not become a broker assumption in new core logic.

## Logic findings

1. The implemented source chain is AdaptivePnfRunner -> Matrix -> Structure/Trendline/Pattern engines -> Regime -> legacy SignalEngine -> Entry Readiness output. `pipeline.py:123–127` calls SignalEngine without PatternEngine/EntryReadiness results; do not imply optional classic PatternEngine or readiness automatically authorizes or changes BUY/SELL. Legacy pattern evidence can affect legacy points. Full evidence codes plus algorithm versions must identify patterns (ADR-030).
2. P&F uses quantized event price; first event seeds, later whole-box moves extend/reverse, with configurable reversal box count. Replay must retain event sequence, price_source, precision, warm-up, box history and configuration changes; bar OHLC cannot silently stand in for tick movement order.
3. Score is clamped dominant-minus-conflict, with gap/score gates and forced WAIT guards. Setup reference is latest transition to_price; entry zone is reference +/- half-width boxes. Invalidation uses latest support/resistance level or low/high pivot fallback. BUY risk is **entry-zone low minus invalidation**, SELL risk invalidation minus **entry-zone high**; TP prices are reference +/- configured R multiples. This is not necessarily reward/risk from an actual bid/ask fill. Choosing a filled-entry interpretation needs Quant approval.
4. Legacy BacktestRunner baseline mode is close-momentum over baseline_lookback completed bars, **not P&F Baseline V1**. Its P&F modes override sizing.mode to fixed/atr. `_to_trade` ignores signal targets and invalidation and uses delayed single-price exits plus flat spread/commission/slippage. It cannot establish fidelity of TP1/TP2, partial closes, stop, swap or actual fill costs.
5. PR #79 draft wording “frozen at bc1712f” must not be interpreted as full freeze: it explicitly says production values are not pinned, and current audited main is different. A fresh exact-source delta/config attestation is required. ADR-037 remains Proposed; no acceptance was inferred from owner preference for Option 1.

## Freeze proposal — gates, not selected values

1. Preserve this audit as a source inventory only. Obtain a **small operator-made non-secret attestation** of the actual observation process: source clean full SHA and fingerprint, time interval, runtime identity, approved JSON contents + canonical hash, resolved feature document/hash, stream identity, adapter/capability snapshot and source/normalization/time-offset versions. Do not attach environment files containing secrets or start/read heavy production storage for this task.
2. Reconcile attestation with the exact source/config segment actually used. Multiple configurations/versions create separate baseline candidates/segments; never combine them under a single invented default. Pin per-resolution P&F and sizing versions and effective numerical values, complete regime/signal values (including inherited defaults), units, features and algorithm descriptors. Record unresolved fields as unresolved; no BOX 10/20/30 inference.
3. Quant states information cutoff and data already seen, selects trade-plan semantics and all Track C decisions **before validation results**, and signs the config/execution/cost policy. Unknown cutoff blocks confirmatory registration. Historical data earlier than the cutoff may support exploration, never pretend to be unseen.
4. Rin resolves ADR-037 and independent review findings. Approve write-once registry/records and provenance enforcement before any registered run; do not create a holdout unlock. Keep signal generator freeze separate from exit simulator approval.
5. Record Baseline V1 only after all above evidence exists: exact clean code SHA, complete config canonical hash, feature hash, execution/cost contract hashes, dataset/manifest hash, split/registry hash, information cutoff, preregistration/protocol hash, reviewers and approval date. All inherited defaults must be expanded in the frozen payload. No strategy tuning based on validation/holdout.

## Blockers and next review gate

- **A1:** actual production-observation config/process attestation unavailable. Owner/operator evidence needed; repository examples cannot unblock.
- **A2:** information cutoff unknown. Quant declaration before eligible interval selection needed.
- **A3:** no approved SL/TP/partial-close/sizing/end-window execution semantics; legacy runner does not measure this strategy. Track C decisions and fidelity tests needed.
- **A4:** ADR-037 Proposed, protocol/registry/provenance gates not satisfied. Rin architecture acceptance and Track D review needed.

Tests: documentation-only source audit. `git diff --check` required before commit; application tests NOT RUN because no code/config/runtime behavior changed. No profit calculation or data run performed. Self-review only; independent review pending. Next gate: **RIN REVIEW GATE**, review inventory and request non-secret attestation + Quant decisions. No merge, PROD start, execution submission, holdout unlock, tuning or profitability authorization.
