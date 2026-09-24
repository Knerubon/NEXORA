# ADR-024 — P&F Pattern Engine V1

Status: **proposed — draft, NOT accepted** (awaiting Rin architecture review; Quant review required before any catalogue formula in Decision 12 is frozen)
Date: 2026-09-24
Workstream: Track D — `claude/pnf-pattern-engine-v1`, worktree `D:\NEXORA\NEXORA-PATTERN-ENGINE`, role DEV-PNF
Base: `origin/main` `e2ba8ba`
Depends on: [ADR-023](./ADR-023-feature-lifecycle-v1.md) (Feature Lifecycle V1)
Related: [ADR-009](./ADR-009-market-structure-lifecycle.md), [ADR-011](./ADR-011-signal-evidence-policy.md), [ADR-020](./ADR-020-pnf-trendline-v1.md), [ADR-021](./ADR-021-entry-readiness-v1.md), ADR-022 (Track B, unmerged), [chart visual intelligence](../chart-visual-intelligence.md), [P8A](../../tasks/P8A-signal-pattern-scoring.md)

## Context — current state (verified at `e2ba8ba`)

**Only one place detects patterns:** `SignalEngine._patterns(structure)` (`packages/nexora/signals/engine.py:852-962`). It recomputes patterns from scratch on every `evaluate()`, using only the most recent confirmed pivots in `StructureSnapshot.pivots`. Pivots are local extremes of consecutive **transition** `to_price`s (`StructureEngine._confirm_last_pivot`, 3-transition window, ADR-009). They are not X/O column highs and lows. There is no pattern identity, no lifecycle and no history. A pattern exists only while its pivot window is the latest one.

Current algorithm inventory, with `tol = SignalConfig.pattern_price_tolerance` (default `0.8`, absolute price):

| `pattern_type` | Direction | Window | Rule | `algorithm_version` |
|---|---|---|---|---|
| `double_bottom` | bullish | last 3 pivots `L,H,L` | `abs(p1−p3) ≤ tol` | `p8a-pattern-v1` |
| `double_top` | bearish | last 3 pivots `H,L,H` | `abs(p1−p3) ≤ tol` | `p8a-pattern-v1` |
| `head_and_shoulders` | bearish | last 6 `H,L,H,L,H,L` | `c > max(a,e)+tol`, `abs(a−e) ≤ tol`, `f < min(b,d)` | `p8b-pattern-v2` |
| `inverse_head_and_shoulders` | bullish | last 6 `L,H,L,H,L,H` | `c < min(a,e)−tol`, `abs(a−e) ≤ tol`, `f > max(b,d)` | `p8b-pattern-v2` |
| `triangle_breakdown` | bearish | last 6 `H,L,H,L,H,L`, only if not H&S | `a>c>e`, `b<d`, `f<b` | `p8b-pattern-v2` |
| `triangle_breakout` | bullish | last 6 `L,H,L,H,L,H`, only if not inverse H&S | `a<c<e`, `b>d`, `f>b` | `p8b-pattern-v2` |
| `failed_breakout` | bearish | last 4 `H,L,H,L` | `c>a`, `d<b` | `p8b-pattern-v2` |
| `failed_breakdown` | bullish | last 4 `L,H,L,H` | `c<a`, `d>b` | `p8b-pattern-v2` |

The repository `double_top` is a **three-transition-pivot reversal**. It is not the classic P&F Double Top Breakout (an X column exceeding the prior X column top). The two must never share a name. `_structure_evidence` (`engine.py:681-706`) separately repeats the double top/bottom test to emit `structure_double_top/bottom` codes. That is a second copy of the same logic and is recorded here, not changed.

**Consumers of `PatternEvidence` / `decision.patterns`:**

1. **Signal scoring** (`_assess_components`): `relation` is set against the pre-pattern dominant side. A confirmation adds `pattern_confirmation=10` points and a conflict subtracts `pattern_conflict_penalty=15`. Patterns therefore **already change BUY/SELL/WAIT**.
2. **Entry Readiness** (ADR-021): does not read patterns. It is affected only indirectly, through `SignalDecision.action`.
3. **Experience** `fingerprint()` hashes `decision.patterns` (sorted), and `freeze()` stores the whole decision in context. Any change to `PatternEvidence` fields or values changes Experience identity for **new** events. Historical rows keep their stored output.
4. **Paper**: reads `signals.latest` only (unaffected).
5. **Web**: `overlay-model.ts` (chart bracket, keyed `pattern:<evidence_code>:<source_data_reference>`, interim source), and `signal-intelligence.tsx` (text list).
6. **Tests**: `tests/test_signals.py`, `tests/signal_pattern_golden.py`, `tests/test_signal_intelligence.py`, `tests/test_experience.py`, `tests/test_decision_context.py`, `tests/test_entry_readiness.py`, `apps/web/tests/chart-overlays.test.mjs`, `page-chart-regression.test.mjs`.
7. **Backtest**: runs `ResearchPipeline`, so it inherits pattern scoring through Signal.

Changing the pattern source, fields or formulas therefore changes signal decisions, Experience identity, backtest results and chart output together. That is why V1 does not change the source (Decision 11).

## Decision 1 — Ownership and source of truth

- The **P&F Pattern Engine** (`packages/nexora/patterns/`, DEV-PNF) is the only component allowed to own pattern detection **once migration completes** (Decision 11 Phase 3). Until then, `SignalEngine._patterns` remains the production source, and the engine's copy of the legacy algorithms is a time-boxed, parity-tested shadow. It is the only permitted duplication, and it is removed in Phase 3.
- The engine produces **evidence only**. It never emits BUY/SELL/WAIT, score, relation (confirmation/conflict), entry, stop, target, readiness or risk. `relation` depends on the Signal's dominant side, so it stays in Signal.
- It consumes `StructureSnapshot.pivots` and `PnfTransition`s as given, like ADR-020 (it never rediscovers pivots). Classic column algorithms (Decision 12) will also consume `PnfColumn`s.
- The engine is pure Python in `packages/nexora`, with no API/DB/UI imports. Replay, backtest and live observation use the same engine.

## Decision 2 — Feature control (adopts ADR-023)

- Feature id `pattern_engine`. Units are algorithms, with id `<family>.<pattern_type>`. V1 units are `legacy_pivot.double_bottom`, `legacy_pivot.double_top`, `legacy_pivot.head_and_shoulders`, `legacy_pivot.inverse_head_and_shoulders`, `legacy_pivot.triangle_breakdown`, `legacy_pivot.triangle_breakout`, `legacy_pivot.failed_breakout`, `legacy_pivot.failed_breakdown`.
- V1 selectable lifecycles are `SHADOW` and `DISABLED` only (ADR-023 Decision 7). Default is DISABLED.
- Effective lifecycle = `min(ceiling, engine, unit)` (ADR-023 Decision 3).
- **Precedence.** H&S has precedence over triangle and is evaluated first. If `head_and_shoulders` is DISABLED, `triangle_breakdown` is still evaluated with the H&S predicate as an **exclusion test**, so triangle output stays identical to the legacy chain. A disabled unit's predicate may run only as an exclusion guard; it never publishes.

## Decision 3 — Placement in the pipeline

```
ResearchPipeline._process(event)
  for transition in new structure-resolution transitions:
      structure_step = structure.process(transition)
      trendline.process(transition, structure_step)
      pattern_engine.process(transition, structure_step)      # new, per transition
  ...
  signals = signals.evaluate(...)                              # unchanged in V1
  output["pattern_engine"] = pattern_engine.snapshot()         # new top-level key
```

- Resolution: the pipeline's `structure_resolution`, the same one Structure and Trendline use. Multi-resolution patterns are out of scope.
- Output key is **`pattern_engine`**, deliberately not `patterns`, to avoid confusion with `signals.decision.patterns`.
- `ResearchPipeline(config, features=...)` takes the resolved ADR-023 feature config as a constructor argument. The default is all-DISABLED, so backtests and existing callers are unchanged.

## Decision 4 — `PatternResult` contract (frozen shape)

```python
PatternFamily = Literal["legacy_pivot"]              # "classic_pnf" added by the Decision 12 ADR
PatternResultStatus = Literal["confirmed", "expired"]

@dataclass(frozen=True, slots=True)
class PatternAnchor:
    role: str                    # algorithm-defined, e.g. "first", "neckline_left", "head"
    pivot_kind: Literal["high", "low"]
    price: Decimal
    column_id: int               # resolved from the pivot's source transition; never guessed
    source_transition_id: str    # ConfirmedPivot.source_transition_id (= PnfTransition.identity_key)
    occurrence_time: datetime
    confirmation_time: datetime

@dataclass(frozen=True, slots=True)
class PatternResult:
    pattern_id: str              # Decision 5
    algorithm_id: str            # "legacy_pivot.double_top"
    family: PatternFamily
    pattern_type: str            # legacy names unchanged ("double_top", ...)
    direction: Literal["bullish", "bearish"]
    status: PatternResultStatus
    status_reason: str           # "detected" | "window_superseded"
    symbol: str
    resolution: str              # structure_resolution name
    anchors: tuple[PatternAnchor, ...]          # chronological
    start_column: int            # anchors[0].column_id
    end_column: int              # anchors[-1].column_id
    confirmation_column: int     # column of the transition that confirmed the last anchor pivot
    confirmation_transition_id: str
    price_low: Decimal
    price_high: Decimal
    start_time: datetime         # anchors[0].occurrence_time
    confirmation_time: datetime  # time the pattern became knowable (last anchor confirmation)
    status_time: datetime        # event_time of the transition that set `status`
    confirmed_sequence: int      # engine transition sequence at detection
    status_sequence: int         # engine transition sequence at last status change
    evidence_code: str           # legacy code unchanged ("pattern_double_top")
    evidence: tuple[str, ...]    # machine-readable rule facts, e.g. "abs_p1_p3=0.3<=tol=0.8"
    algorithm_version: str       # legacy version strings unchanged
    parameters_hash: str         # canonical_hash of the algorithm's effective parameters
    lifecycle: FeatureLifecycle  # effective lifecycle of the unit when this result was produced
    source_refs: tuple[str, ...] # anchor transition ids + confirmation transition id
    config_version: str          # P&F config_version of the confirming transition (ADR-020 convention)
```

Rationale for fields versus the Track D brief:

- `column_index` is replaced by `start_column`/`end_column`/`confirmation_column` plus per-anchor `column_id` (the existing `column_id`, contiguous per ADR-020 Decision 3).
- Health is not duplicated per result. It lives in `FeatureStatus` (ADR-023 Decision 5), and consumers must read both.
- `relation`, score and trading fields are excluded (Decision 1).
- `lifecycle` is copied onto each result so a result read in isolation (journal row, popup, export) still self-describes as SHADOW.

## Decision 5 — Stable identity

```
pattern_id = canonical_hash((symbol, resolution, algorithm_id, algorithm_version,
                             parameters_hash, tuple(a.source_transition_id for a in anchors)))
```

This follows the ADR-020 `line_id` precedent (`canonical_hash((symbol, kind, pivot ids))`):

- It is deterministic and has no random or wall-clock input. The same events give the same id on every replay, restart and backtest.
- A different algorithm version or different parameters produce a **different id**, so historical outcomes from different algorithms or configs can never be conflated.
- Lifecycle is not part of identity: SHADOW and a later ACTIVE run produce the same id for the same detection, which enables shadow-versus-active evaluation.

## Decision 6 — Result lifecycle

```
(none) --window matches on pivot confirmation--> confirmed
confirmed --a newer pivot confirms (window shifts)--> expired (status_reason="window_superseded")
```

- This matches today's legacy semantics exactly: a legacy pattern is "current" only while its pivot window is the latest one. `confirmed` results in the snapshot's `current` are the parity set (Decision 10).
- No `candidate`/`forming`/`invalidated`/`completed` state exists in V1, because no legacy algorithm defines them. Following the ADR-020 precedent, unreachable states are not declared. The Decision 12 catalogue ADR adds them with formulas.
- Expiry is terminal. Records are never mutated after `expired`; a later identical shape is a new window with new anchors and therefore a new id.
- Detection is evaluated on every transition that confirms a pivot. If one event yields several transitions, intermediate windows are detected and expired within the same event, so the engine records strictly more than Signal ever saw. Parity is defined on the end-of-event `current` set.

## Decision 7 — Snapshot (bounded)

```python
@dataclass(frozen=True, slots=True)
class PatternAlgorithmDescriptor:
    algorithm_id: str
    algorithm_version: str
    parameters: tuple[tuple[str, str], ...]   # canonical string values, e.g. (("price_tolerance", "0.8"),)
    parameters_hash: str

@dataclass(frozen=True, slots=True)
class PatternEngineSnapshot:
    schema_version: Literal[1]
    symbol: str
    sequence: int                              # transitions processed
    status: FeatureStatus                      # ADR-023 Decision 5
    algorithms: tuple[PatternAlgorithmDescriptor, ...]
    current: tuple[PatternResult, ...]         # status == "confirmed", ordered by (confirmed_sequence, algorithm_id)
    changed: tuple[PatternResult, ...]         # results created or changed status during this event
```

- **No unbounded history in the output.** ADR-022 identifies cumulative per-row output as the root cause of recovery cost. `current` holds at most one result per unit, and `changed` is bounded by the event's transitions. Full history is reconstructable from the `changed` entries of journaled rows. An in-engine or API history view is a later phase.
- Engine state is also bounded. The anchor column map keeps only the pivots inside the largest window (6) plus pending transitions, unlike Trendline's unbounded `_column_by_transition`.
- Parameters: legacy units read `SignalConfig.pattern_price_tolerance` directly, which gives one value and no drift from Signal. Thus V1 adds **no** threshold config.

## Decision 8 — DISABLED, SHADOW and health behaviour

- **DISABLED (engine):** no algorithm runs and no pattern state accumulates. `current = changed = ()`. Status is `effective_lifecycle=DISABLED`, `health=not_evaluated`. The block is still published, so absence is explicit and never stale.
- **DISABLED (unit):** that unit publishes nothing. Its `FeatureUnitStatus` shows `not_evaluated`, and other units run normally. Any results of a unit that becomes DISABLED do not exist, because lifecycle changes only at process start (ADR-023 Decision 6c).
- **SHADOW:** full computation, published with `lifecycle="SHADOW"`. In V1 nothing reads `pattern_engine` for decisions. Tests must prove that the entire output except the `pattern_engine` key is **bit-identical** between SHADOW and DISABLED runs.
- **Health per unit:**
  - `warmup`: pivots below the window size.
  - `unavailable`: symbol mismatch, pivot `confirmation_time` later than the processing transition, an unresolvable anchor column, or an exception inside the unit.
  - `ready`: otherwise.
- **Engine health:** `unavailable` if the input itself is invalid (all units unavailable), `degraded` if some units are unavailable and some are not, otherwise `ready`/`warmup`.

## Decision 9 — Fail-closed rules (frozen)

| Condition | Behaviour |
|---|---|
| Anchor column cannot be resolved | Unit `unavailable` (`anchor_column_unresolved`), no result, column never guessed |
| Inputs contain future confirmation (`confirmation_time > transition.event_time`) | Unit `unavailable` (`future_inputs`), mirroring Signal's `future_inputs` guard |
| Exception in one unit | That unit's results for the step are dropped, `unavailable` (`algorithm_exception`), other units continue; the exception is logged, never swallowed silently |
| Exception outside units | Engine `unavailable`, `current=()`; the pipeline must not fail because of a SHADOW feature |
| Feature config missing | All DISABLED (ADR-023) |
| Feature config invalid, or ACTIVE requested | Startup fails `invalid_features_config` / `feature_lifecycle_not_permitted` |
| Consumer receives missing/undecodable `pattern_engine` block, unknown `schema_version` or version not pinned | Treat as DISABLED + unavailable; use nothing (ADR-023 Decision 5 codes) |
| Stale evidence | Impossible by construction: the block is rebuilt every event, and consumers may only pair it with inputs from the same output |

None of these conditions can ever produce positive evidence.

## Decision 10 — Backward compatibility

- `PatternEvidence`, `SignalDecision.patterns`, `SignalEngine._patterns`, the legacy `pattern_type`/`evidence_code`/`algorithm_version` strings, and Experience `fingerprint()` are **unchanged in V1**.
- Legacy names are never reinterpreted. Classic P&F patterns receive new, distinct names (Decision 12).
- **Parity contract.** For every event, each `current` result maps 1:1 onto the corresponding `SignalEngine._patterns` evidence on `(pattern_type, direction, start_time, confirmation_time, price_low, price_high, evidence_code, algorithm_version)`, with `source_data_reference == "|".join(anchor source ids)`. For double top/bottom, that is only the third pivot's id, which matches legacy behaviour; `anchors` still lists all three. Relation is excluded. Parity is a required test and the gate for Phase 3.
- `canonical_hash(RuntimeConfig)` must be unchanged, so the stream id and Experience scope are preserved. This is a required test.
- Existing journal rows are untouched. New rows gain one additive key, and decoders ignore unknown top-level keys, because the output is a plain dict.

## Decision 11 — Migration phases (consumer boundary)

| Phase | Content | Decision impact |
|---|---|---|
| **1** (this ADR) | Inspection, ADR-023/024 drafts | none |
| **2** — Pattern Engine V1 implementation | `features.py`, `patterns/` package, legacy units in SHADOW, pipeline wiring, API config, tests; Signal/chart/Experience unchanged | none (proven by bit-identity test) |
| **3** — Source switch | Signal consumes `pattern_engine` for the `legacy_pivot` family and computes `relation` itself; `SignalEngine._patterns` removed. Needs its own ADR: legacy units become decision-bearing, so they cannot be user-DISABLED without a decision change (lock or move into hashed config per ADR-023 Decision 7), plus Quant sign-off and parity evidence | behaviour-neutral if parity holds |
| **4** — Classic P&F catalogue | Decision 12, SHADOW first | none until promoted |
| **5** — Chart consumption | DEV-CHART renders `pattern_engine.current` (SHADOW visibly marked), replacing the interim source for engine-owned units | display only |
| later | Entry Readiness / Breakout / Experience context / AI consumers, each by its own ADR using ADR-023 Decision 5 | per ADR |

Frontend boundary (all phases): the chart renders only backend `PatternResult`s, resolves columns only from `anchors[].column_id`/`confirmation_column`, and never detects, infers or recreates patterns. A DISABLED engine with the layer ON draws nothing and never falls back to another source for engine-owned units. The interim `signals.decision.patterns` rendering stays unchanged until Phase 5.

Experience boundary: in V1 the `pattern_engine` block is **not** read by `fingerprint()` or `freeze()`. SHADOW therefore cannot alter Experience identity (required test). One side effect: `context.provenance.output_hash` hashes the whole output and will differ between SHADOW and DISABLED rows. It is context, not identity. Adding pattern results to Experience context (observational only, never fingerprint) needs a separate decision, like ADR-020 Decision 13.

## Decision 12 — Classic P&F patterns: reserved names only, formulas NOT frozen

Reserved `pattern_type` names (family `classic_pnf`): `pnf_double_top_breakout`, `pnf_double_bottom_breakdown`, `pnf_triple_top_breakout`, `pnf_triple_bottom_breakdown`, `pnf_bullish_catapult`, `pnf_bearish_catapult`.

The formulas require a separate Quant-approved ADR before any code. Questions it must answer:

1. Column top/bottom definition (`PnfColumn.close_price` for X/O, and treatment of the current incomplete column).
2. "Exceeds the prior X top", given Adaptive Box: strict price comparison (ADR-020 style), or ≥ 1 `effective_box_size` of which transition?
3. "Equal tops" tolerance for triple top: exact price, or within the box of which column?
4. Confirmation moment: the transition that crosses, or column completion.
5. Invalidation and completion rules, and whether the result lifecycle gains `invalidated`/`completed`.
6. Catapult composition: does it reference the triple-top result id?
7. How many prior columns may be scanned (a no-backward-search rule like ADR-020).

A breakout that belongs to a formally defined classic pattern stays inside that pattern's result. A generic breakout/breakdown event, and treating Structure `invalidated` levels as breakouts, remain out of scope.

## Decision 13 — Persistence

- No schema migration and no new table. Rows stay JSON with one additive `pattern_engine` key.
- No Research Journal V2.
- Track B checkpoints pickle the pipeline, including the engine, with no new serialization contract. ADR-023 Decision 6d (feature config hash in checkpoint validation) is the only interaction, and it is an architect decision.

## Consequences

- One pattern-evidence owner, with a safe path to it. V1 changes no decision, Experience identity, chart, backtest result or stream id.
- Journal rows grow by one bounded block per event.
- The legacy duplication (Signal and engine) exists from Phase 2 until Phase 3, guarded by the parity test.

## Out of scope (V1)

HH/HL/LH/LL structure labels (no pivot-level backend contract; separate Structure Classification track); generic breakout/breakdown; classic pattern formulas; multi-resolution patterns; `_structure_evidence` structure-code duplication (Phase 3 ADR); Entry Readiness/Experience/AI consumption; pattern history API/UI; Global Safe Mode implementation; runtime lifecycle switching; any PROD configuration change.

## Open questions

- **Q-PE1** Is Phase 2 shipping only legacy units in SHADOW acceptable as "V1", with classic patterns deferred to a Quant ADR?
- **Q-PE2** Phase 3 lock strategy for decision-bearing legacy units: a locked lifecycle, or moving into `PipelineConfig` with a new stream.
- **Q-PE3** Should `_structure_evidence`'s `structure_double_*` codes migrate with Phase 3, or stay Structure-owned evidence?
- **Q-PE4** Does the chart doc's interim canonical list (6 names) need correcting now? `failed_breakout`/`failed_breakdown` are also emitted and are currently rendered as generic names.
- **Q-PE5** Does Phase 2 wait for Track B to merge (ADR-023 Decision 6d and the `runtime.py` constructor threading), or land first with DEV-PERF rebasing?

## Validation expected (Phase 2)

Parity with `SignalEngine._patterns` on golden cases and on seeded deterministic streams · bit-identical output except `pattern_engine` for SHADOW vs DISABLED (signals, entry_readiness, paper, Experience fingerprint/id) · unchanged `canonical_hash(RuntimeConfig)` · deterministic `pattern_id` across replay/restart · id changes with `algorithm_version`/`parameters_hash` · prefix invariance · incremental `process` equals `replay` · confirmed→expired transitions and bounded `current`/`changed` · engine DISABLED runs no unit (spy) and publishes an explicit empty block · per-unit disable including H&S-vs-triangle exclusion parity · health warmup/ready/degraded/unavailable/not_evaluated · exception isolation · config validation (unknown ids, ACTIVE rejected, invalid file, unset ⇒ DISABLED) · checkpoint invalidation on `feature_config_hash` change (after Track B) · existing web tests including `page-chart-regression` unchanged.
