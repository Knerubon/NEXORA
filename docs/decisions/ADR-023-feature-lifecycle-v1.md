# ADR-023 — NEXORA Feature Lifecycle V1 (ACTIVE / SHADOW / DISABLED)

Status: **accepted for Phase 2** (Rin architecture review 2026-09-24, on revision 2 at `14f30ae`; Q-FL3 and Q-FL4 resolved below). Quant review is still required for any decisional ACTIVE promotion (Decision 7).
Date: 2026-09-24
Workstream: Track D — `claude/pnf-pattern-engine-v1` (DEV-PNF acting as ARCHITECT for the proposal)
Base: `origin/main` `4f69e9c` (revision 1 was drafted on `e2ba8ba`, before ADR-022 merged)
Related: [ADR-011](./ADR-011-signal-evidence-policy.md), [ADR-014](./ADR-014-backtest-lab-reproducibility.md), [ADR-020](./ADR-020-pnf-trendline-v1.md), [ADR-021](./ADR-021-entry-readiness-v1.md), [ADR-022](./ADR-022-startup-recovery-checkpoint-v1.md) (checkpoint recovery, merged), [ADR-024](./ADR-024-pnf-pattern-engine-v1.md) (first adopter), ADR-026 (M30 Next Candle Bias, draft on `claude/m30-next-candle-bias-v1`, analytical adopter), [environment isolation](../environment-isolation.md)

## Revision history

- **Revision 1** (`e2ba8ba`): initial draft. It assumed that ADR-022 checkpoints pickle the whole `ResearchPipeline`, restricted the registry to `pattern_engine`, and rejected ACTIVE for every V1 feature.
- **Revision 2** (`4f69e9c`): synced with merged ADR-022 revision 2 (explicit, versioned JSON checkpoint state). Changes:
  - The pickle assumption is removed. Revision 1's Decision 6d (checkpoint interaction) moves to the new Decision 11 (checkpoint contract).
  - Decision 6d now defines `feature_config_hash` precisely.
  - ACTIVE is split into non-decisional and decisional (Decisions 2 and 7), so analytical features such as M30 Bias (ADR-026) can use ACTIVE without gaining decision authority.
  - The registry is open to features with their own accepted ADR (Decision 1).
  - Q-FL1 and Q-FL2 are resolved. Q-FL4 is reframed; Q-FL3 stays open.

## Context

NEXORA has no feature-control mechanism. Every engine in `ResearchPipeline` always runs, and its output always reaches downstream consumers. Experimental chart-analysis features (first the P&F Pattern Engine, ADR-024; then M30 Bias, ADR-026; later Breakout, Buy/Sell Force, AI Chart Analyst) must be controllable without deleting code. They must be able to run without influencing decisions, and must fail closed.

Repository facts that constrain the design (verified at `4f69e9c`):

1. **Config hash = stream identity.** `ResearchRuntime.stream = "research:" + canonical_hash(RuntimeConfig)`. `canonical_serialize` uses `dataclasses.asdict`, so adding **any** field to `RuntimeConfig`/`PipelineConfig`, even one with a default, changes the stream id. The runtime would then start a new, empty research stream, and Experience `scope_for()` (which also hashes the config) would change too.
2. **Journal rows store the full pipeline output.** Anything added to that output is persisted per event (ADR-022 names cumulative output as the root cause of recovery cost).
3. **ADR-022 checkpoints are explicit, versioned JSON** (header `schema_version` 2, payload `state_version` 1, `packages/nexora/research/checkpoint_state.py`):
   - Configuration attributes are **never** persisted. Every component is freshly constructed from the current startup config, then only its replay-derived state is assigned.
   - Types come only from the module's static schema. Unknown, missing or ill-typed fields fail with `state_invalid`, and every failure falls back to full journal replay.
   - `_OUTPUT_SCHEMA` fixes the pipeline output keys, and `COVERED_FIELDS` fails a contract test when any component gains an attribute. New feature state therefore cannot silently escape, or silently enter, a checkpoint.
   - The checkpoint is bound to the stream (`RuntimeConfig` hash), environment, `code_fingerprint` (any code change invalidates it) and exact journal row anchors. It is **not** bound to anything outside `RuntimeConfig`.
   - Invariant: `STATE(full replay) == STATE(checkpoint + delta replay)`.
4. Existing status vocabulary: Matrix `ResolutionStatus = ready | warmup | stale | unavailable`; Structure level `unavailable`; startup rejects invalid env values (`invalid_research_checkpoints`).
5. Core packages must not read environment variables or depend on FastAPI (architecture.md).

## Decision 1 — Scope and registry

Only **optional analysis features** registered in a closed, code-level registry are lifecycle-controlled. Core data paths are **never** lifecycle features: market data, quote, P&F, Adaptive Box, Matrix, Structure, basic chart. The current production decision chain (Structure, Trendline, Regime, Signal, Entry Readiness, Risk, Paper) is not converted by this ADR.

The registry is **open to any feature whose own accepted ADR registers it**. That ADR must reference this one and declare, fixed in code:

| Registry field | Meaning |
|---|---|
| `feature_id` | Stable id, for example `pattern_engine`, `m30_next_candle_bias` |
| `units` | Closed set of sub-unit ids (algorithms) |
| `feature_class` | `analytical` (non-decisional) or `decisional` (Decision 7) |
| `max_lifecycle` | Highest lifecycle the feature's accepted ADR permits (`SHADOW` or `ACTIVE`) |
| declared consumer contract | What ACTIVE output may be used for, defined in the feature's ADR |

Known registrations:

- `pattern_engine` (ADR-024): `analytical` in V1, `max_lifecycle = SHADOW`.
- `m30_next_candle_bias` (ADR-026): `analytical`. Its `max_lifecycle` is set by ADR-026 or its promotion ADR, not by this ADR.

A feature without an accepted ADR cannot be registered, and config naming an unregistered feature fails startup (Decision 6a).

## Decision 2 — Lifecycle states

```python
FeatureLifecycle = Literal["ACTIVE", "SHADOW", "DISABLED"]
```

Ordered `DISABLED < SHADOW < ACTIVE`.

**ACTIVE means: the feature may serve its declared consumer contract.** ACTIVE never implies trading authority. What ACTIVE permits is exactly the consumer contract declared in the feature's accepted ADR, and nothing more.

| | Computes | Output published | May serve its declared consumer contract | May change decision semantics (Signal action/score, Entry Readiness, Risk, Paper, orders) | Visualized |
|---|---|---|---|---|---|
| ACTIVE, `analytical` | yes | yes, tagged `ACTIVE` | yes: production-visible analytical output only | **never** | yes |
| ACTIVE, `decisional` | yes | yes, tagged `ACTIVE` | yes | only through its accepted consumer ADR (Decision 7) | yes |
| SHADOW | yes | yes, tagged `SHADOW` | no (evaluation and display only) | **never** | yes, visibly marked shadow |
| DISABLED | **no** (computation skipped) | explicit disabled status, **zero** results | no | never | nothing drawn |

**Analytical ACTIVE (for example M30 Bias) is production-visible analytical output only.** It has **no** authority to:

- generate BUY or SELL;
- override Matrix, Signal, Entry Readiness or Risk;
- execute trades.

No decision-chain component may read an analytical feature's output. This is a frozen boundary: a feature cannot gain decision authority through configuration, only through a new accepted ADR that reclassifies it as `decisional` (Decision 7).

DISABLED skips computation rather than computing and discarding. Reasons: no hidden cost, no stale state carried across, and one uniform rule for every feature. A DISABLED feature keeps no accumulated state, and therefore contributes no checkpoint state (Decision 11). Re-enabling it takes effect only through a full rebuild (Decision 6c).

## Decision 3 — Effective lifecycle (engine × algorithm × ceiling)

A feature may have sub-units (algorithms). The effective lifecycle of a sub-unit is:

```
effective = min(ceiling, feature_lifecycle, unit_lifecycle)
```

- `feature_lifecycle` is the engine-level switch. `unit_lifecycle` is per algorithm, so one defective algorithm can be disabled without disabling the engine.
- `ceiling` is reserved for a future Global Safe Mode (Decision 9). V1 fixes `ceiling = ACTIVE`, which makes it a no-op.
- Engine DISABLED ⇒ every unit DISABLED. Engine SHADOW + unit ACTIVE ⇒ unit SHADOW.
- `max_lifecycle` (Decision 1) is a validation limit, not a runtime ceiling: configuring above it fails startup (Decision 7).

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
    feature_class: Literal["analytical", "decisional"]
    configured_lifecycle: FeatureLifecycle
    effective_lifecycle: FeatureLifecycle
    health: FeatureHealth
    reason_codes: tuple[str, ...]
    engine_version: str
    feature_config_version: str      # human label from the feature config file
    feature_config_hash: str         # Decision 6d
    units: tuple[FeatureUnitStatus, ...]
```

**Downstream usability rule for decisional consumers (frozen, fail-closed).** A result may influence decision semantics only if **all** of the following hold. Otherwise the consumer must ignore it and record the first failing reason code:

| Check | Ignore reason code |
|---|---|
| `feature_class == decisional` | `ignored_not_decisional` |
| feature `effective_lifecycle == ACTIVE` and unit `effective_lifecycle == ACTIVE` | `ignored_shadow` / `ignored_disabled` |
| feature health ∈ {`ready`, `degraded`} and unit health == `ready` | `ignored_unhealthy` |
| `engine_version` and `algorithm_version` are among the versions the consumer's own ADR pins | `ignored_version_mismatch` |
| result status is one the consumer's ADR accepts (for ADR-024: `confirmed`) | `ignored_status` |
| status/result belong to the same step as the consumer's other inputs (same pipeline output) | `ignored_stale` |

These codes are the traceability vocabulary for "evidence existed / was used / was ignored and why". A consumer that adopts a feature must record per result `(result_id, used | ignored, reason_code)` in its own output. Designing that record is the consumer's ADR, not this one.

Analytical consumers (display, API, research evaluation) apply the same health, version and staleness checks, but never influence decisions.

A missing, undecodable or schema-mismatched status block is treated as `DISABLED` + `unavailable` (fail closed). `enabled == healthy` must never be assumed.

## Decision 6 — Configuration source, hash and reproducibility

**6a. Feature config is a separate file, `NEXORA_FEATURES_CONFIG`, never part of `RuntimeConfig`/`PipelineConfig` in V1.** A field there would change the stream id and Experience scope (Context 1) for every existing environment, including PROD, merely because a DISABLED, SHADOW or analytical ACTIVE lifecycle changed. None of those can change decisions (6b), so they must not create a new research stream or Experience scope.

- The file is named by `NEXORA_FEATURES_CONFIG` and resolved by the API layer (`apps/api`), like `NEXORA_RESEARCH_CONFIG`. It is passed into `ResearchPipeline` as an explicit constructor argument. Core code never reads the environment.
- Schema: `{"schema_version": 1, "version": "<label>", "features": {"<feature_id>": {"lifecycle": "...", "units": {"<unit_id>": "..."}}}}`.
- Unknown feature/unit ids, unknown keys, invalid literals or an unreadable file make startup fail with `invalid_features_config`, following the precedent of `invalid_research_checkpoints`. A lifecycle above the feature's `max_lifecycle` fails with `feature_lifecycle_not_permitted` (Decision 7).
- Units that are not listed inherit the feature lifecycle. Features that are not listed are DISABLED.
- **Variable unset ⇒ every registered feature DISABLED.** This is the code default, identical in every environment, so there is no env-conditional code.
- Backtests construct `ResearchPipeline` directly. They get the all-DISABLED default unless a `BacktestConfig` explicitly carries a feature config. Any such field is a later ADR-014 change, not V1.

**Future decisional features.** Before a feature may affect decisions, its decision-affecting configuration must enter the reproducible decision/stream identity contract (`RuntimeConfig` or its successor), with the new-stream or migration consequence decided explicitly in that feature's ADR (Decision 7).

**6b. Why this is safe for reproducibility.** DISABLED, SHADOW and analytical ACTIVE cannot change decision semantics. Decision outputs are therefore a function of `RuntimeConfig` + events alone, exactly as today. The feature config affects only each feature's own published block. Every journal row self-describes it through `FeatureStatus` (class, `configured`/`effective` lifecycle per unit, `feature_config_hash`, versions). Historical rows therefore answer "ACTIVE/SHADOW/DISABLED at this event? which units? which versions? which config?" without Research Journal changes.

**6c. Rebuild rule.** A feature-config change takes effect only at process start. On full replay the feature state is rebuilt under the new config. Stored journal rows are never rewritten, and they keep the status in force when they were produced. In-memory replayed feature state may therefore differ from the historical rows' feature block. That is acceptable only because no V1-permitted lifecycle can influence decisions (6b).

**6d. `feature_config_hash`.** It is the deterministic canonical hash of the **fully resolved** feature config:

```
feature_config_hash = canonical_hash({
    "schema_version": <file schema_version, or 1 for the unset default>,
    "version": <label, or "" for the unset default>,
    "ceiling": <Decision 3 ceiling>,
    "features": {feature_id: {"lifecycle": L, "units": {unit_id: U for every registered unit}}
                 for every registered feature_id},
})
```

- "Fully resolved" means after defaults are applied. Every registered feature and every registered unit appear explicitly, including DISABLED ones and inherited unit lifecycles, and keys are ordered canonically.
- The hash is computed once at startup, published in every `FeatureStatus`, and used for checkpoint validation (Decision 11).
- Two files that resolve to the same lifecycles but differ in `version` label produce different hashes. This is conservative: at worst a cosmetic change costs one full replay.

## Decision 7 — ACTIVE: non-decisional vs decisional

Config validation enforces each feature's `max_lifecycle` (`feature_lifecycle_not_permitted`).

**Non-decisional (analytical) ACTIVE** is permitted when the feature's accepted ADR sets `max_lifecycle = ACTIVE`. It:

- serves only the declared analytical consumer contract (production-visible analytical output);
- has no decision authority (Decision 2);
- stays outside `PipelineConfig`, because it cannot change decisions (6b). A change of it is covered by `feature_config_hash` for checkpoints (Decision 11).

**Decisional ACTIVE** is never permitted by this ADR. A decisional feature may become ACTIVE only after an accepted ADR defines:

- the **consumer** and its contract (including the Decision 5 usability record);
- the **decision semantics** it changes, with Quant approval for any scoring/permission change;
- **reproducible identity**: its decision-affecting config enters the hashed decision/stream identity contract (6a), with the new-stream or migration consequence decided explicitly;
- **rollback behaviour**: how demotion to SHADOW/DISABLED restores prior decisions, and what happens to stream/Experience continuity;
- the **safety boundary**: what it can never do (Phase 1: no live order path, AGENTS.md §0 and §9).

Pattern Engine V1 is `analytical` with `max_lifecycle = SHADOW`, so it is **DISABLED / SHADOW only** (ADR-024).

## Decision 8 — DEV / PROD policy

There are no environment-conditional code defaults. Policy is expressed by which file each environment's `.env.<env>` names:

- DEV: may name a features file selecting SHADOW, or analytical ACTIVE where permitted.
- PROD: `NEXORA_FEATURES_CONFIG` stays unset (everything DISABLED) until a feature is validated and a human approves a PROD change. This track changes no PROD configuration.
- DEV and PROD checkpoints are separate by ADR-022 Decision 6. A features file change in one environment only invalidates that environment's checkpoint (Decision 11).
- Q-FL3 (resolved for V1): no separate PROD allow-list. The registry `max_lifecycle` and fail-closed validation are the guard (see Questions).

## Decision 9 — Global Safe Mode compatibility (design only, not implemented)

A future `NEXORA_SAFE_MODE` would set `ceiling` (Decision 3) for all registered features to `DISABLED` (or `SHADOW`). Core paths are unaffected because they are not registered features (Decision 1). No V1 code implements Safe Mode. V1 only guarantees that one global ceiling can be applied centrally in the lifecycle resolver without per-feature changes. Because `ceiling` is part of `feature_config_hash` (6d), a Safe Mode change also invalidates checkpoints and forces a deterministic full replay.

## Decision 10 — Display toggles are not lifecycle

UI layer toggles (for example the chart's Patterns layer) are client-side visibility only. They never change, request or infer a lifecycle. A DISABLED feature with its layer ON draws nothing, and the frontend never reconstructs results from other fields.

## Decision 11 — Checkpoint contract (ADR-022)

Lifecycle-controlled features follow ADR-022's explicit, versioned JSON checkpoint architecture. They introduce no other persistence mechanism.

**11a. What is and is not checkpoint state.**

- The feature config, lifecycles, `max_lifecycle`, `feature_config_hash` and algorithm parameters are **configuration**. They are never persisted as mutable checkpoint state. On restore they are reconstructed from the startup configuration (`NEXORA_FEATURES_CONFIG` + code registry), exactly like every other ADR-022 component.
- Only **bounded, replay-derived feature/engine state** is serialized. That is the exact state needed to resume incremental processing, as an explicit, JSON-compatible, typed section with its own explicit schema. Each feature's own ADR defines its section (for the Pattern Engine: ADR-024 Decision 13).
- A DISABLED feature holds no state, so its section is the canonical empty form defined with DEV-PERF (see 11d).

**11b. `feature_config_hash` validation.** The stream id binds only `RuntimeConfig`, so ADR-022 alone cannot detect a feature-config change. Without an extra check, state built under config A (for example a unit in SHADOW with accumulated results) would be restored into a process running config B (that unit DISABLED). That would break ADR-022's invariant `STATE(full replay) == STATE(checkpoint + delta replay)`. Therefore:

- A checkpoint records the `feature_config_hash` in force when it was written.
- Restore compares it with the current startup `feature_config_hash` **before any state is adopted**.
- On mismatch: reject checkpoint adoption, start from empty state, and perform a deterministic full journal replay (ADR-022 Decision 3). Existing journal rows are never rewritten. The completed replay then writes a fresh checkpoint under the new hash (ADR-022 Decision 4).

**11c. Fail-closed restore.** Any missing, unknown, ill-typed or incompatible feature section, or feature-state schema version, is `state_invalid` under ADR-022 and falls back to full replay. A restored feature is adopted only together with every other component, after every check passes (ADR-022: no partial adoption). Restored feature state must be cross-checked against the components it derives from (for example Structure's transition sequence), in addition to its own invariants.

**11d. Checkpoint coordination points (resolved for Phase 2 in 11e).** `checkpoint_state.py`, `checkpoint.py` and `runtime.py` originated in DEV-PERF (ADR-022). The initial proposal stated requirements only. The authorized Phase 2 integration after PERF-2 merged resolves these points in 11e:

- where `feature_config_hash` lives (checkpoint header field or state field), and its position in the ADR-022 validation order (required: before adoption; preferred: before state decode);
- the reason code for a mismatch (proposed: `feature_config_mismatch`);
- the checkpoint section name(s) for features, and the canonical empty/DISABLED form;
- encoder/restorer integration, `COVERED_FIELDS` coverage and the `_OUTPUT_SCHEMA` extension for new output keys;
- the header `schema_version` and/or `STATE_VERSION` bump. Any state layout change requires one, and older checkpoints are then rejected with `schema_version_mismatch`/`state_version_mismatch`, falling back to full replay;
- passing the feature config into `_restore_pipeline`'s fresh `ResearchPipeline(config, features=...)` construction.

Any Phase 2 code change alters ADR-022's `code_fingerprint`, so the first start after upgrade is always a full replay regardless.

**11e. Phase 2 integration contract (2026-09-25, Lane C authorization).**
After PERF-2 merged at `6cdff36`, the authorized integration specializes 11d as follows;
it does not change lifecycle or trading semantics:

- Keep the ADR-022 JSON envelope and header `schema_version = 2` unchanged.
  Bump payload `STATE_VERSION` from 1 to 2 because its explicit layout changes
  (required by 11d). Pattern's own state version stays 1.
- Store `feature_config_hash` at the payload root as validation metadata, not mutable
  engine state. Keep feature config outside `RuntimeConfig`/`PipelineConfig` and stream identity.
- Preserve header, integrity and journal-anchor validation in `checkpoint.verify`.
  Then validate payload version/keys/hash before typed component decoding. A well-typed
  different hash yields `feature_config_mismatch`; missing/ill-typed hash or malformed
  component state yields `state_invalid`. All failures use existing full-replay fallback.
- Pass the same resolved startup features to both cold pipeline construction and
  `_restore_pipeline`. Decode onto fresh components; restore Structure before checking
  Pattern cross-component invariants. Adopt only after the existing output hash and
  event checks and Experience restore all pass.
- Add `pipeline.pattern_engine` and the matching typed output block; exact state,
  DISABLED representation and validation are specified in ADR-024 §13i.
- PERF-2's `ExperienceService._derived` remains covered but never serialized; the
  existing Experience restore clears it and rebuilds it lazily. No Experience codec,
  semantics, journal schema or persistence model changes.
- A feature-config mismatch rebuilds under the new config and writes a fresh checkpoint;
  historical journal rows and their feature metadata are never rewritten.

## Consequences

- One reusable resolver `resolve(ceiling, feature, unit) -> FeatureLifecycle` plus the registry and the `FeatureStatus` contract in a new pure module (proposed `packages/nexora/features.py`).
- Adds one optional environment variable. Example files document it commented out. No PROD change.
- Pipeline output grows by one bounded status block per feature.
- Analytical features (M30 Bias) can reach ACTIVE through their own ADR without gaining decision authority and without changing the stream id.
- Decisional ACTIVE remains impossible until a dedicated accepted ADR satisfies Decision 7.
- Checkpoint recovery stays correct across feature-config changes, at the cost of one full replay per change.

## Out of scope

Global Safe Mode implementation; a runtime (non-restart) lifecycle switch or API; any decisional ACTIVE promotion; converting existing engines to lifecycle features; Research Journal V2; backtest feature config; changes to ADR-022 implementation files (Decision 11d is a Phase 2 coordination item).

## Questions

- **Q-FL1 — resolved (revision 2).** Use a separate `NEXORA_FEATURES_CONFIG` in V1 (Decision 6a). Lifecycle config never enters `PipelineConfig`, so DISABLED/SHADOW/analytical ACTIVE changes do not create a new research stream or Experience scope. Decision-affecting config of a future decisional feature must enter the reproducible identity contract before use (Decision 7).
- **Q-FL2 — resolved (revision 2).** Track B / ADR-022 has merged, so the old sequencing question is settled. The design follows ADR-022's JSON architecture: config is never persisted, only bounded feature state is, and `feature_config_hash` is validated before adoption with a full-replay fallback (Decision 11). The actual checkpoint integration is a coordinated Phase 2 task (Decision 11d).
- **Q-FL3 — resolved for V1 (Rin, 2026-09-24).** No second, independent PROD allow-list in V1. The guard is the registry `max_lifecycle` fixed by each feature's accepted ADR, plus fail-closed lifecycle validation. A requested lifecycle above `max_lifecycle` fails validation/startup (`feature_lifecycle_not_permitted`). It is never silently downgraded.
- **Q-FL4 — accepted (Rin, 2026-09-24).** Non-decisional (analytical) ACTIVE may expose production-visible analytical output, has no trading authority, is permitted per feature by its accepted ADR's `max_lifecycle`, and stays outside `PipelineConfig`. Decisional ACTIVE requires an accepted ADR defining consumer and decision semantics, reproducible decision identity (hashed config), rollback and safety boundaries, before use (Decision 7).
