# Track D — Independent adversarial readiness review

Date: 2026-10-15. Role: independent QA / adversarial reviewer. Owner: `/root/track_d`.
Status: **CHANGES REQUESTED — RIN REVIEW GATE.** No merge or execution authority.

## Exact targets and ownership

- Main/base: `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`.
- PR #79: `196a962fd6fe2be41e0acfd70f6f7ec3b7a240db`.
- PR #81: `49bd2d6c9d8cf11c0e78a1e209ccc0cbb5eb4282`.
- Report branch: `codex/edge-readiness-d-20261015-v1`; worktree: `/workspace/scratch/8df221b1c96d/readiness-d`.
- Review-only detached checkout: `/workspace/scratch/8df221b1c96d/review-d-pr79` at exact PR #79 target. Existing Claude branches and worktrees were not edited.
- Own path: this file only. Branch absence/worktree inventory checked before creation; new report tree initially clean and HEAD matched base. MASTER supplied remote branch/open-PR ownership checks. Remote state is point-in-time; any later PR HEAD requires delta review.

## Inspected sources

Read main `AGENTS.md`, cloud test rules in `CLAUDE.md` and `docs/development.md`, accepted ADR-014 and ADR-030 (especially knowledge time, G9/G10, provenance and isolation). At PR #79 read ADR-037 (Proposed), the complete edge dataset/split/registration/cost/evidence/exit implementation and engineering fixtures. At PR #81 read complete EDGE-VP-1 protocol. Inspected changed-path inventories for both targets. PR #79 has 19 added files; PR #81 has one added protocol document relative to this main. Issue #77 and remote review/check state are MASTER-owned evidence; this independent source review does not claim to have queried them itself.

## D-1 — MAJOR: malformed ambiguity policy silently takes favorable target

PR #79 `packages/nexora/edge/exits.py:88–96` validates concurrency and intervals but never validates `ambiguity`. At lines 346–354 a dual-hit bar selects the stop only for the exact string `stop_first`; every other object/value takes `target_first`. Python Literal annotations do not enforce public configuration at runtime.

Reproduced with engineering-only fixture: bar open100/high110/low90/close100; long decision at T0, stop95/target105, five-minute interval. `ambiguity='stop_first'` exits95/stop; `'target_first'` exits105/target; **`'typo'` and `None` both exit105/target without error**. A malformed loaded configuration therefore silently changes a loss to a win. No live data, holdout, broker or order path was involved.

Required correction by PR #79 owner: reject values outside the exact closed policy vocabulary before simulation, including malformed runtime values; add regression cases proving refusal. This review chooses no strategy policy. `plan_from_signal` checks target membership and has no analogous fallback from arbitrary target name to TP2.

## D-2 — MAJOR: same run identity can describe different cost/net outputs

PR #79 `packages/nexora/edge/costs.py:109–112` and `exits.py:394–418` use ambient Decimal context. `exits.py:628–640` hashes inputs and exit objects, but not calculated costs/net nor the arithmetic context. Consequently the same recorded inputs and run id produce different results depending on caller context; this violates the deterministic identity/reproduction expectation in ADR-014, ADR-030 G10 and PR #81 section12.

Engineering fixture: one bid bar open100/high105/low99/close105, bid100/ask101; long stop95/target105 at T0; unit_size1; known commission0.123456789; all other costs explicitly known zero; unsplit synthetic manifest and empty synthetic registry.

| Decimal precision | known costs | known-cost net |
|---|---|---|
| 6 | 0.123457 | 4.87654 |
| 28 | 0.1234567890000000000000000000 | 4.876543211000000000000000000 |

Both runs produced **`edge-exit-run:71325d962be23a4849577ef3ed97072436d09515baba381653d6421cd5c79851`**. Because reproduction checks run hash, a hash-only comparison can falsely confirm differing financial output. Required correction: Architect/Quant approve explicit arithmetic semantics, bind them to run provenance, and ensure result identity detects differing computed outputs. Do not silently choose rounding/precision in this report.

## Other controls, residual gaps and blockers

- **Holdout controls are procedural, not access security.** `run_exit_mode` requires registry/hash and registered split; matching-stream alternate manifests cannot simply run unsplit. Stream normalization and refusal outside registered ranges close the specifically claimed in-stream escapes by inspection. Caller can still replace registry plus expected hash, relabel source/symbol, or call low-level `simulate_exit` with already-loaded bars. ADR-037 and PR #81 explicitly disclose caller trust; do not describe these as independent authorization. No bypass was exercised against real data.
- `HoldoutUnlock` is a declaration; logs are memory-only and repeat detection is scoped to that object/plan. Persisted append-only record, reviewed registry/hash source and independent authorization are not implemented. PR #81 records these gaps; no holdout read or unlock occurred in this review, including fixture holdout access.
- **Leakage boundary:** exits enforce decision_time before entry bar open (plus delay); completed segment filtering uses received_at, and OHLC validation requires received_at at/after close. That does not prove caller-supplied TradePlan was derived only from past information. Signal generation/replay prefix parity and future-tail invariance are still required before evidence. PR #81 requires at least one-bar entry delay absent proof signal known before opening; code default zero is not a frozen strategy value.
- Segment-boundary open trades are returned as `open_at_end`, excluded from closed-trade statistics and explicitly counted. This is censoring, not a demonstrated cross-segment execution read. With max_hold_bars=None there is no finite maximum holding period from which to derive protocol purge; Quant must resolve bounded holding/end-window treatment and censoring criteria before freezing purge. No invented numeric purge here.
- Walk-forward bounds stay before validation_end and enforce minimum plan purge. Register/protocol/config/metric/cost decisions must predate confirmatory validation; known config information cutoff must precede applicable unseen windows. Protocol thresholds remain unresolved, correctly blocking evidence.
- **Reproducibility:** code SHA/fingerprint/dirty flag absent from EdgeExitRun is an already disclosed P7 gap. D-2 adds a distinct, reproduced hash/output mismatch even at fixed source SHA. Full bundle byte comparison should accompany any future hash identity mechanism.
- **Execution isolation:** changed production engine, API, broker, execution and runtime paths: none in PR #79; PR #81 is docs only. Edge imports data/models/signals/artifacts, not broker submit or runtime startup. No runtime processes were started. This is source-boundary review, not a live integration proof.
- Evidence provenance/authorization/eligibility remain false through evaluate_gates. A real_market label is not verified reality; no profitability assertion is possible here.
- ADR-037 remains Proposed; acceptance and pre-registration storage/authority decisions are Rin gates. PR #81 is a draft protocol, not implementation or numerical Quant approval. No new independent blocker specific to the PR #81 text was established in this pass; it remains ineligible for freeze while its listed entry gates/open decisions and engine defects remain unresolved.

## Tests and limitations

Executed in detached exact PR #79 checkout with `PYTHONPATH=packages:tests python` (installed CPython3.12; project-required3.13 unavailable): two small inline scripts importing `edge_fixtures`, edge costs/exits, constructing the cases above and printing returned results. Both reproductions completed exit0. Synthetic fixtures establish engineering defects only, never trading edge.

Attempted: `PYTHONPATH=packages:tests python -m pytest -q tests/test_edge_costs.py tests/test_edge_dataset.py tests/test_edge_exits.py tests/test_edge_splits.py tests/test_edge_hardening.py tests/test_edge_hardening_r2.py tests/test_edge_hardening_r3.py tests/test_edge_hardening_r4.py`.

Result: **NOT RUN** (exit1: `No module named pytest`). No dependencies installed or required interpreter substituted claim made. Full suite, lint/type checks, future-tail invariance and production/Windows integration were not run. `git diff --check` passed in clean review checkout; report whitespace check performed before commit. No CI result inferred from PR prose.

Reproduction recipe for D-1: use `make_bar(0,'100','110','90','100',bid='100',ask='101')`, `TradePlan('fixture-only','long',T0,Decimal('95'),Decimal('105'))`, and for each ambiguity construct `ExitPolicy('fixture','one_position',BAR,ambiguity=value)` then `simulate_exit(...,ohlc_basis='bid')`.

Reproduction recipe for D-2: use `make_bar(0,'100','105','99','105',bid='100',ask='101')`, synthetic_manifest, the same plan, `CostModel('fixture','SYNTH-UNIT',KNOWN_ZERO,CostComponent('known',Decimal('0.123456789')),KNOWN_ZERO,KNOWN_ZERO)`. Within localcontext precisions6/28 call run_exit_mode with unit1, the manifest/hash, ExitPolicy('fixture','one_position',BAR), and registry_args(). Compare run_id and trade net/cost fields.

## Next review gate

PR #79 owner receives D-1/D-2 for correction and regression evidence on Python3.13, then independent exact-HEAD delta review. Architect/Quant resolve arithmetic/provenance and remaining policy contracts; Rin accepts or rejects proposed ADR-037/protocol freeze only with complete decisions and data evidence. Historical readiness remains blocked; no holdout/evidence run is authorized by this report.

**Stopped at RIN REVIEW GATE. NO MERGE / NO PROD START / NO ORDER SUBMISSION / NO HOLDOUT UNLOCK / NO HOLDOUT TUNING.**
