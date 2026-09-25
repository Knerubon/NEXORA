# ADR-030 — Replay Validation Framework V1 (VALID-1)

Status: **architecture approved with required doc fixes** (Rin architecture review 2026-09-25; the fixes are applied in revision 2). Phase 2A (pure offline core) is authorized. Quant decisions Q-V2–Q-V5 remain open, and no default for any of them is frozen here.
Date: 2026-09-25
Workstream: VALID-1, `claude/replay-validation-v1`, worktree `D:\NEXORA\NEXORA-REPLAY-VALIDATION`, acting as ARCHITECT for the proposal
Base: `origin/main` `4f69e9c38803796ccfc8afc01480deffd8499c38`
Task: [VALID1](../../tasks/VALID1-replay-validation-v1.md)
Related (on `main`): [ADR-009](./ADR-009-market-structure-lifecycle.md), [ADR-011](./ADR-011-signal-evidence-policy.md), [ADR-014](./ADR-014-backtest-lab-reproducibility.md), [ADR-019](./ADR-019-explicit-feed-time-correction.md), [ADR-020](./ADR-020-pnf-trendline-v1.md), [ADR-021](./ADR-021-entry-readiness-v1.md), [ADR-022](./ADR-022-startup-recovery-checkpoint-v1.md), [EX1](../../tasks/EX1-experience-engine-v1.md), [environment isolation](../environment-isolation.md), [requirements FR-09](../requirements.md)

These ADRs are not on `main` and are referenced by name only:
- ADR-023 (Feature Lifecycle) and ADR-024 (Pattern Engine): accepted for Phase 2 on `claude/pnf-pattern-engine-v1`.
- ADR-025 (MT5 instrument resolution / Binding Mode) on `claude/mt5-multi-broker-v1`.
- ADR-026 (M30 Next Candle Bias, draft) on `claude/m30-next-candle-bias-v1`.
- ADR-027 (Research Journal Payload V2, draft) on `claude/research-journal-payload-v2`.
- ADR-028 (Experience snapshot additive fields, proposed) in the `NEXORA-EXPERIENCE-COMPAT` worktree.

ADR-029 is reserved by the concurrently active PERF-1 Recovery Checkpoint Hardening workstream, so this ADR is numbered 030. Revision 1 (`ba5c388`) was drafted as ADR-029 and renamed on Rin's instruction.

## Objective

Give NEXORA a deterministic, offline way to replay historical market data through the **existing** analysis engines and produce reproducible evidence of how each component behaved afterwards. It answers the question "when this evidence appeared, what did price do next?" without letting anything that happened later reach the decision-time input.

VALID-1 is:

- a **measurement instrument** over existing, unmodified engines;
- **offline and isolated**: it never runs inside the research runtime and never writes production storage;
- **evidence only**: its results inform future ADR and Quant decisions and change nothing on their own.

VALID-1 is **not**:

- a strategy optimizer, parameter search or auto-tuner;
- a trading-rule change, a new signal or a permission source;
- a replacement for the Experience Engine (EX1) or the Backtest Lab (ADR-014);
- a "win probability", composite score or single success number.

## Repository evidence (verified at `4f69e9c`)

### Data and storage

| Asset | Where | Facts relevant to validation |
|---|---|---|
| Canonical event | `NormalizedPriceEvent` (`packages/nexora/market_data/models.py`) | `event_time` (market time, UTC), `received_at` (knowledge time), `source_sequence`, `identity_key`, `kind` = `tick` or `bar`, `price_source`, `precision`, and the flags `is_duplicate`, `is_out_of_order`, `is_gap`, `gap_from_sequence` |
| Research journal | `research_journal` (`packages/nexora/storage.py`, `infra/migrations/007_research_journal.sql`) | Append-only. Stream `research:<RuntimeConfig hash>`; each row is `{event, output, completeness, observation_metadata}`, content-hashed and re-verified on read, ordered by `sequence`. **This is the only real historical record today.** |
| Legacy dev journal | `D:\NEXORA\NEXORA\data\research.sqlite` (51.8 GB; 54,141 events in the main stream per ADR-022) | Rows grow to about 2.4 MB each because every `output` repeats all columns and transitions (ADR-022, ADR-027). VALID-1 must never open this file in place. |
| Immutable datasets | `DatasetManifest` + `normalized.json` (`packages/nexora/backtest/datasets.py`) | Content-hashed partition, `verify_events` fails closed on duplicates, non-finite prices, symbol or source mismatch, `received_at < event_time`, and non-monotonic sequence, `event_time` or `received_at`. `manifest_for` hard-codes `normalizer_version="p2-v1"` and sets `range_end` to the last `received_at`. |
| Market-data repository | `SQLiteMarketDataRepository` / `ReplayReader` (`packages/nexora/market_data/{repository,replay}.py`) | Raw and normalized tables exist, but nothing in `apps/` or `scripts/` writes them. Live data reaches history only through the research journal. |
| Experience streams | `experience:v1:*` in the runtime journal | Observations, T0 snapshots, lifecycle and 5/15/30/60-minute outcomes (EX1) |

### Engines and their causality contracts

All engines run in `ResearchPipeline._process` (`packages/nexora/research/pipeline.py:80`) in this fixed order: Matrix/Adaptive P&F → for each new structure-resolution transition: Structure → Trendline → Regime → Signal → Entry Readiness.

| Component | Output | Causality facts |
|---|---|---|
| P&F + Adaptive Box (ADR-005/006) | `columns`, `transitions` (`PnfTransition.event_time`, `effective_box_size`, `sizing_rule_version`) | Prefix invariance is mandated by ADR-006. The **current column mutates** (its `close_price` and `box_count` extend), and the box size is not constant over history. |
| Structure (ADR-009) | `ConfirmedPivot{occurrence_time, confirmation_time}`, `CandidateLevel{status, updated_at}` | A pivot is confirmed one transition later (`structure/engine.py:57`). **`occurrence_time` precedes knowledge**, and level status changes later (invalidated). |
| Pattern (today) | `SignalDecision.patterns: PatternEvidence{start_time, confirmation_time, algorithm_version}` | Recomputed from the latest pivots by `SignalEngine._patterns` (`signals/engine.py:852`). It has no identity or history; a pattern exists only while its window is the latest one. |
| Pattern Engine (ADR-024, branch) | `pattern_engine` output key, `PatternResult` with deterministic `pattern_id` and `confirmed`/`expired` status | SHADOW/DISABLED only in V1. Its declared consumer contract is "evaluation and display only", and VALID-1 is such an evaluation consumer. |
| Trendline (ADR-020) | `TrendlineSnapshot{active_bullish, active_bearish, history}` | Pure function of already-processed transitions and pivots, with prefix invariance required. Line state evolves (`active → broken → retesting → retest_held/failed → replaced`), and `projected_price_at_latest_column` changes every column. |
| Signal (ADR-011) | `SignalDecision{action BUY/SELL/WAIT, score, entry_zone, invalidation_price, targets}`, `ResearchSignal{occurrence/confirmation/decision_time, status}` | Rejects inputs confirmed after `matrix.generated_at` (`signals/engine.py:72-90`, `future_inputs`). Signal `status` changes later from `active` to `expired`. `BacktestRunner.generate_signals` already preserves the decision-time artifact (`backtest/runner.py:163`). |
| Entry Readiness (ADR-021) | `READY / DEVELOPING / NOT_READY / BLOCKED` + blockers | Stateless, pure function of `(decision, trendline)` for the same event, and prefix-invariant by construction. It can only narrow permission. |
| Experience (EX1) | T0 snapshot, lifecycle, horizon outcomes | Live post-decision observer. `eligible()` requires `event_time > t0` **and** `received_at > t0` (`experience/engine.py:156`). `measure()` uses sampled prices only, with no intrabar guessing, and fixed `HORIZONS = (5, 15, 30, 60)` minutes. Replays **originally recorded outputs**, never recomputed ones. |
| Backtest (ADR-014) | `BacktestRun`, trade metrics | The closest existing tool. It turns signals into simulated trades with delay and cost policies, and it computes trade PnL metrics (win rate, expectancy), which are exactly what VALID-1 must not collapse evidence into. |

### Time and ordering facts

- **Two clocks.** `event_time` is market time, taken from the feed and shifted by the ADR-019 `NEXORA_MT5_TIME_OFFSET_SECONDS` workaround. `received_at` is knowledge time, the wall clock at ingest (`market_data/adapters.py:164`). Replay reads both as recorded, so it is deterministic.
- **Pipeline order guard** (`pipeline.py:80-101`). A duplicate identity with the same hash is a no-op and a conflicting one raises `event_identity_conflict`. Flagged duplicate or out-of-order events raise `noncanonical_event`. `source_sequence` must strictly increase and `event_time` must not decrease. **`received_at` monotonicity is not enforced by the pipeline**, but it is enforced by `verify_events`.
- **Equal `event_time` values are legal.** Canonical order is the dataset or journal order, never a timestamp sort.
- **Bars have no knowledge-time contract.** `MarketBar` has no `interval_start`/`interval_end`, so it is unknowable whether a bar's close was known at its `event_time`. ADR-026 Decision 15 reaches the same conclusion.
- **Mixed code versions within one stream.** The stream id binds the `RuntimeConfig` hash, not the code (`runtime.py:47`). A long-lived journal therefore holds outputs produced by several engine versions, and older rows lack later keys (`trendline`, `entry_readiness`; ADR-028).

### Existing tooling and tests

- `scripts/research_cli.py` imports a verified dataset into a configured journal and optionally runs a backtest. `scripts/recovery_drill.py` proves recovery in a fresh process.
- `tests/conftest.py` isolates every test into a temporary `NEXORA_RUNTIME_ROOT`.
- Prefix-invariance tests already exist in `test_trendline`, `test_structure`, `test_entry_readiness`, `test_adaptive_box`, `test_experience` and `test_readiness_regressions`.
- ADR-022 recovery tests (`test_recovery_checkpoint.py`) prove that checkpoint recovery equals full replay.
- CI (`.github/workflows/validate.yml`) runs pytest, ruff, strict mypy, the recovery drill and `git diff --check`.

## Existing research capabilities vs. gap

| Need | Exists today | Gap |
|---|---|---|
| Deterministic replay through the same engines | `ResearchPipeline` via `ResearchRuntime._rebuild` / `BacktestRunner` | No driver that captures per-component evidence **as of** each event |
| Immutable, hashed input | ADR-014 datasets | No extraction from the research journal (the only real history) |
| Forward outcome measurement | Experience `measure()`: fixed time horizons, one reference, live runtime only | No configurable windows, no box-based or structural outcomes, no offline or batch use, no per-component subjects |
| Signal outcome | Backtest trade PnL | Trade-shaped only (one entry/exit rule), and it collapses to win rate |
| Leak protection | Engine-level cutoffs and prefix invariance, component by component | No **framework-level** guarantee, no automatic truncation or future-injection test applied to whatever is evaluated |
| Provenance | Dataset, config and assumption hashes (ADR-014); `code_fingerprint` (ADR-022) | No single run manifest binding dataset, code, config, outcome definition and generation mode |

## Rin architecture review (2026-09-25) — frozen decisions

- Architecture approved. Q-V1, Q-V6, Q-V7, Q-V8 and Q-V10 are resolved (see *Open decisions*).
- **Mandatory causal acceptance properties.** These are acceptance requirements, not optional test ideas. Failure of either one invalidates the replay result.
  1. **Prefix invariance.** For a cutoff event `c`, `replay(full_dataset)[≤c]` equals `replay(dataset_cut_at_c)` for every decision-time observation through `c` (G3).
  2. **Future mutation invariance.** Changing events strictly after `c` does not change any captured observation at or before `c` (G4).
- Decision-time evidence uses confirmation time where applicable, never future knowledge of an underlying structure (E3). Outcome information never enters decision-time engine input (G2).
- **Phase 2A scope:** the pure offline core in RECOMPUTED mode, as listed under *Implementation phases*. Not in 2A: the legacy journal extractor, Pattern Engine integration, database, API, UI, M30 Bias evaluation, optimization, production-rule changes, AI scoring, win probability, `ExperienceService` changes and production journal changes. RECORDED mode keeps its contract in this ADR, but its integration with historical journal rows waits for its dependencies.
- Unresolved Quant items (Q-V2–Q-V5) stay explicit and configurable, or fail closed. Adaptive Box semantics are not guessed.

## Decision 1 — Placement and boundaries

- A new pure package `packages/nexora/validation/` (Phase 2). It has no FastAPI, database-driver, UI or environment-variable imports (architecture rule: core does not depend on UI, API or DB). Wall-clock time is read only by the CLI shell, and never into hashed content.
- It **reuses** `ResearchPipeline`, the dataset loader and verifier, `canonical_hash`/`canonical_serialize`/`decode`, and ADR-022 `code_fingerprint()`. It contains **no second copy** of P&F, Structure, Pattern, Trendline, Signal or Entry Readiness logic. Engines are called only through their existing public entry points and public snapshots.
- An offline CLI `scripts/validation_cli.py` (Phase 2C) resolves the environment through `nexora_api.environment.Environment`, like `research_cli.py`, and writes only under `<runtime_root>/validation/`.
- **No engine file, config dataclass, journal stream, Experience stream, checkpoint or API route is changed by VALID-1.** Adding a field to `RuntimeConfig`/`PipelineConfig` would change the production stream id (ADR-023 context, fact 1), so validation configuration lives in its own dataclasses.

## Decision 2 — Proposed replay architecture

```
                  ┌──────────────── INPUT (read-only, hash-verified) ────────────────┐
                  │ ADR-014 dataset (manifest + normalized events)                   │
                  │   ▲ extracted offline from a *backup copy* of a research journal │
                  │     (Decision 3)                                                 │
                  └──────────────────────────────┬────────────────────────────────────┘
                                                 │ events e_1..e_n in canonical order
┌──────────────────────── STAGE A — DECISION CAPTURE (sees prefix only) ─────────────────────────┐
│ ReplayDriver: for k = 1..n: pipeline.process(e_k) → Capturers read public snapshots → freeze   │
│  • RECOMPUTED mode: fresh ResearchPipeline(config) (same engines as live)                     │
│  • RECORDED mode: committed output of row k as recorded (no engine run)                       │
│ Output: sealed ObservationRecord stream (hash per record + running prefix hash)                │
└────────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                 │ sealed observations (immutable)
┌──────────────────────── STAGE B — OUTCOME LABELING (may see the future) ───────────────────────┐
│ Labeler: for each observation at anchor k, scan e_{k+1}.. under each OutcomeDefinition        │
│ Output: OutcomeRecord (references observation hash; never modifies it)                        │
└────────────────────────────────────────────────┬───────────────────────────────────────────────┘
┌──────────────────────── STAGE C — METRICS (aggregation only) ──────────────────────────────────┐
│ Descriptive distributions per subject group + mandatory baseline + coverage and censoring     │
└────────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                 ▼
               Immutable ValidationResult bundle (write-once, content-addressed, Decision 12)
```

**Stage separation is the primary no-look-ahead mechanism.** Stage A never has a handle to events after `k`. Stage B cannot write to Stage A artifacts. Stage C reads only A and B.

## Decision 3 — Input data and generation modes

### 3a. Inputs

1. **Canonical input is an ADR-014 dataset.** Every run starts from `load_dataset()` plus `verify_events()`, and requires `expected_dataset_hash` like `BacktestRunner.run`.
2. **Journal extraction** (Phase 2B) is a separate, offline, read-only step:
   - It reads a **copy** of a research journal, never a live runtime file or database. SQLite copies are made with `SQLiteJournal.backup()`.
   - It streams rows with `iter_rows`, not `read()`, because of row size, and emits:
     - an ADR-014 dataset of the recorded `event`s;
     - a sidecar with per-row `(sequence, event_key, content_hash)`, and `observation_metadata` where present (EX1 stores raw time and correction data there). This lets RECORDED mode later verify each row.
   - Violations are reported, never repaired: a `received_at` regression (the pipeline allows it, `verify_events` does not), a `noncanonical_event`, or a symbol or price-source change. The dataset is cut into **contiguous verified segments**, each a separate dataset with `parent_dataset_id`. No event is reordered, dropped silently or invented.
3. **Bars are not eligible** until a bar contract defines `interval_start`, `interval_end` and a knowledge time of at least `interval_end` (the same rule as ADR-026 Decision 15). A dataset with any `kind="bar"` event is rejected (`bar_knowledge_time_undefined`).
4. **PROD data** reaches VALID-1 only as an operator-made backup copy placed in a non-production runtime root. VALID-1 never connects to a PostgreSQL DSN in V1.

### 3b. Generation modes (never mixed)

| Mode | What it evaluates | Engine run | Key question |
|---|---|---|---|
| `RECOMPUTED` | Current (pinned) code over historical events | Fresh `ResearchPipeline(config)` per run, `process()` per event | "What would this code have known at each moment?" |
| `RECORDED` | Outputs the system actually committed at the time | None; row `k`'s recorded `output` is the capture source, re-verified against its `content_hash` | "What did NEXORA actually say?" |

- `generation_mode` is part of run identity and of every record. Metrics are **never** pooled across modes. A combined figure may appear only next to both separate figures, labelled as combined (aligned with ADR-026 Q-M6).
- RECORDED mode segments the stream by the version fields present in each output (`config_version`, `decision.engine_version`, pattern `algorithm_version`, trendline and entry-readiness `config_version`). It treats an absent key as **absent**, not `null` (ADR-028), and never infers a missing component.
- **Parity report (L0).** Both modes run over the same segment, and each component is compared event by event. Divergence is reported as engine drift evidence, never as a failure to "fix" in the data.

## Decision 4 — Event-time model

| Term | Definition |
|---|---|
| canonical index `k` | Position of the event in the verified dataset (1-based). **This is the validation clock.** Timestamps are attributes, never the ordering key. |
| `event_time(k)` | Recorded market time (ADR-019/ADR-025 contract in force, recorded in provenance, never re-derived) |
| `received_at(k)` | Recorded knowledge time |
| decision point | The state immediately after `process(e_k)` has returned and before `e_{k+1}` is fed |
| `t0` | `received_at(k)` of the anchor event. Identical to `ResearchSignal.decision_time` and the Experience `t0`. |
| evidence knowledge time | The time an evidence item became known: pivot `confirmation_time`, pattern `confirmation_time`, trendline break or retest transition `event_time`, level `updated_at`, P&F transition `event_time` |

Rules:

- **E1.** An observation anchored at `k` may contain only information produced by processing `e_1..e_k`. This is enforced structurally (Decision 5, G1) and verified by test (G3, G4).
- **E2.** Every evidence item's knowledge time must be ≤ `event_time(k)`, and ≤ `t0` when compared on the knowledge clock. A violation marks the **run** `INVALID` with `evidence_after_decision_point`, never a silent drop. This mirrors Signal's own `future_inputs` guard.
- **E3.** Evidence is anchored at its knowledge time, **never** its occurrence time. A pivot is evaluated from the event that confirmed it, not from its extreme. A pattern is evaluated from its confirmation, not from `start_time`.
- **E4.** Equal timestamps are resolved only by canonical index. Two events with the same `event_time` remain ordered `k < k+1`.
- **E5.** Outcome samples for an anchor at `k` must satisfy `index > k`, **and** `event_time > t0` **and** `received_at > t0` (the EX1 `eligible()` rule, adopted unchanged). A window end bound applies to both clocks.
- **E6.** Market-closed or feed-gap periods are not interpolated, and there are no synthetic clock events (same as EX1 and ADR-026). Time windows that span a gap are labelled per Decision 8 censoring.

## Decision 5 — No-look-ahead guarantees

A run is **valid** only if G1–G10 hold. Any detected violation makes the whole run `INVALID`, and an invalid run cannot produce metrics.

| Id | Threat | Safeguard |
|---|---|---|
| **G1** | Future price leakage | **Prefix feeding.** The driver owns the dataset. Engines and capturers receive only `e_k` through `process()`. Capture runs synchronously after `process(e_k)` and before `e_{k+1}` exists in any engine-reachable object. Stage A code has no import path to the labeler. |
| **G2** | Future outcome labels entering engine input | **Sealing.** Each `ObservationRecord` is canonical-hashed at capture. A running `prefix_hash(k) = H(prefix_hash(k-1), record_hash)` is stored. Stage B references `observation_hash`, and any mismatch at Stage C fails the run. Engine configuration is frozen and hashed into the run manifest **before** Stage A starts, and there is no code path from outcomes, metrics, Experience outcomes or backtest results into `PipelineConfig` (ADR-026 N4 equivalent). |
| **G3** | Any engine peeking ahead | **Truncation test.** For a set of cut points `c` (deterministic: first, last, every `n/m`, plus every anchor in a sampled subset), a run over the prefix `e_1..e_c` must produce observations byte-identical to the full run's observations with `k ≤ c`. This is applied **generically to every capturer**, so a new component cannot join VALID-1 without passing it. |
| **G4** | Future pattern/structure/trendline knowledge | **Future-injection test.** Replace `e_{c+1}..e_n` with a deterministically perturbed tail (seeded price perturbations, reversed trend, inserted gaps). Observations with `k ≤ c` must be byte-identical. |
| **G5** | Using completed structures before they existed | E2/E3: knowledge-time checks per evidence item at capture. Pattern and pivot anchors use confirmation, never occurrence. |
| **G6** | Repainting: final state read as if it were past state | **As-of capture.** Capturers copy the as-of value at decision point `k` (for example the line state and projected price *now*, the level status *now*, the signal status *now*). They never reconstruct the past from end-of-run `history`, the final `columns` list, the latest `status`, or the current column's extended `close_price`. The P&F current column is captured as `(column_id, direction, box_count_at_k)`. |
| **G7** | Outcome window starting at or before the decision | E5. `t0` itself is never a sample. The reference price is taken from `e_k` or explicitly declared (Decision 8), never from a later event without labelling it (for example `next_event_price` is allowed only as a declared reference kind). |
| **G8** | Timestamp/order ambiguity | Dataset verification plus E4. Reject `received_at < event_time`, bars (Decision 3a) and mixed time contracts inside one dataset (a changed ADR-019 offset or ADR-025 binding mode starts a new segment). Gap flags are carried into outcomes. |
| **G9** | Selection bias via post-hoc choices | **Pre-registration.** Subject selection, outcome definitions, splits and baselines are part of the hashed run manifest, fixed before Stage A. A result is only ever reported under the definitions it was run with. Changing a definition is a new run, and every run, including discarded ones, is kept (Decision 11). |
| **G10** | Hidden nondeterminism | Core reads no wall clock, environment or randomness. Seeds, used only by G4 test tails and never by evaluation, are in the manifest. Same manifest ⇒ byte-identical bundle (Decision 13). |

## Decision 6 — Component evaluation model

Each component is evaluated **independently** through a *subject*: a typed evidence occurrence at a decision point. Subjects are never merged into a score.

| Level | Subject kind | Occurrence rule (anchor `k`) | Direction | Source (RECOMPUTED / RECORDED) |
|---|---|---|---|---|
| **L0** P&F reconstruction | none (consistency) | n/a: determinism, RECOMPUTED-vs-RECORDED parity of columns and transitions, transition counts per `effective_box_size` | n/a | engine / recorded output |
| **L1** P&F transition | `pnf.reversal`, `pnf.extension` | the event whose processing produced the transition | column direction | `transitions[before:]` |
| **L2a** Structure | `structure.pivot_confirmed`, `structure.level_invalidated` | the event that confirmed the pivot or invalidated the level | pivot kind or level side | `structure` snapshot delta |
| **L2b** Pattern (legacy) | `pattern.legacy.<evidence_code>` | first decision point at which `(evidence_code, source_data_reference, algorithm_version)` appears in `decision.patterns` | pattern `direction` | `signals.decision.patterns` |
| **L2c** Pattern Engine | `pattern.<algorithm_id>` | `PatternResult` first reported `confirmed` (by `pattern_id`) | `direction` | `pattern_engine` key. **Only after ADR-024 Phase 2 merges**; SHADOW output is eligible (its consumer contract is evaluation). |
| **L3** Trendline | `trendline.<kind>.<state>` | the event whose processing moved a line to `active`, `broken`, `retesting`, `retest_held` or `retest_failed` | from line kind and state | `trendline` snapshot delta |
| **L4** Signal | `signal.decision.<BUY/SELL/WAIT>`, `signal.issued` | action change, or `ResearchSignal` with `decision_time == t0` (the existing backtest rule) | BUY=long, SELL=short, WAIT=neutral | `signals` |
| **L5** Entry Readiness | `entry_readiness.<state>` | state change for a BUY/SELL decision (and NOT_READY for completeness) | inherited from `signal_action` | `entry_readiness` |
| **L6** Experience | `experience.snapshot` | a new Experience fingerprint (the EX1 freeze rule) | `action` | RECORDED only: `experience:v1:snapshots`. In RECOMPUTED mode the pure `freeze()`/`fingerprint()` functions are used. `ExperienceService` is **never** run, because it writes journals. |
| **L7** Combined | `combined.<signal action>.<entry readiness state>[.<pattern relation>]` | joint key evaluated at L4 anchors | L4 | joins by anchor `k`, never by timestamp |

Rules:

- **Conditional comparisons, not scores.** For example, L5 answers "outcome distribution of BUY decisions that were READY vs BLOCKED vs DEVELOPING". Each group is reported separately, next to its baseline.
- **Neutral subjects** (WAIT, NOT_READY, level events) get non-directional outcomes only: upward/downward excursion and endpoint change.
- **Legacy naming caveat.** The legacy `double_top` is a three-pivot reversal, not a classic P&F Double Top Breakout (ADR-024). Reports use the full `evidence_code` plus `algorithm_version`, and never a bare pattern name.
- **Adding a subject kind** requires a capturer that passes G3/G4, a recorded version, and inclusion in the pre-registered manifest.

## Decision 7 — Observation data model (Stage A)

```python
@dataclass(frozen=True, slots=True)
class EvidenceItem:
    component: str                   # "structure" | "pattern_legacy" | "pattern_engine" | "trendline" | ...
    ref: str                         # stable id: pivot source_transition_id, pattern_id, line_id, signal_id, ...
    knowledge_time: datetime         # E2/E3
    knowledge_index: int             # canonical index that produced it (≤ anchor_index)
    version: str                     # algorithm/config version of the producing engine
    payload_hash: str                # canonical hash of the as-of value (G6)

@dataclass(frozen=True, slots=True)
class ObservationRecord:             # HASHED CONTENT — timestamps come from events only (G10)
    schema_version: Literal[1]
    run_id: str
    generation_mode: Literal["RECOMPUTED", "RECORDED"]
    subject_kind: str                # Decision 6
    subject_id: str                  # canonical_hash(subject_kind, anchor event identity, evidence refs)
    anchor_index: int                # k
    anchor_event_id: str             # identity_key
    t0: datetime                     # received_at(k)
    anchor_event_time: datetime
    direction: Literal["long", "short", "neutral"]
    reference_price: Decimal         # price(e_k); other reference kinds are Decision 8 fields
    box_size_at_t0: Decimal | None   # effective_box_size of the structure resolution at k
    as_of: str                       # canonical JSON of the as-of component view (G6), bounded
    evidence: tuple[EvidenceItem, ...]
    gap_before: bool                 # any is_gap within the component's warmup lookback (reported, not filtered)
    record_hash: str                 # canonical hash of the fields above
```

`as_of` is a **bounded** view: current lines rather than full `history`, latest pivots rather than every pivot, and the current column. This keeps records small and avoids serializing the O(history) output that ADR-022 identified as the recovery bottleneck.

## Decision 8 — Outcome data model (Stage B)

Outcome definitions are **inputs**, versioned and hashed. This ADR fixes their *shape*, not their values. No window length, barrier distance or success threshold is frozen, because none is defined by an existing ADR. The only existing fixed values are EX1's `HORIZONS = (5, 15, 30, 60)` minutes, which remain Experience policy and are not a VALID-1 default.

```python
WindowKind = Literal["time", "events", "pnf_columns", "until_barrier"]
ReferenceKind = Literal["anchor_price", "next_event_price", "signal_entry_zone_mid"]
BoxUnitPolicy = Literal["t0_effective_box_size", "fixed_price_unit"]   # Q-V3

@dataclass(frozen=True, slots=True)
class OutcomeDefinition:
    definition_id: str               # e.g. "od-time-900s-anchor" (name is free text; hash is identity)
    version: str
    window_kind: WindowKind
    window_length: int               # seconds | events | columns; for until_barrier, the maximum length
    max_window_seconds: int | None   # hard stop for until_barrier / pnf_columns
    reference: ReferenceKind
    box_unit: BoxUnitPolicy
    fixed_price_unit: Decimal | None
    favorable_barrier_boxes: int | None
    adverse_barrier_boxes: int | None
    use_signal_invalidation: bool    # adverse barrier = SignalDecision.invalidation_price when present
    gap_policy: Literal["label_censored", "measure_through"]              # Q-V4
```

`OutcomeRecord` (hashed content; one per observation × definition):

| Field group | Fields |
|---|---|
| Identity | `observation_hash`, `definition_hash`, `outcome_id = H(observation_hash, definition_hash)` |
| Status | `COMPLETE` · `CENSORED_END_OF_DATA` (window not finished) · `CENSORED_GAP` (gap inside window, `label_censored`) · `NO_SAMPLES` · `REFERENCE_UNAVAILABLE` (for example no entry zone). Censored records are **kept and counted**, never dropped. |
| Excursions | `mfe_price`, `mae_price`, `mfe_boxes`, `mae_boxes` (directional; neutral subjects get `up_excursion`/`down_excursion`), `endpoint_change`, `endpoint_change_boxes` |
| Timing | `time_to_mfe_s`, `time_to_mae_s`, `events_to_mfe`, `window_end_index`, `window_end_time`, `endpoint_delay_s` (late endpoint, as in EX1) |
| Barrier race | `first_barrier`: `favorable` \| `adverse` \| `none`, `time_to_first_barrier_s`, `index_of_first_barrier` |
| Structural | `pnf_reversal_occurred` + `columns_to_reversal` (a P&F reversal against the subject direction), `breakout_continuation` (for breakout-type subjects: favorable barrier from the breakout level before re-crossing it), `invalidation_hit` + `time_to_invalidation_s` |
| Coverage | `sample_count`, `max_sample_gap_s`, `gap_observed`, `sampled_only = true` |

Measurement rules:

- **Sampled prices only.** Excursions are over canonical event `price` values. There is no intrabar or path guessing, so MFE and MAE are **lower bounds** (same as EX1). Reports state this.
- **Box units.** Under Adaptive Box the box size is not constant. `t0_effective_box_size` freezes the structure-resolution box at `k` into the observation, and every box count for that outcome uses it. The alternative is an explicit price unit. The choice is Q-V3.
- **Structural labels** (reversal, columns-to-reversal, breakout continuation) come from the **same** RECOMPUTED pipeline run's later captures (`k' > k`). The labeler never runs a second P&F implementation. For RECORDED mode they come from later recorded outputs of the same segment.
- **Simultaneity.** A single sampled event has one price and the barriers lie on opposite sides of the reference, so one event cannot touch both. A gap-jump through a barrier counts as a touch at that event's price, and the overshoot is recorded.

## Decision 9 — Metrics (Stage C)

Descriptive only. **No composite score, no "win probability", no ranking of components against each other.**

Per `(generation_mode, dataset segment, subject_kind, definition, split)`:

- `n_subjects`; counts per outcome status; coverage ratio.
- Distribution summaries of `mfe_boxes`, `mae_boxes`, `endpoint_change_boxes` and time-to-event: count, mean, median, quartiles, p10/p90, min, max. Exact decimal arithmetic via `canonical_serialize`, so results are reproducible bit for bit.
- Barrier-race frequencies (favorable / adverse / none), each with its denominator and an uncertainty interval. The method is open (Q-V5).
- **Mandatory baseline.** Every group is reported next to an **unconditional baseline** built with the same definition from anchors that carry no evidence condition. The baseline anchor rule is pre-registered (Q-V2). A group without its baseline is not reportable.
- **Stratification** by regime label at `t0`, `effective_box_size` bucket, and contiguous sub-period (stability across time). Strata are pre-registered.
- **Dependence disclosure.** Subjects whose windows overlap are not independent. Each group reports `overlapping_pairs` and the maximum concurrent open windows. No independence-based p-value is shown without this.
- **Multiplicity disclosure.** Every result lists the total number of groups × definitions evaluated in the run.
- **Small-sample flag.** A group below a minimum `n` (a Quant value, Q-V5) shows counts only, with no rates.

## Decision 10 — Provenance and run identity

`ValidationRunManifest` (hashed; `run_id = "valid1-" + canonical_hash(manifest)`):

| Group | Fields |
|---|---|
| Dataset | `dataset_id`, `dataset_hash` (ADR-014), `parent_dataset_id`, `source`, `symbol`, `price_source`, `units`, `range_start`, `range_end`, `first/last_event_id`, `event_count`, `quality_status`, extraction sidecar hash (journal-derived datasets), source stream id |
| Time contract | ADR-019 `time_offset_seconds` as recorded, ADR-025 binding mode (when present), `timezone="UTC"` |
| Engines | `PipelineConfig` canonical hash + `version`; per-resolution `PnfConfig.version`, box size, reversal boxes, `AdaptiveBoxConfig.rule_version`; `SignalConfig.version`; pattern `algorithm_version`s observed; trendline/entry-readiness `config_version`; ADR-023 `feature_config_hash` once available |
| Code | `code_fingerprint()` (ADR-022: every `.py` in `nexora` + interpreter + format version), `git_commit`, `git_dirty` |
| Evaluation | subject kinds, `OutcomeDefinition` hashes, baseline rule, strata, split definition (train/eval periods), G3 cut points, G4 seeds, `validation_framework_version` |
| Mode | `generation_mode` |

- **Non-hashed envelope:** `created_at` (wall clock), host, duration, operator note. These never enter `run_id` (ADR-026 12A rule).
- `git_dirty = true` or an unknown commit marks the run `EXPLORATORY`. It is kept and shown, but it cannot be cited as evidence in an ADR (Q-V7).
- The Backtest Lab's `split` concept (ADR-014) is reused: definitions and strata may be explored on `train` segments, and cited evidence must come from a pre-registered `eval` segment that was never used for exploration.

## Decision 11 — Storage

- **V1: a file-based, write-once bundle** at `<runtime_root>/validation/<run_id>/`: `manifest.json`, `observations.jsonl`, `outcomes.jsonl`, `metrics.json`, `integrity.json` (per-file SHA-256 and the final `prefix_hash`). This follows the `save_dataset` pattern (`mkdir(exist_ok=False)`, canonical JSON).
- **Atomic finalization.** Stages write to `<run_id>.partial/`, which is renamed to `<run_id>/` only after Stage C and the integrity check pass. A partial directory is never a result.
- **Immutable.** An existing `run_id` directory is never overwritten. Re-running an identical manifest must reproduce identical bytes, which is a verification tool (Decision 13).
- **Nothing is deleted by the framework.** Discarded and `INVALID` runs remain on disk with their reason (supports G9). Retention is an operator action outside VALID-1.
- **Not stored in** `research_journal`, Experience streams, checkpoint files, PostgreSQL or any PROD runtime root. A future database or API index needs its own ADR (Q-V6).
- The CLI refuses an environment whose identity is `production` (the environment-isolation marker) and any journal path equal to a configured runtime journal.

## Decision 12 — Test strategy

Phase 2 tests (pytest, isolated `NEXORA_RUNTIME_ROOT`, synthetic fixtures plus golden vectors; no PROD or legacy data in CI):

| Area | Tests |
|---|---|
| Determinism | Same manifest ⇒ byte-identical bundle (two runs, and a run in a fresh process); `run_id` stable; different config/definition/dataset ⇒ different `run_id`; no wall clock in hashed content (monkeypatched clock changes only the envelope) |
| Event ordering | Non-monotonic `source_sequence`/`event_time`/`received_at` rejected by dataset verification; equal `event_time` ordered by index (E4); a shuffled dataset fails its hash |
| Duplicates | Same identity and content ⇒ pipeline no-op, no duplicate observation; same identity with different content ⇒ `event_identity_conflict` ⇒ run fails; `is_duplicate`/`is_out_of_order` ⇒ rejected |
| Gaps | `is_gap` propagates to `gap_observed`; `label_censored` vs `measure_through` behavior; windows spanning weekend/feed gaps; `NO_SAMPLES` / `CENSORED_END_OF_DATA` counted, not dropped |
| Malformed | Non-finite or ≤ 0 price, `received_at < event_time`, timezone-naive time, unknown schema fields (`decode`), bar events (`bar_knowledge_time_undefined`), symbol/price-source switch mid-dataset, corrupted `normalized.json` (hash mismatch) |
| No look-ahead | **G3 truncation** for every capturer; **G4 future-injection**; E2 violation injection (a fake capturer emitting evidence with a future knowledge time ⇒ run `INVALID`); E3 pivot/pattern anchored at confirmation, not occurrence; E5 outcome sample at `t0` excluded; G2 tampered observation ⇒ Stage C fails; **no import path** from `validation.capture` to `validation.labeling` (static test) |
| P&F consistency | RECOMPUTED over a journal-derived dataset reproduces the recorded `columns`/`transitions` for same-version segments (L0 parity); prefix invariance of transitions; box counts under Adaptive Box use `box_size_at_t0` |
| Outcome math | Golden vectors for MFE/MAE (price and boxes), barrier race including gap-jump overshoot, time-to-event, reversal detection, invalidation; neutral subjects; **cross-check**: for an EX1-equivalent definition (time window, anchor price) VALID-1 excursions equal Experience `measure()` `market_upward/downward_excursion` on the same data |
| Modes | RECORDED verifies row hashes and treats absent keys as absent (ADR-028 fixture without `trendline`); mixed-version segments split; modes never pooled |
| Restart/resume | A crash between stages leaves only `*.partial`; a rerun produces an identical final bundle; an existing `run_id` is never overwritten |
| Isolation | No write outside `<runtime_root>/validation`; production environment refused; source dataset files unchanged (hash before/after); no journal/Experience/checkpoint writes (spy journal) |
| Production neutrality | Importing and running VALID-1 leaves `canonical_hash(RuntimeConfig)`, stream id, pipeline output and Experience fingerprint of a parallel runtime unchanged |

CI: the existing `pytest`, `ruff`, strict `mypy`, `git diff --check`. A bounded synthetic end-to-end run (about 2k events) goes into pytest. Large-dataset runs stay local and out of CI.

## Decision 13 — Performance considerations

- Engines are cheap: less than 1% of ADR-022's replay time. The costs to avoid are **serialization and hashing of O(history) output**, which was 93% of recovery time in Experience.
  - RECOMPUTED mode must not call `pipeline.snapshot()` per event. Capturers read bounded component views.
  - Capture is **on change**, keyed on cheap counters (transition count, structure/trendline/signal `sequence`, action or state change), not on every tick.
- RECORDED mode must stream `iter_rows` (32-row pages) and parse only the output keys a capturer needs. Full outputs of multi-MB rows are never held in memory. ADR-027 (Payload V2), if accepted, changes what RECORDED mode can read (Decision 15).
- Labeling: time and event windows use a forward two-pointer scan (O(n + observations × window)). `until_barrier` stops at the first touch or `max_window_seconds`.
- A run is single-threaded (P&F is sequential). Parallelism is across independent datasets or segments only, and each produces its own bundle.
- BEFORE/AFTER timing (events per second, peak RSS, bundle size) is recorded in the non-hashed envelope for each run. Phase 2 reports it for a 5,000-event prefix of a journal copy, mirroring ADR-022's benchmark, on DEV storage only.

## Decision 14 — Relationship to the Experience Engine

| | Experience (EX1) | VALID-1 |
|---|---|---|
| When | Online, inside the research runtime | Offline batch over immutable datasets |
| Subjects | T0 snapshot on fingerprint change | Per-component subjects (Decision 6) |
| Outcomes | Fixed 5/15/30/60 min, entry-zone or T0 reference, plan lifecycle | Pre-registered configurable definitions |
| Writes | Runtime journal streams | Own bundle only |

- VALID-1 **does not replace, modify or feed** Experience. It never runs `ExperienceService` (a journal writer). It may use the pure `freeze()`/`fingerprint()` functions to identify L6 subjects in RECOMPUTED mode, and it reads recorded Experience records in RECORDED mode.
- **Outcome-formula duplication.** VALID-1 needs generalized excursion math that overlaps `experience/engine.py measure()`. V1 proposes that VALID-1 owns its own general outcome kernel, and that a **parity test** pins agreement with `measure()` for the EX1-equivalent definition. Experience code (EX1-frozen, and under active compatibility work in ADR-028) is not touched. Extracting a shared kernel later is Q-V8.
- Experience outcomes are never an input to VALID-1 engines, and VALID-1 results are never written into Experience.

## Decision 15 — Relationship to other active tracks

| Track | Relationship | Conflict handling |
|---|---|---|
| ADR-024 Pattern Engine (DEV-PNF) | L2c consumes `pattern_engine` as an evaluation consumer (its declared SHADOW contract). `pattern_id` determinism enables SHADOW-vs-later-ACTIVE comparison. | L2c waits for ADR-024 Phase 2 on `main`. Until then only L2b (legacy). No Pattern Engine file touched. |
| ADR-023 Feature Lifecycle | A SHADOW feature's output is eligible for VALID-1. `feature_config_hash` is part of provenance. | VALID-1 never changes lifecycle and grants no ACTIVE promotion. It only provides evidence for one. |
| ADR-026 M30 Bias | ADR-026 owns its own prediction and evaluation contract. VALID-1 adopts the same time vocabulary, the N1–N8 spirit and the LIVE/REPLAY split. | VALID-1 does **not** evaluate M30 bias in V1 and defines no competing M30 outcome. A later ADR may add M30 records as a RECORDED subject. |
| ADR-027 Journal Payload V2 | If recorded `output` is slimmed or compacted, RECORDED mode can no longer read full per-row outputs for new rows. | RECORDED mode reads whatever the payload version provides and declares missing components `absent`. **ARCHITECT coordination required** on which fields V2 keeps for evaluation. |
| ADR-028 Experience compat | Absent vs `null` semantics | Adopted (Decision 3b). |
| ADR-022 recovery (DEV-PERF) | Reuses `code_fingerprint()` read-only | No change to `checkpoint*.py`. |
| ADR-025 MT5 binding | The time contract is a provenance and segmentation input | Contract change ⇒ new segment. |
| Backtest Lab (ADR-014) | Reuses dataset contracts and the `split` concept | `BacktestRunner` unchanged; trade metrics are not VALID-1 metrics. |

**Shared files touched by VALID-1: none.** All Phase 2 work is new files: `packages/nexora/validation/*`, `scripts/validation_cli.py`, `tests/test_validation_*.py` and docs. If Phase 2 finds a needed engine change (for example a missing public accessor), it stops and reports to the owning workstream instead (AGENTS.md §7).

## Decision 16 — Relationship to AI

- AI may **read** a finished `ValidationResult` and write commentary. That commentary is stored **outside** the bundle (a separate, labelled `ai_commentary` artifact referencing `run_id`). It is never hashed into, merged with or used to reorder results.
- AI may not choose or change outcome definitions, subjects, strata or baselines for an existing run. It may not relabel outcomes, mark runs valid or invalid, or suppress groups.
- AI output is visibly distinct from deterministic results in any report or UI (AGENTS.md §2, §9).

## Decision 17 — Relationship to production rules

- **Results are evidence, never configuration.** VALID-1 emits no config file, threshold, weight or lifecycle change, and no code path reads a validation result at runtime.
- Changing a deterministic rule (Signal weights, pattern tolerances, trendline or entry-readiness semantics, box sizing) based on VALID-1 evidence follows the normal route: an ADR citing non-exploratory `run_id`s from pre-registered `eval` segments, Quant review and human approval. VALID-1 never "recommends" a parameter value.
- Phase 1 scope holds: research, observation, backtest and paper only. VALID-1 has no order path, broker access or paper submission.

## Implementation phases (each needs its own review; none starts without acceptance)

| Phase | Deliverable | Gate |
|---|---|---|
| **2A Pure core** | `validation/{models,driver,capture,labeling,metrics,manifest}.py`; RECOMPUTED mode on ADR-014 fixture datasets; L1, L2a, L2b, L3, L4, L5 capturers; G1–G10 harness; outcome kernel; full Decision 12 test set except RECORDED | Authorized by Rin 2026-09-25 (Q-V1 approved). Quant items may stay open: definitions are test fixtures, **not** defaults. |
| **2B Journal extraction + RECORDED** | Offline extractor from a journal **backup** to segmented datasets + sidecar; RECORDED mode; L0 parity; L6 | ADR-027 coordination settled; Security review (reads journal copies) |
| **2C CLI + report** | `scripts/validation_cli.py`, write-once bundle, human-readable report (baseline-adjacent, coverage-first) | Q-V2, Q-V4, Q-V5 answered by Quant for any evidence-grade run |
| **2D Pattern Engine subjects** | L2c | ADR-024 Phase 2 on `main` |
| Later (separate ADR) | API or UI read-only views, database index | Q-V6 |

## Consequences

- NEXORA gains a leak-tested, reproducible way to measure every analysis component against historical data, distinguishing "what current code knows" from "what the system actually said".
- The no-look-ahead guarantee becomes a **framework property tested generically** (G3/G4) rather than a per-engine promise.
- No production behavior, stream id, journal, Experience identity, checkpoint or config changes.
- Cost: new code of roughly the size of `backtest/` plus tests. A second, deliberately separate outcome kernel is pinned to Experience by a parity test.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Sampled ticks understate true excursions | MFE/MAE are lower bounds, and barrier races can miss touches between samples | Stated in every report; `max_sample_gap_s` per outcome; no intrabar inference |
| Journal history covers only periods the runtime was up | Survivorship and coverage bias; weekends and outages | Segmenting, gap censoring, coverage reported first |
| ADR-019 offset or DST changes shift `event_time` semantics mid-history | Time windows are wrong across the change | Segment on any time-contract change; raw time from `observation_metadata` is kept in the sidecar for audit, never re-applied |
| Mixed engine versions in one journal stream | RECORDED metrics pool incompatible behavior | Segment by version fields; RECOMPUTED as the controlled comparison |
| Overlapping subjects, many groups × definitions | False confidence, multiple-comparison artifacts | Dependence and multiplicity disclosure, baseline adjacency, pre-registration, train/eval split |
| Adaptive Box makes "boxes" ambiguous | Inconsistent box outcomes | Box unit frozen per observation (`box_size_at_t0`); policy is Q-V3 |
| Temptation to tune on results | Overfitting | G9 pre-registration, `EXPLORATORY` marking, ADR/Quant route only (Decision 17) |
| Legacy pattern names vs classic P&F names | Misread evidence | Full `evidence_code` + `algorithm_version` in reports |
| Legacy journal size (51.8 GB) | Extraction time, disk | Stream pages, extract event-only datasets, operate on copies on DEV storage |
| ADR-027 slims recorded outputs | RECORDED mode loses components for new rows | Coordination item; RECOMPUTED unaffected |

## Out of scope (V1)

- Strategy optimization, parameter search, walk-forward auto-tuning, machine learning.
- Trade simulation, PnL, costs and sizing (that is the Backtest Lab; ADR-014/015).
- Bar datasets (until a bar knowledge-time contract exists).
- Multi-symbol and cross-symbol validation, and multi-resolution pattern evaluation.
- M30 Bias evaluation (owned by ADR-026).
- API, UI, PostgreSQL persistence and live or streaming validation.
- Any change to engines, Experience, journal schemas, checkpoints, `RuntimeConfig` or `PipelineConfig`.
- Classic P&F pattern formulas (ADR-024 Decision 12, Quant-gated).

## Open decisions

| Id | Owner | Question |
|---|---|---|
| **Q-V1** | Architect (Rin) | **RESOLVED — approved.** Offline package `packages/nexora/validation/`; Capture, Labeling and Metrics stay separated; RECOMPUTED and RECORDED are distinct and never pooled; V1 results are file-based immutable artifacts. ADR number is 030. |
| **Q-V2** | Quant | The first pre-registered outcome definition set (window kinds and lengths, reference kind, barrier distances) and the **baseline anchor rule** (for example every k-th event, or every P&F transition). No value is frozen here. |
| **Q-V3** | Quant | Box unit policy under Adaptive Box: `t0_effective_box_size` vs a fixed price unit, and which resolution's box is used. |
| **Q-V4** | Quant | Gap policy default (`label_censored` vs `measure_through`) and the maximum tolerated `max_sample_gap_s` inside a window. |
| **Q-V5** | Quant | Statistical reporting: interval method for frequencies, minimum `n` before rates are shown, and whether any hypothesis test is shown at all. |
| **Q-V6** | Architect | **RESOLVED — deferred.** No database or API index in V1. |
| **Q-V7** | Architect | **RESOLVED.** Runs from a dirty or uncommitted source tree may be used for development and debugging, but never qualify as official or citable evidence. Official evidence records an identifiable committed revision and reproducibility metadata. |
| **Q-V8** | Architect | **RESOLVED.** Validation outcome computation stays independent in V1. `ExperienceService` is not modified. Parity/contract tests cover the overlapping Experience measurement semantics. |
| **Q-V9** | Architect + DEV-PERF (Journal V2 owner) | Which recorded-output fields does ADR-027 guarantee to keep, so RECORDED mode stays possible for new rows? |
| **Q-V10** | Architect | **RESOLVED.** VALID-1 owns the journal-extractor **input contract** only. The legacy journal extraction implementation is not part of Phase 2A. The 51.8 GB legacy journal is never opened or modified in place. |
