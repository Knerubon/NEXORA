# ADR-037 — Edge-validation research contracts and their relation to ADR-014 / ADR-030

Status: **Proposed. Not accepted.** Drafted for the Architect/Rin decision on review finding B-3 of PR #79. The Owner chose Option 1 (keep `edge/*` separate) on 2026-10-13; this document is written on that basis. Nothing in it is in force until Rin accepts it; until then the interim rule in "Until a decision exists" applies.
Date: 2026-10-13
Workstream: EDGE-VALIDATION-1, `claude/edge-validation-v1` (PR #79), acting as ARCHITECT for the proposal only (no self-acceptance)
Base: `origin/main` `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`
Related: [ADR-014](./ADR-014-backtest-lab-reproducibility.md) (accepted), [ADR-030](./ADR-030-replay-validation-framework-v1.md) (approved), [EDGE-2](../research/edge-validation/EDGE-2-engineering-components.md), Issue #77, review reports for PR #79 (Agent B review, round-3 re-review)

Numbering: ADR-036 is used by the open Decision Audit Trail / Data Quality Guard PR (#80), so this proposal is numbered 037.

## Context

PR #79 adds `packages/nexora/edge/*`, a research-only toolkit for validating whether the P&F baseline has an edge. It introduces three things that look like shared contracts that ADR-014 already freezes:

| Concern | ADR-014 (frozen, `packages/nexora/backtest/models.py`) | `edge/*` (PR #79) |
|---|---|---|
| Split | `BacktestConfig.split: Literal["train", "eval"]` (a label on a whole run) | `SplitPlan` with absolute UTC boundaries, `train` / `validation` / `holdout`, purge gap, locked holdout with unlock + access log, walk-forward windows inside `[start, validation_end)` |
| Costs | `CostPolicy(spread, commission, slippage, currency, version)`, all known `Decimal`s | `CostModel` whose components are `known` / `unknown` / `unavailable`; unknown is never zero; net is "known-cost net" and flagged incomplete |
| Run identity | `BacktestRun.run_id`, `config_hash`, `dataset_hash`, `assumptions_hash` | `EdgeExitRun.run_id` over exit policy, cost model, manifest hash, split registry hash, segment, plan hash and trades |
| Dataset | `DatasetManifest` + `verify_events` | `EdgeDatasetManifest` *wraps* the ADR-014 `DatasetManifest` (reused, not replaced) and adds provenance, quality audit, `ohlc_basis`, split-plan hash |

ADR-030 (VALID-1) reuses the ADR-014 `split` concept and requires pre-registration (G9: splits are part of the hashed run manifest, fixed before Stage A).

AGENTS.md section 3 forbids incompatible versions of a shared contract. Review finding B-3 (MAJOR) asks for an Architect/Rin decision on whether `edge/*` extends ADR-014/ADR-030 or is a separate research contract, and how `eval` maps to `validation` / `holdout`.

Facts that bear on the choice (verified at PR #79 HEAD):

- No code outside `nexora.edge` imports `nexora.edge` (`git grep -l "nexora.edge" -- ':!packages/nexora/edge' ':!tests'` lists only the EDGE-2 research doc). The `edge/*` types have no consumers.
- `packages/nexora/{backtest,signals,risk,execution,market_data}` are unchanged by PR #79 (`git diff 36c90c59 -- <paths>` empty). `BacktestRunner` and its fixed-delay exit are untouched.
- `edge/*` statistical evidence eligibility is constant `False` (`nexora.edge.evidence`), so nothing it produces can be cited as evidence today.

## Options

### Option 1 — Separate research-only contract, with an explicit mapping to ADR-014 (chosen by the Owner)

`edge/*` is recorded as its own research contract family (`edge-research/v1`) that **does not** replace or extend ADR-014. ADR-014 stays frozen and unchanged. The relation is fixed by a one-way mapping:

| `edge/*` | ADR-014 / ADR-030 equivalent | Rule |
|---|---|---|
| `train` segment | `split="train"` | Exploration allowed (definitions, parameters, sensitivity). |
| `validation` segment | `split="eval"` (non-final) | Out-of-sample check during development. Results are labelled `validation`, never `eval` alone. |
| `holdout` segment | `split="eval"` (final, pre-registered) | The ADR-030 G9 "pre-registered eval segment never used for exploration". Read only with a `HoldoutUnlock` + `HoldoutAccessLog`, and only under the plan recorded in the split registry. |
| `unsplit` run | none | Never evidence; always labelled `unsplit_run_not_valid_out_of_sample`. |
| `CostModel` known components | `CostPolicy` fields | A `CostModel` whose components are all `known` maps to a `CostPolicy` with the same values. A `CostModel` with any `unknown` / `unavailable` component has **no** `CostPolicy` equivalent and must not be converted (that would turn unknown into zero). |
| `EdgeExitRun.run_id` | `BacktestRun.run_id` | Different identity spaces. An edge run is never stored or compared as a `BacktestRun`. |
| `EdgeDatasetManifest.base` | `DatasetManifest` | Same object; `base_hash` is the ADR-014 dataset hash. |

Consequences: no frozen contract changes; ADR-014 consumers (Backtest Lab API/UI, VALID-1) are untouched. The cost is two vocabularies; the mapping table above is what keeps them from drifting. A later ADR can still promote parts of `edge/*` into ADR-014 once a consumer needs them.

Safety: no live path is added; holdout protection and fail-closed costs stay inside `edge/*`.

### Option 2 — Extend ADR-014

`BacktestConfig.split` gains `validation` / `holdout`; `CostPolicy` gains explicit unknown/unavailable states; `edge/*` uses the extended contract.

Consequences: one vocabulary, but it changes a frozen contract with live consumers (Backtest Lab runner, API, UI, fixtures, VALID-1 references). AGENTS.md section 3 then requires stopping dependent work, evaluating every consumer and updating contract tests. A `CostPolicy` that can hold "unknown" also changes the meaning of every existing `fees` / `net_pnl` computed by `BacktestRunner`.

Safety: higher regression risk in an accepted subsystem for no current consumer benefit.

### Option 3 — Hold PR #79 unmerged

Keep `edge/*` off `main` until VALID-1 / Backtest Lab consumers are designed, then choose 1 or 2.

Consequences: no contract risk, but the validation-readiness work (dataset manifests, holdout lock, side-aware exits) stays on a long-lived branch and drifts from `main`.

## Chosen direction (pending Rin acceptance)

Option 1, chosen by the Owner on 2026-10-13 (decision card in the NEXORA project, relayed to this workstream). Rationale: it is the only option that changes no frozen contract, matches the fact that `edge/*` has no consumers, and keeps the stricter semantics (locked holdout, unknown-is-not-zero costs) out of ADR-014 until something needs them.

## Holdout pre-registration record (review findings B-2 / R-1)

ADR-030 G9 requires splits to be fixed before evaluation. PR #79 implements this in two layers; this ADR proposes the second layer as the named independent record the round-3 re-review asked for (R-1):

1. **Per manifest.** `build_edge_manifest(..., split_plan=...)` hashes the plan into the manifest; a run must use exactly that plan and can never be unsplit once one is registered.
2. **Per market stream (new, `nexora.edge.registration`).** A `SplitRegistry` records, for each `(source, symbol)` stream, which `SplitPlan` governs which time range. Streams are compared by a normalised key (NFKC, case-folded, non-alphanumerics removed), so case, spacing and separator differences are the same stream; names that are not ASCII after NFKC (look-alike dashes or letters) are refused. `run_exit_mode` requires the registry and its expected hash and refuses:
   - an unsplit run over any dataset whose events overlap a registered plan's range (`registered_split_requires_split_run`), even through a second manifest built over the same or trimmed events, or shifted events that still overlap the range;
   - any dataset of a registered stream that lies outside every registered range (`stream_registered_dataset_outside_registered_ranges`), so the same bars with moved timestamps cannot run unsplit;
   - a split different from the registered one (`split_plan_not_registered_for_dataset`);
   - a split with no registry entry (`split_not_in_registry`);
   - a dataset that spans two registrations, and overlapping registrations for one stream.

   The registry serialises to canonical JSON (`SplitRegistry.to_json`) with a per-entry plan hash cross-check, so it can be committed and reviewed.

Proposed operating rule (needs acceptance): the split registry for any dataset used in edge validation is a committed, reviewed JSON file, and its `registry_hash` is pinned in the same independent record as the expected manifest hash. Adding a registration is a reviewed change; editing or removing one is not allowed (a new plan is a new registration over a new time range). A range must be registered before any run touches it, and a range that was already run unsplit is disqualified from later holdout registration (re-review F-3).

Limits that remain, stated plainly: the registry and both expected hashes are still inputs to the library; they protect the holdout only when they come from the reviewed record. A genuinely different `source` or `symbol` (for example `feed2`) is a different stream, and timestamps are labels too: bars relabelled with a new stream name and moved timestamps are not recognised (a content fingerprint is out of scope). Holdout unlock remains a declaration (`authorization_verified=False`), and statistical evidence eligibility remains constant `False` until independent provenance and authorization mechanisms exist. None of this unlocks the holdout.

## Until a decision exists

- Nothing outside `nexora.edge` may import or consume `edge/*` types.
- No edge result is reported as ADR-014 `eval` evidence.
- PR #79 stays draft and unmerged; merge requires a Rin decision on this ADR plus the AGENTS.md section 20.3 gates.

## Decision record

| Field | Value |
|---|---|
| Owner direction | Option 1, keep `edge/*` a separate research contract with the mapping above (2026-10-13) |
| Rin acceptance | _pending_ (status stays Proposed until recorded) |
| Accepted by / date | _pending_ |
| Pre-registration record rule accepted | _pending_ |
