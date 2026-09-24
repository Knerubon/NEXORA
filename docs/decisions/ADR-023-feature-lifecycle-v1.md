# ADR-023 — NEXORA Feature Lifecycle V1 (ACTIVE / SHADOW / DISABLED)

Status: **proposed — draft, NOT accepted** (awaiting Rin architecture review; Quant review for Decision 7)
Date: 2026-09-24
Workstream: Track D — `claude/pnf-pattern-engine-v1` (DEV-PNF acting as ARCHITECT for the proposal)
Base: `origin/main` `e2ba8ba`
Related: [ADR-011](./ADR-011-signal-evidence-policy.md), [ADR-014](./ADR-014-backtest-lab-reproducibility.md), [ADR-020](./ADR-020-pnf-trendline-v1.md), [ADR-021](./ADR-021-entry-readiness-v1.md), ADR-022 (Track B, unmerged), [ADR-024](./ADR-024-pnf-pattern-engine-v1.md) (first adopter), [environment isolation](../environment-isolation.md)

## Context

NEXORA has no feature-control mechanism. Every engine in `ResearchPipeline` always runs, and its output always reaches downstream consumers. Experimental chart-analysis features (first the P&F Pattern Engine, ADR-024; later Breakout, Buy/Sell Force, AI Chart Analyst) must be controllable without deleting code. They must be able to run without influencing decisions, and must fail closed.

Repository facts that constrain the design (verified at `e2ba8ba`):

1. **Config hash = stream identity.** `ResearchRuntime.stream = "research:" + canonical_hash(RuntimeConfig)`. `canonical_serialize` uses `dataclasses.asdict`, so adding **any** field to `RuntimeConfig`/`PipelineConfig`, even one with a default, changes the stream id. The runtime would then start a new, empty research stream, and Experience `scope_for()` (which also hashes the config) would change too.
2. **Journal rows store the full pipeline output.** Anything added to that output is persisted per event (ADR-022 names cumulative output as the root cause of recovery cost).
3. **Track B checkpoints pickle the whole `ResearchPipeline`** and validate only code fingerprint, journal anchors and a state hash (ADR-022 Decision 1–3). Config that lives outside `RuntimeConfig` is not part of that validation.
4. Existing status vocabulary: Matrix `ResolutionStatus = ready | warmup | stale | unavailable`; Structure level `unavailable`; startup rejects invalid env values (`invalid_research_checkpoints`).
5. Core packages must not read environment variables or depend on FastAPI (architecture.md).

## Decision 1 — Scope: which features are lifecycle-controlled

Only **optional analysis features** registered in a closed, code-level registry are lifecycle-controlled. Core data paths are **never** lifecycle features: market data, quote, P&F, Adaptive Box, Matrix, Structure, basic chart. The current production decision chain (Structure, Trendline, Regime, Signal, Entry Readiness, Risk, Paper) is not converted by this ADR.

V1 registry: `pattern_engine` (ADR-024) and its algorithms. Adding a feature requires its own ADR, which must reference this one.

## Decision 2 — Lifecycle states

```python
FeatureLifecycle = Literal["ACTIVE", "SHADOW", "DISABLED"]
```

Ordered `DISABLED < SHADOW < ACTIVE`. Semantics:

| | Computes | Output published | Output may change decision semantics (Signal action/score, Entry Readiness, Risk, Paper, orders) | Visualized |
|---|---|---|---|---|
| ACTIVE | yes | yes, tagged `ACTIVE` | only through a consumer contract frozen by its own ADR | yes |
| SHADOW | yes | yes, tagged `SHADOW` | **never** | yes, visibly marked shadow |
| DISABLED | **no** (computation skipped) | explicit disabled status, **zero** results | never | nothing drawn |

DISABLED skips computation rather than computing and discarding. Reasons: no hidden cost, no stale state carried across, and one uniform rule for every feature. A DISABLED feature keeps no accumulated state. Re-enabling it takes effect only through a full rebuild (Decision 6).

## Decision 3 — Effective lifecycle (engine × algorithm × ceiling)

A feature may have sub-units (algorithms). The effective lifecycle of a sub-unit is:

```
effective = min(ceiling, feature_lifecycle, unit_lifecycle)
```

- `feature_lifecycle` is the engine-level switch. `unit_lifecycle` is per algorithm, so one defective algorithm can be disabled without disabling the engine.
- `ceiling` is reserved for a future Global Safe Mode (Decision 9). V1 fixes `ceiling = ACTIVE`, which makes it a no-op.
- Engine DISABLED ⇒ every unit DISABLED. Engine SHADOW + unit ACTIVE ⇒ unit SHADOW.

## Decision 4 — Health is separate from lifecycle

```python
FeatureHealth = Literal["ready", "warmup", "degraded", "unavailable", "not_evaluated"]
```

| Health | Meaning |
|---|---|
| `ready` | Evaluated this step on valid inputs; results are valid |
| `warmup` | Evaluated; inputs valid but insufficient (for example not enough confirmed pivots). No results; not an error |
| `degraded` | Engine level only: at least one enabled unit is `unavailable` while at least one other is `ready`/`warmup` |
| `unavailable` | Evaluation failed: invalid/inconsistent input, contract/version mismatch, or exception. Results of the failed unit are dropped for this step |
| `not_evaluated` | Effective lifecycle is DISABLED |

Health is reported per unit and aggregated per feature, each with machine `reason_codes`. `ACTIVE` never implies `ready`: consumers must check both (Decision 5).

## Decision 5 — Feature status contract (published every step)

Each lifecycle-controlled feature publishes a status block beside its results in the pipeline output:

```python
@dataclass(frozen=True, slots=True)
class FeatureUnitStatus:
    unit_id: str                     # e.g. "legacy_pivot.double_top"
    configured_lifecycle: FeatureLifecycle
    effective_lifecycle: FeatureLifecycle
    health: FeatureHealth
    reason_codes: tuple[str, ...]
    algorithm_version: str

@dataclass(frozen=True, slots=True)
class FeatureStatus:
    schema_version: Literal[1]
    feature_id: str                  # e.g. "pattern_engine"
    configured_lifecycle: FeatureLifecycle
    effective_lifecycle: FeatureLifecycle
    health: FeatureHealth
    reason_codes: tuple[str, ...]
    engine_version: str
    feature_config_version: str      # human label from the feature config file
    feature_config_hash: str         # canonical_hash of the resolved feature config
    units: tuple[FeatureUnitStatus, ...]
```

**Downstream usability rule (frozen, fail-closed).** A result may influence decision semantics only if **all** of the following hold. Otherwise the consumer must ignore it and record the first failing reason code:

| Check | Ignore reason code |
|---|---|
| feature `effective_lifecycle == ACTIVE` and unit `effective_lifecycle == ACTIVE` | `ignored_shadow` / `ignored_disabled` |
| feature health ∈ {`ready`, `degraded`} and unit health == `ready` | `ignored_unhealthy` |
| `engine_version` and `algorithm_version` are among the versions the consumer's own ADR pins | `ignored_version_mismatch` |
| result status is one the consumer's ADR accepts (for ADR-024: `confirmed`) | `ignored_status` |
| status/result belong to the same step as the consumer's other inputs (same pipeline output) | `ignored_stale` |

These codes are the traceability vocabulary for "evidence existed / was used / was ignored and why". A consumer that adopts a feature must record per result `(result_id, used | ignored, reason_code)` in its own output. Designing that record is the consumer's ADR, not this one.

A missing, undecodable or schema-mismatched status block is treated as `DISABLED` + `unavailable` (fail closed). `enabled == healthy` must never be assumed.

## Decision 6 — Configuration source, persistence and reproducibility

**6a. Feature config is not part of `RuntimeConfig` in V1.** Putting it there would change the stream id (Context 1) for every existing environment, including PROD. Instead:

- A separate JSON file is named by `NEXORA_FEATURES_CONFIG` and resolved by the API layer (`apps/api`), like `NEXORA_RESEARCH_CONFIG`. It is passed into `ResearchPipeline` as an explicit constructor argument. Core code never reads the environment.
- Schema: `{"schema_version": 1, "version": "<label>", "features": {"pattern_engine": {"lifecycle": "...", "units": {"<unit_id>": "..."}}}}`. Unknown feature/unit ids, unknown keys, invalid literals or an unreadable file make startup fail with `invalid_features_config`, following the precedent of `invalid_research_checkpoints`. Units that are not listed inherit the feature lifecycle.
- **Variable unset ⇒ every registered feature DISABLED.** This is the code default, identical in every environment, so there is no env-conditional code.
- Backtests construct `ResearchPipeline` directly. They get the all-DISABLED default unless a `BacktestConfig` explicitly carries a feature config. Any such field is a later ADR-014 change, not V1.

**6b. Why this is safe for reproducibility in V1.** In V1 the only lifecycles a feature config may select are `SHADOW` and `DISABLED` (Decision 7). Neither can change decision semantics, so decision outputs are a function of `RuntimeConfig` + events alone, exactly as today. The feature config affects only the feature's own published block, and every journal row self-describes it through `FeatureStatus` (`configured`/`effective` lifecycle per unit, `feature_config_hash`, versions). Historical rows therefore answer "ACTIVE/SHADOW/DISABLED at this event? which units? which versions? which config?" without Research Journal changes.

**6c. Rebuild rule.** A feature-config change takes effect only at process start. On full replay the feature state is rebuilt under the new config. Stored journal rows are never rewritten, and they keep the status in force when they were produced. In-memory replayed feature state may therefore differ from the historical rows' feature block. That is acceptable only because V1 features cannot influence decisions (6b).

**6d. Checkpoint interaction (Track B).** A restored ADR-022 checkpoint would carry the pickled feature config of the process that wrote it, silently overriding a changed file. So `feature_config_hash` must be added to the checkpoint validation key (mismatch ⇒ full replay). This is a change to a Track B file (`packages/nexora/research/checkpoint.py`/`runtime.py`). **ARCHITECT DECISION REQUIRED:** land it after Track B merges, coordinated with DEV-PERF. Until then the feature must not ship to any environment with checkpoints enabled.

## Decision 7 — ACTIVE is not selectable in V1

Config validation rejects `ACTIVE` for every V1 feature (`feature_lifecycle_not_permitted`). Reasons:

- No consumer contract for Pattern Engine results exists yet (ADR-024 Decision 11), so ACTIVE would behave identically to SHADOW.
- ACTIVE changes decision semantics, and that requires the config to become part of stream identity (6b no longer holds).

Promoting a feature to ACTIVE requires a new ADR that:

- defines the consumer contract;
- moves that feature's config into the reproducible, hashed decision config (with the new-stream or migration consequence decided explicitly);
- obtains Quant approval for any scoring/permission change.

## Decision 8 — DEV / PROD policy

There are no environment-conditional code defaults. Policy is expressed by which file each environment's `.env.<env>` names:

- DEV: may name a features file selecting `SHADOW`.
- PROD: `NEXORA_FEATURES_CONFIG` stays unset (everything DISABLED) until a feature is validated and a human approves a PROD change. This track changes no PROD configuration.
- Open question Q-FL3: should `NEXORA_ENV=production` additionally refuse any non-DISABLED lifecycle unless an explicit allow-list is approved?

## Decision 9 — Global Safe Mode compatibility (design only, not implemented)

A future `NEXORA_SAFE_MODE` would set `ceiling` (Decision 3) for all registered features to `DISABLED` (or `SHADOW`). Core paths are unaffected because they are not registered features (Decision 1). No V1 code implements Safe Mode. V1 only guarantees that one global ceiling can be applied centrally in the lifecycle resolver without per-feature changes.

## Decision 10 — Display toggles are not lifecycle

UI layer toggles (for example the chart's Patterns layer) are client-side visibility only. They never change, request or infer a lifecycle. A DISABLED feature with its layer ON draws nothing, and the frontend never reconstructs results from other fields.

## Consequences

- One reusable resolver `resolve(ceiling, feature, unit) -> FeatureLifecycle` plus the `FeatureStatus` contract in a new pure module (proposed `packages/nexora/features.py`).
- Adds one optional environment variable. Example files document it commented out. No PROD change.
- Pipeline output grows by one bounded status block per feature.
- V1 cannot produce decision-affecting feature output, which is intentional.

## Out of scope

Global Safe Mode implementation; a runtime (non-restart) lifecycle switch or API; ACTIVE promotion; converting existing engines to lifecycle features; Research Journal V2; backtest feature config.

## Open questions

- **Q-FL1** Is a separate `NEXORA_FEATURES_CONFIG` acceptable? The alternative is a field on `PipelineConfig`, which starts a new research stream and Experience scope in every environment.
- **Q-FL2** Sequencing of Decision 6d with Track B (after merge, or folded into ADR-022 follow-up).
- **Q-FL3** Production allow-list guard (Decision 8).
- **Q-FL4** Should `feature_lifecycle_not_permitted` for ACTIVE be relaxed per feature by a later ADR, or should ACTIVE promotion always move config into `PipelineConfig`?
