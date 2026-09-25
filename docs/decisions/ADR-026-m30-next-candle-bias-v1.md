# ADR-026 — M30 Next Candle Bias V1: time, prediction and evaluation contract

Status: **accepted for the Phase 2A Architect scope** (rev 2 with the rev 2.1 Decision 3 clarification), recorded 2026-09-25 by Rin's architecture acceptance review. The acceptance came **after** the Phase 2A implementation `56ba6a7`; see [Acceptance record](#acceptance-record). Architect decisions Q-M1, Q-M2, Q-M5, Q-M6 and Q-M7 are frozen. Quant decisions Q-M3, Q-M4 and Q-M8 remain open. Phase 2B and later gates (Decision 17) are unchanged.
Date: 2026-09-24
Workstream: `claude/m30-next-candle-bias-v1`, worktree `D:\NEXORA\NEXORA-M30-BIAS` (ARCHITECT role for the proposal; no implementation)
Base: `origin/main` `4f69e9c38803796ccfc8afc01480deffd8499c38`
Task: [M30B1](../../tasks/M30B1-next-candle-bias-v1.md)
Related: [ADR-004](./ADR-004-market-data.md), [ADR-011](./ADR-011-signal-evidence-policy.md), [ADR-014](./ADR-014-backtest-lab-reproducibility.md), [ADR-019](./ADR-019-explicit-feed-time-correction.md), [ADR-022](./ADR-022-startup-recovery-checkpoint-v1.md), [EX1 Experience contract](../../tasks/EX1-experience-engine-v1.md), [DC1 Decision Clarity](../../tasks/DC1-decision-clarity-bias-v1.md), [TS1 time-semantics proposal](../../tasks/TS1-time-semantics-proposal.md).

Not on `main`, referenced by name only:
- ADR-023 (Feature Lifecycle) and ADR-024 (Pattern Engine): Track D drafts on `claude/pnf-pattern-engine-v1`.
- ADR-025 (MT5 multi-broker instrument resolution, Binding Mode): a draft in the `claude/mt5-multi-broker-v1` worktree.

## Revision history

- rev 1 (`3972d62`): initial proposal.
- rev 2 (`c557b5e`):
  - freezes Architect decisions Q-M1, Q-M2, Q-M5, Q-M6 and Q-M7 (Decision 0);
  - adds the Candle Identity contract (Decision 5A), the Algorithm Identity contract (Decision 5B) and the Freeze-before-write crash contract (Decision 12A);
  - replaces the rev-1 `scope`/`prediction_id` derivation with those contracts;
  - removes wall-clock and lifecycle values from hashed record content;
  - makes Δ = 0 a candidate only.

  No-look-ahead rules N1–N8 are unchanged.
- rev 2.1 (this revision): Decision 3 now states normatively which target receives the `SKIPPED` record: the latest target whose cutoff the trigger crossed, `B = bucket_start(F.event_time + Δ)`. The rev 2 phrase "the bucket containing `F_k`" was ambiguous for Δ > 0. This matches the Phase 2A implementation, which is unchanged. No other contract changes. The acceptance status is recorded.

## Acceptance record

Recorded transparently. It is not backdated.

1. 2026-09-24: Phase 2A implementation `56ba6a7` was committed while this ADR still read "proposed — draft rev 2, NOT accepted". At that time the Decision 17 gate for Phase 2A ("ADR-026 accepted") was **not** met.
2. 2026-09-25: the independent Phase 2A review returned `CHANGES_REQUESTED`. It found no blocking technical defect. It reported:
   - the unmet governance gate (B-1);
   - the ambiguous Decision 3 `SKIPPED` wording for Δ > 0 (NB-1);
   - missing Δ > 0 tests (NB-2).
3. 2026-09-25: Rin performed the architecture acceptance review. It accepted ADR-026 rev 2 for the Phase 2A Architect scope, subject to the Decision 3 clarification (rev 2.1). It accepted the existing implementation semantics.
4. The Phase 2A gate is satisfied **prospectively**, from this record onward. Phase 2A completion still requires independent re-review and human merge (AGENTS.md §10, §17).

## Decision 0 — Frozen Architect decisions (Rin review)

| # | Decision |
|---|---|
| **Q-M1** | The **Pure Core** may be developed before ADR-023 is accepted, but only: models, candles, freeze, evaluate, and pure unit tests. Runtime wiring, SHADOW activation and `features.py` integration wait until ADR-023 is accepted. |
| **Q-M2** | The lifecycle is `DISABLED → SHADOW → ACTIVE`. For M30 Bias, **ACTIVE means production-visible analytical output only.** ACTIVE grants **no** authority to generate or override BUY/SELL, override Matrix, Signal, Entry Readiness or the Risk Engine, or execute trades. |
| **Q-M5** | Track B shared integration files (`packages/nexora/research/runtime.py`, `packages/nexora/research/checkpoint_state.py`) are not modified until Track B coordination. Implementation splits into **Phase 2A = Pure Core** and **Phase 2B = Runtime/Checkpoint integration** (after Track B coordination). Checkpoint state uses explicit schema and version handling. Compatibility with older checkpoint formats is never guessed. |
| **Q-M6** | Replay-generated historical predictions and outcomes are kept. Provenance distinguishes at minimum `LIVE_GENERATED` and `REPLAY_GENERATED`. Performance and evaluation reporting never silently combines them. |
| **Q-M7** | M30 Bias consumes the canonical upstream market-time contract. **ADR-026 does not own or independently calculate broker timezone offsets.** Legacy mode consumes the existing ADR-019 corrected `event_time`. When ADR-025 Binding Mode is available, M30 Bias consumes the binding/feed time contract, with no separate timezone logic. |

Open Quant decisions: **Q-M3** (outcome label and θ), **Q-M4** (lead time Δ), **Q-M8** (eligibility thresholds). No default for any of them is frozen by this ADR.

## Context

The M30 Next Candle Bias is a deterministic, measurable **bias/evidence record** for the next 30-minute candle. Its historical performance can be evaluated later. It is **not** a prediction claim, a probability, a trade permission or an input to any decision. This ADR freezes the time, identity, prediction, evaluation, crash-recovery and no-look-ahead contracts. It deliberately does **not** define a bias algorithm.

Repository facts that constrain the design (verified at `4f69e9c`):

1. **No M30 or candle concept exists.** `ResearchPipeline` consumes a single canonical stream of `NormalizedPriceEvent`s. Live, those are polled MT5 quotes (`apps/api/nexora_api/quotes.py`, `symbol_info_tick`), recorded only when the quote changes. No `copy_rates`, timeframe or interval field exists anywhere in `packages/` or `apps/`.
2. **The bar contract has no interval semantics.** `MarketBar` carries one `event_time` and complete OHLC. The fixtures set `received_at = event_time + 2s` with the bar's close already known. Whether `event_time` is bar open or bar close is undefined.
3. **Time fields.** `event_time` is market occurrence time in UTC. For MT5 it is corrected by `NEXORA_MT5_TIME_OFFSET_SECONDS` (ADR-019). `received_at` is knowledge time. The pipeline requires `received_at >= event_time`, and rejects `event_time` going backwards or `source_sequence` not increasing (`out_of_order_event`). Events reach the pipeline in journal order.
4. **Pipeline output is causal per row.** The output of row *i* is computed from rows ≤ *i* only (`matrix.process(event, now=event.received_at)`). Structure pivots carry `confirmation_time`, and candidate levels and trendlines are as-of snapshots that can change later.
5. **Experience (EX1) conventions.** T0 = `received_at`. Freezing uses the originally recorded output. Records are derived append-only journal streams, write-once via the journal's unique `(stream, event_key)` and `content_hash` conflict guard. Excursions are sampled lower bounds. Late data never revises a completed label.
6. **"Bias" is already taken** by DC1 `DecisionContext.bias` (`BULLISH`, `BEARISH`, `*_LEAN`, `MIXED`, `UNAVAILABLE`).
7. **Recovery cost.** ADR-022 measured `ExperienceService.observe` at 93% of full-replay time. Checkpoint state lists every component attribute in `COVERED_FIELDS`.
8. **Stream identity = config hash.** Any new field on `RuntimeConfig`/`PipelineConfig` changes the research stream id and the Experience scope.
9. **ADR-025 draft** proposes a `FeedBinding` with `instrument_id`, a per-binding `time_offset_seconds` and a `feed_id` provenance key. It also defines a legacy mode (`feed_id = "legacy:" + hash(company, symbol, digits, offset)`).

## Decision 1 — Scope and non-goals

In scope (after acceptance, in phases per Decision 17):
- a pure core that builds NEXORA M30 buckets from the canonical stream, freezes bias records at a defined cutoff, and evaluates outcomes;
- an append-only persisted record set;
- a read-only API;
- later, a separate UI card.

Never in scope, **including under ACTIVE** (Q-M2):
- a feedback path into, or override of, Signal, Matrix, Entry Readiness, Risk, Paper, Structure, Trendline, the Pattern Engine or chart overlays;
- any probability or win-rate claim;
- learning from outcomes;
- broker orders.

The bias algorithm itself is out of scope (Decision 10).

## Decision 2 — Time vocabulary and the NEXORA M30 bucket

**Time source (Q-M7).** M30 Bias takes `event_time` exactly as delivered by the canonical upstream market-time contract, and never adjusts it:
- **Legacy mode:** the ADR-019 corrected `event_time`, as recorded.
- **Binding mode (ADR-025, when available):** the binding/feed `event_time`, as recorded.

M30 Bias never reads, computes, infers or re-applies a broker offset. The offset and time contract in force are *identity inputs* (Decision 5A), not calculations. If the upstream contract changes, the candle identity changes, and nothing is reinterpreted.

| Term | Definition |
|---|---|
| `event_time` | UTC market occurrence time of a canonical event, as delivered by the upstream time contract |
| `received_at` | UTC knowledge time of a canonical event |
| canonical order | journal row order of the research stream. It is monotonic in `event_time`. |
| bucket `k` | half-open interval `[B_k, E_k)`, where `B_k = floor(event_time_epoch / 1800) × 1800` in UTC and `E_k = B_k + 1800s` |
| NEXORA M30 candle | sampled OHLC of the canonical event `price` (the pipeline `price_source`) over the events in bucket `k`: open = first, close = last, high/low = sampled max/min, plus `sample_count`, first/last event identities and `max_sample_gap_seconds` |

- An event at exactly `B_k` belongs to bucket `k`. An event at `B_k − 1µs` belongs to bucket `k−1`.
- UTC alignment makes buckets independent of host, browser and display timezone. Bangkok display is presentation only (TS1).
- **A NEXORA M30 candle is not an MT5 M30 bar.** It is built from sampled quotes, so its high and low are lower bounds. Whether it matches broker M30 boundaries depends on the upstream time contract, which M30 Bias does not own. The UI and reports say "NEXORA M30 (sampled)".
- **Bucket closure.** Bucket `k` is closed by the first canonical event with `event_time ≥ E_k` (the *closing event*). Out-of-order ticks never enter the canonical stream. That is a recorded limitation, not a revision path.
- No wall clock is read in core code. There is no scheduler and there are no synthetic clock events. A bucket without events has no candle. A closed market leaves the closure pending until the next event.

## Decision 3 — Prediction cutoff and freeze point

For target bucket `k`, with `Δ = freeze_lead_seconds` (integer, `0 ≤ Δ < 1800`; **value is a Quant decision, Q-M4**):

- **Prediction cutoff** `C_k = B_k − Δ`.
- **Freeze trigger event** `F_k` = the first canonical event with `event_time ≥ C_k` **whose runtime row is committed** (Decision 12A).
- **Information set** `I_k` = all committed canonical rows before `F_k` in canonical order. This is identical to "all canonical events with `event_time < C_k`". `F_k` itself is **not** in `I_k`.
- **Snapshot** = the committed pipeline output of the last row of `I_k`, as recorded in the journal, plus the M30 module's own state built from `I_k`. It is never recomputed from current engine code.
- The prediction is **frozen** when `F_k` is processed. From then on it is immutable. Later data, snapshots, late ticks, restarts and replays cannot change it.

Recorded timestamps (all taken from canonical events; none from a wall clock):

| Field | Value |
|---|---|
| `cutoff_time` | `C_k` |
| `snapshot_event_time` / `snapshot_received_at` | `event_time` / `received_at` of the last event in `I_k` |
| `snapshot_event_identity` | its `identity_key` |
| `prediction_time` | `F_k.received_at` |
| `freeze_event_identity` | `F_k.identity_key` |
| `target_start` / `target_end` | `B_k` / `E_k` |

**Eligibility.**
- **Structural rule:** a record for target `k` is `FROZEN` only if `I_k` contains at least one event with `event_time ∈ [B_k − 1800s, C_k)`.
- **Targets crossed by one trigger (normative, rev 2.1).** Let `L` be the previous committed event and `F` the committed event being processed. `F` crosses every target `k` with `L.event_time < C_k ≤ F.event_time`, and `F` is `F_k` for each of them. Only the earliest crossed target can satisfy the structural rule: every later one needs an event at or after the earliest target's `B_k`, which is after `L`. Therefore:
  - If the earliest crossed target is eligible, one `FROZEN` record is written for it.
  - If the earliest crossed target is ineligible, or more than one target is crossed, exactly one `SKIPPED` record with `discontinuous_feed` is written for the **latest crossed target**. That is the latest target whose cutoff `C_k ≤ F.event_time`, equivalently the target with `B_k = bucket_start(F.event_time + Δ)`.
  - Nothing is written for crossed targets in between.
- "The bucket containing `F`" is **not** the rule. With Δ = 0 it coincides with the latest crossed target. With Δ > 0 and `F.event_time ∈ [B − Δ, B)`, the latest crossed target is the bucket after the one containing `F`. The bucket containing `F` has also been crossed, by `F` or by an earlier trigger. It therefore either already holds its own record under its `candle_id`, or it is an in-between target that receives none.
- **Quant decision (Q-M8):** any further threshold, such as a minimum sample count or a maximum gap. No default is frozen.
- **Not a skip:** warmup, disabled evidence and invalid inputs produce `bias = UNAVAILABLE` with reason codes.

**Late freeze.** If `prediction_time > B_k`, the record carries `freeze_after_target_open = true` and `freeze_lag_seconds = prediction_time − B_k`, both derived from event timestamps. The record remains leak-free.

**Choice of Δ (Quant, Q-M4).**
- `Δ = 0` uses all information up to the boundary but usually freezes just after the target opens.
- `Δ > 0` freezes before the target opens whenever the feed is live, at the cost of ignoring the last `Δ` seconds.

`Δ = 0` remains a **candidate recommendation only**. It is not a frozen rule. The contract supports any valid Δ, and Δ is part of the algorithm/config identity (Decision 5B).

## Decision 4 — Prediction record contract

Names avoid DC1 vocabulary. The bias values say only which direction the evidence leans for the target bucket.

```python
M30BiasValue = Literal["UP", "DOWN", "NO_EDGE", "UNAVAILABLE"]
M30BiasRecordStatus = Literal["FROZEN", "SKIPPED"]

@dataclass(frozen=True, slots=True)
class M30BiasEvidence:
    component: str            # "matrix" | "pnf" | "structure" | "trendline" | "regime" | "signal" | "m30_candles" | ...
    code: str                 # machine code, algorithm-defined, versioned with the algorithm
    polarity: int             # +1 up, -1 down, 0 neutral/informational
    value: str | None         # canonical string of the observed fact, no floats
    as_of_event_time: datetime
    as_of_received_at: datetime
    source_version: str       # engine/config version of the component that produced the fact

@dataclass(frozen=True, slots=True)
class M30BiasPrediction:          # HASHED CONTENT — deterministic fields only
    schema_version: Literal[1]
    policy_version: str           # "m30-bias-v1"
    prediction_id: str            # Decision 5B
    candle_id: str                # Decision 5A
    algorithm_key: str            # Decision 5B
    source: str; symbol: str; price_source: str; units: str
    target_start: datetime; target_end: datetime
    freeze_lead_seconds: int
    cutoff_time: datetime
    prediction_time: datetime
    snapshot_event_time: datetime; snapshot_received_at: datetime
    snapshot_event_identity: str; freeze_event_identity: str
    freeze_after_target_open: bool; freeze_lag_seconds: Decimal
    status: M30BiasRecordStatus
    bias: M30BiasValue            # SKIPPED => UNAVAILABLE
    reason_codes: tuple[str, ...]
    reference_price: Decimal | None    # P_ref = price of the last event in I_k
    threshold: Decimal | None          # θ frozen at cutoff (Decision 6); null until Q-M3
    evidence: tuple[M30BiasEvidence, ...]
    input_provenance: dict        # runtime stream id, runtime config hash, pipeline/engine
                                  # versions, snapshot output hash — all from committed rows
```

**Excluded from hashed content.** These live only in the generation-provenance record (Decision 11):
- wall-clock times;
- generation mode (`LIVE_GENERATED` / `REPLAY_GENERATED`);
- lifecycle in force (DISABLED/SHADOW/ACTIVE) and the ADR-023 `FeatureStatus` block;
- health;
- host, process or session identifiers.

The reason: a lifecycle promotion, or a replay, must never change prediction content.

No `probability`, `confidence` or `win_rate` field exists. `NO_EDGE` means the algorithm ran and found no lean, which is an abstention. `UNAVAILABLE` means it could not evaluate. Reports show coverage next to any directional result.

## Decision 5A — Candle Identity contract

`candle_id` identifies one NEXORA M30 bucket of one feed/instrument under one contract version. It is **never** the target start timestamp alone.

**Identity fields** (all strings; nothing is omitted, and an absent value is an explicit literal):

| Key | Value |
|---|---|
| `id_contract` | `"m30-candle-id-v1"` (this contract's version) |
| `bucket_contract` | `"m30-bucket-v1"`: Decision 2 bucketing (UTC epoch alignment, 1800 s, half-open `[start, end)`) |
| `timeframe` | `"M30"` |
| `duration_seconds` | `"1800"` |
| `time_contract` | The upstream contract M30 consumes (Q-M7). One of three values: (a) `"legacy-adr019"` for MT5 quote observations; (b) the ADR-025 binding time-contract version string (for example `"adr025-binding-v1"`), defined by ADR-025 and not here; (c) `"recorded-utc-v1"` for non-MT5 recorded datasets and fixtures whose `event_time` is taken as recorded UTC with no correction claimed. |
| `feed_key` | Legacy or recorded mode: `"legacy:" + event.source + ":" + event.symbol`, verbatim. For MT5 quotes, the recorded `event.source` is already `"MT5-quote-observation:time-offset=<N>"` (`apps/api/nexora_api/research.py`), so the ADR-019 offset in force is part of identity without M30 computing or parsing it. Binding mode: the ADR-025 `feed_id`, verbatim. |
| `instrument_key` | legacy mode: the event `symbol`. Binding mode: the ADR-025 `instrument_id`. |
| `price_source` | the pipeline price source (for example `"bid"`) |
| `units` | runtime units |
| `bucket_start` | `B_k` in UTC, formatted exactly `YYYY-MM-DDTHH:MM:SS.ffffffZ` (always 6 fractional digits, literal `Z`) |

**Canonical serialization (`m30-candle-id-v1`):**
1. Build a JSON object with exactly the ten keys above. All values are JSON strings.
2. Serialize with keys sorted in ascending code-point order, separators `,` and `:` with no whitespace, `ensure_ascii=true`, and no trailing newline. This is equivalent to Python `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
3. Encode the result as UTF-8.
4. Compute `candle_id = "m30c1-" + lowercase_hex(SHA-256(bytes))`.

**Rules.**
- `candle_id` is independent of `canonical_serialize`/`canonical_hash`, so a change in the generic serializer cannot silently change identity. Phase 2A must include a golden test vector: a fixed field set, its exact serialized bytes and the resulting hex digest.
- A candle's OHLC content is not part of its identity. Identity says *which* bucket, not *what* was observed.
- Any change to bucketing, the time contract, the feed, the instrument, the price source or the units yields a different `candle_id`. Records under the old id are never reinterpreted.
- Changing the field set or serialization requires a new `id_contract` version and prefix (`m30c2-`, …). Existing ids are never rewritten.
- The `time_contract` is selected by explicit configuration of the M30 feature (Phase 2B) and validated against recorded provenance. `legacy-adr019` requires an `MT5-quote-observation:time-offset=` source, and binding mode requires a recorded ADR-025 `feed_id`. A mismatch or missing provenance means the candle is not identifiable. An unidentifiable candle cannot be keyed, so **no record is written**: the feature reports health `unavailable` with `candle_identity_unavailable`, and no default is inferred.

## Decision 5B — Algorithm Identity contract and journal uniqueness

**`algorithm_key`** identifies everything that can change prediction content for the same candle:

| Key | Value |
|---|---|
| `id_contract` | `"m30-algorithm-id-v1"` |
| `policy_version` | `"m30-bias-v1"` (this ADR's time and measurement policy) |
| `algorithm_id` | stable algorithm name |
| `algorithm_version` | explicit version, bumped on any rule or code change that can alter output |
| `algorithm_params` | the fully resolved parameter object, with defaults expanded, as a nested JSON object with sorted keys, string scalars only, no floats |
| `freeze_lead_seconds` | Δ as a string |
| `threshold_policy` | the θ policy id plus its resolved parameters (Q-M3), or `"none"` until decided |
| `eligibility_policy` | the eligibility rule id plus its resolved parameters (Q-M8), or `"structural-v1"` |
| `evidence_source` | the research runtime stream id (`"research:" + canonical_hash(RuntimeConfig)`). Predictions read that runtime's committed outputs. |

Serialization uses the same rules as 5A (sorted keys, compact, ASCII, UTF-8, SHA-256). The result is `algorithm_key = "m30a1-" + hex`, with a golden test vector in Phase 2A.

- `prediction_id = "m30p1-" + hex(SHA-256(canonical JSON {"id_contract":"m30-prediction-id-v1","algorithm_key":…,"candle_id":…}))`.
- Outcome identity uses the same pair. `outcome_id` is `"m30o1-"` over the same two keys, because an outcome's MFE/MAE orientation depends on the prediction.

**Lifecycle is excluded from `algorithm_key`.** SHADOW → ACTIVE promotion therefore keeps identities and content, and only the generation-provenance record differs.

**Journal uniqueness.** Records reuse `research_journal` with its unique `(stream, event_key)` and `content_hash` conflict guard:

| Stream | `event_key` | Semantics |
|---|---|---|
| `m30bias:v1:{algorithm_key}:predictions` | `candle_id` | deterministic `M30BiasPrediction`; the same content is an idempotent no-op; different content is a **visible conflict**, never an overwrite |
| `m30bias:v1:{algorithm_key}:outcomes` | `candle_id` | deterministic `M30BiasOutcome`; same guard |
| `m30bias:v1:{algorithm_key}:provenance` | `candle_id + ":prediction"` or `candle_id + ":outcome"` | insert-if-absent generation provenance (Decision 11). Never recomputed or compared. |

- Two algorithm or config versions for the same candle write to **different streams**, so they never collide, and both remain queryable.
- The same `algorithm_key` producing different content for a candle means an unversioned rule or code change, or nondeterminism. The journal rejects it (`m30_prediction_conflict` / `m30_outcome_conflict`). The M30 feature then reports health `unavailable` and stops writing for that `algorithm_key`. The research runtime and every other feature continue unaffected, which is the fail-closed feature isolation from ADR-023.
- The code fingerprint is deliberately **not** in `algorithm_key`, because it would split identity on every unrelated commit. Unversioned changes are caught by this conflict guard during replay over overlapping history, and prevented by required version bumps plus golden-vector tests.

## Decision 6 — Candidate outcome definitions (QUANT DECISION REQUIRED, Q-M3)

All candidates are computed from `W_k` (Decision 5) and the frozen `P_ref` and `θ`. They must not be selected by headline accuracy.

| # | Definition | Strengths | Weaknesses |
|---|---|---|---|
| O1 | Candle body direction `sign(close_k − open_k)` | Matches the usual chart reading | `open_k` is post-cutoff information. Doji and noise count as full wins or losses. Ignores the gap from `P_ref`. |
| O2 | Raw close return `r_k = close_k − P_ref` (signed; also `/θ`) | Consistent with the information set; continuous | Not a class label by itself |
| O3 | Thresholded close return (ternary): `UP` if `r_k ≥ +θ`, `DOWN` if `r_k ≤ −θ`, else `FLAT` | Separates noise from moves. `NO_EDGE` can be judged against `FLAT`. θ is frozen causally. | Depends on the choice of θ |
| O4 | First-touch excursion: `P_ref ± θ` first crossed; `NONE` / `AMBIGUOUS` when both are crossed between samples | Path-aware | Path-dependent on sampled data. The ambiguity rate must be reported. |
| O5 | Directional excursion pair (MFE, MAE; Decision 7) | Opportunity versus adverse move | Not a single label |

**Candidate recommendation, not frozen.**
- O3 as the primary label, with a volatility-scaled θ frozen at the cutoff from `I_k`. Candidate θ policies:
  - `k ×` the structure-resolution adaptive box size;
  - `k ×` the mean sampled range of the last `n` closed NEXORA M30 candles.
- Always record O2, O4 and O5. Record O1 as a reference only.
- The label, the θ policy and `k`/`n` are Quant decisions. Until Q-M3 is decided, `threshold` is null and threshold-dependent fields (O3, O4) are null with the reason `threshold_policy_undecided`.

**Reporting rules (frozen).**
- Always report:
  - coverage;
  - per-class counts;
  - a confusion table against the primary label;
  - O2 conditioned on bias;
  - MFE/MAE distributions;
  - the O4 ambiguity rate.
- Compare against causal baselines: persistence, the prior majority class, and always-`NO_EDGE`.
- Use chronological splits only.
- **Report `LIVE_GENERATED` and `REPLAY_GENERATED` separately (Q-M6).** A combined figure may appear only next to both separate figures and labelled as combined, never as the only figure.
- Report `freeze_after_target_open` records separately.
- Group by `algorithm_key`. Never pool across keys.
- No probability or win-rate claim appears in the UI or API.

## Decision 7 — Evaluation record, MFE and MAE

```python
M30BiasOutcomeStatus = Literal["EVALUATED", "NO_DATA"]

@dataclass(frozen=True, slots=True)
class M30BiasOutcome:             # HASHED CONTENT — deterministic fields only
    schema_version: Literal[1]
    policy_version: str
    outcome_id: str; prediction_id: str; candle_id: str; algorithm_key: str
    status: M30BiasOutcomeStatus
    evaluation_time: datetime          # closing event received_at
    closing_event_identity: str
    open: Decimal | None; high: Decimal | None; low: Decimal | None; close: Decimal | None
    sample_count: int
    first_sample_time: datetime | None; last_sample_time: datetime | None
    max_sample_gap_seconds: Decimal | None
    gap_or_incomplete_samples: int
    close_return: Decimal | None       # O2
    body: Decimal | None               # O1
    pre_target_drift: Decimal | None   # open - P_ref
    label_threshold: Literal["UP","DOWN","FLAT"] | None          # O3; null until Q-M3
    first_touch: Literal["UP","DOWN","NONE","AMBIGUOUS"] | None  # O4; null until Q-M3
    mfe: Decimal | None; mae: Decimal | None
    mfe_time: datetime | None; mae_time: datetime | None
    up_excursion: Decimal | None; down_excursion: Decimal | None
    reason_codes: tuple[str, ...]
```

**Windows.** The evaluation window, MFE window and MAE window are all `W_k` = the canonical events with `event_time ∈ [B_k, E_k)`.

With reference `P_ref` and samples `p ∈ W_k`:

- `up_excursion = max(0, max(p − P_ref))`
- `down_excursion = max(0, max(P_ref − p))`

These are always recorded.

- **MFE**: `up_excursion` for bias `UP`, `down_excursion` for bias `DOWN`.
- **MAE**: `down_excursion` for bias `UP`, `up_excursion` for bias `DOWN`.
- `NO_EDGE` / `UNAVAILABLE` / `SKIPPED`: `mfe = mae = null`.
- `mfe_time` / `mae_time` = `event_time` of the first sample that attains the extreme.

**Semantics.**
- Values are sampled lower bounds.
- There is no intrabar or path inference, no interpolation, no fill model, and no costs or P&L.
- Empty window: `NO_DATA`, with the reason `no_in_window_samples`.
- The outcome never reads evidence. It reads the bias only to orient MFE and MAE.
- The closing event not yet seen: the outcome stays pending and nothing is persisted.

## Decision 8 — No-look-ahead boundary (frozen rules, unchanged from rev 1)

| # | Rule |
|---|---|
| N1 | Every evidence item has `as_of_event_time < cutoff_time` and `as_of_received_at ≤ snapshot_received_at`. Violation ⇒ `UNAVAILABLE` with `evidence_after_cutoff`, never a silent drop. |
| N2 | Bias computation reads only the snapshot (the committed output of the last row of `I_k`) and M30 state built from `I_k`. It never reads rows at or after `F_k`, recomputed engine output, or later snapshots of repaintable objects. |
| N3 | θ and `P_ref` are computed from `I_k` and frozen in the record. |
| N4 | Bias computation never reads M30 outcomes, Experience outcomes or labels, backtest results or evaluation statistics. There is no learning or feedback in V1. |
| N5 | Records are write-once. A recompute with different content is a visible failure, never an overwrite. A changed algorithm, policy, θ policy or Δ is a new `algorithm_key` (Decision 5B). |
| N6 | Core code reads no wall clock and no environment. Every timestamp in hashed content comes from canonical events. |
| N7 | Outcomes use only `W_k` and the closing event. They never update the prediction record. |
| N8 | Pattern Engine results, if ever used, must satisfy N1 through their own confirmation/status time and ADR-023's usability record. |

## Decision 9 — Evidence available at prediction time

Unchanged from rev 1. Which items an algorithm uses is decided by Quant (Decision 10).

| Source (snapshot field) | Usable as evidence | Notes |
|---|---|---|
| `matrix.resolutions[*]` direction, status, latest transition | yes | Warmup and stale resolutions map to `UNAVAILABLE` evidence |
| P&F `columns`/`transitions` (structure resolution) | yes | The current column is developing. Use its as-of state only. |
| `structure` confirmed pivots | yes, only if `confirmation_time < cutoff_time` | Candidate levels are as-of only |
| `trendline` lines/anchors | yes, as-of snapshot | Never a later snapshot |
| `regime.state` | yes | |
| `signals.decision` action and evidence polarity | yes, **evidence only** | M30 never feeds back into Signal |
| `entry_readiness` | not recommended | It is a permission filter |
| DC1 `derive_decision_context` | may be called on the snapshot | Do not duplicate its logic |
| Prior NEXORA M30 candles in `I_k` | yes | Simple statistics only. No candlestick-pattern detection. |
| Spread and feed age of the last `I_k` event | yes (quality) | |
| Session, news, calendar, `data_version` | not available | |
| Experience/M30 outcomes, backtest statistics | **forbidden** (N4) | |

## Decision 10 — The algorithm is a separate decision

Any bias algorithm needs its own Quant-approved ADR that references this one. It must be rule-based and explainable from its `evidence` tuple, and must not be trained or fitted. Phase 2A tests use a test-only fixture algorithm that exists solely to exercise the freeze and identity contracts. It is not a bias rule, and it is never registered or exposed.

## Decision 11 — Persistence, generation provenance and runtime placement

- Pipeline output, `RuntimeConfig` and `PipelineConfig` are unchanged, so the stream id and Experience scope are unchanged. Journal rows gain nothing.
- **Placement (Phase 2B).** A sidecar service beside `ExperienceService` observes each committed runtime row. Streams are per Decision 5B, with no SQL migration.
- **Generation provenance (Q-M6).** One insert-if-absent record per prediction and per outcome:

```python
M30GenerationMode = Literal["LIVE_GENERATED", "REPLAY_GENERATED"]

@dataclass(frozen=True, slots=True)
class M30GenerationProvenance:    # NOT part of prediction/outcome hashed content
    schema_version: Literal[1]
    record_kind: Literal["prediction", "outcome"]
    record_id: str                # prediction_id or outcome_id
    generation_mode: M30GenerationMode
    generation_reason: str        # "live_ingest" | "startup_replay" | "crash_recovery" | "backtest" | ...
    lifecycle_at_generation: str  # ADR-023 effective lifecycle when written (SHADOW/ACTIVE)
    feature_config_hash: str | None
    code_fingerprint: str | None
    materialized_at: datetime     # wall clock; audit only
```

- `LIVE_GENERATED` means the record was first durably written while processing a live-ingested row, in the same ingest step. Every other first write is `REPLAY_GENERATED`, with a reason: startup replay of history, crash recovery (Decision 12A), or backtest.
- The first writer wins. Provenance is never recomputed, compared or upgraded.
- **Write order:**
  1. runtime row commit;
  2. Paper;
  3. Experience;
  4. M30 prediction (on `F_k`), then its provenance;
  5. M30 outcome (on the closing event), then its provenance.

  Each append is transactional, and replay fills only missing keys.

## Decision 12 — Restart and recovery

- **Bounded state:**
  - the in-progress bucket;
  - the last `n` closed candles, if needed;
  - the evidence extracted from the last committed row;
  - at most one open target awaiting its outcome;
  - open keys only.

  Per-row cost is O(1) amortized.
- **Checkpoint (Phase 2B, Q-M5).** M30 state enters ADR-022 checkpoint state as an explicit section with its own schema and version (`m30_state_version`), explicit field coverage, and restore validation of `algorithm_key` and `feature_config_hash`. Compatibility is never guessed:
  - a missing section when M30 is enabled ⇒ checkpoint unusable ⇒ full replay;
  - an unknown or older section version ⇒ full replay;
  - a present section when M30 is disabled ⇒ ignored, and no state is adopted.

  The exact integration into `checkpoint_state.py` and `runtime.py` is a Track B coordination item and is not designed here.
- **Checkpoint consistency point.** A checkpoint may be taken only when every prediction, outcome and provenance record triggered by rows up to the checkpoint row is durably written. A checkpoint never contains a frozen-but-unwritten prediction.
- **Equivalence.** The following must yield identical prediction and outcome content (provenance may differ, per Decision 11):
  - cold full replay;
  - checkpoint restore plus delta;
  - restart at every row boundary;
  - a missing or corrupt checkpoint;
  - appending after a restart.
- A lifecycle or config change takes effect only at process start. A changed `algorithm_key` writes to new streams, and old streams are never rewritten.

## Decision 12A — Freeze-before-write crash contract

**Principle.** A prediction is a pure function of committed journal rows plus immutable identity config:

`prediction = f(algorithm_key inputs, candle_id inputs, committed rows up to and including F_k)`

Here `F_k` contributes only its identity and `received_at`. Nothing else enters hashed content. So any process that sees the same committed rows produces the same bytes.

**Rules.**
1. **Freeze is defined on committed rows.** `F_k` counts only once its runtime row is committed. The live path computes the freeze from the committed, serialized row payloads (as `row["output"]` / the decoded event), not from in-memory objects that were never serialized. Live and replay therefore read byte-identical inputs.
2. **No publication before durability.** The API and UI expose a prediction only after its journal write succeeds. A frozen-but-unwritten prediction is invisible and has no external effect.
3. **Recovery by recomputation.** On restart, rebuild replays committed rows, from a consistent checkpoint (Decision 12) or from the start. When it reaches `F_k`, it recomputes the prediction from the same committed rows, finds the key absent, and writes it. By the principle above, the result is identical to the record that would have been written before the crash. Its provenance is `REPLAY_GENERATED` / `crash_recovery`, because it was never live-visible. That is honest under Q-M6.
4. **No hashed wall-clock values.** Hashed content contains no wall-clock time, generation mode, lifecycle, health, host or process id, and no random value. All timestamps come from events (N6).

**Crash windows.**

| Window | Durable state at crash | Recovery result |
|---|---|---|
| W1: before `F_k`'s runtime row commits | `F_k` absent | Freeze never happened. The next committed event with `event_time ≥ C_k` becomes `F_k` on restart. `I_k` is unchanged, so the evidence, bias, `P_ref` and θ are the same. `freeze_event_identity` and `prediction_time` are those of the committed trigger, consistently everywhere, because nothing was ever published. |
| W2: `F_k` committed, prediction not written | row present, key absent | Replay recomputes and writes the identical content; provenance is `REPLAY_GENERATED` / `crash_recovery` |
| W3: prediction written, provenance not written | prediction present | Replay verifies identical content (no-op) and inserts the missing provenance as `REPLAY_GENERATED` / `crash_recovery` |
| W4: prediction and provenance written | both present | Replay verifies content (no-op) and leaves provenance untouched |
| W5: outcome windows | analogous for the closing event | Same rules for outcome and outcome provenance |
| any: recomputed content ≠ stored | conflict | `m30_prediction_conflict` / `m30_outcome_conflict`, fail closed per Decision 5B. Never an overwrite. |

**Required tests (Phase 2A pure; Phase 2B integration).**
- For every crash window, inject a crash, then restart, and assert byte equality with an uninterrupted run.
- A hash-content scan finds no wall-clock-derived or lifecycle field.
- Recompute from checkpoint versus from zero.
- A deliberately unversioned rule change is detected as a conflict.

## Decision 13 — Feature Lifecycle (depends on draft ADR-023; no competing contract)

- M30 Bias adopts the ADR-023 contract unchanged as feature `m30_next_candle_bias`, with units = algorithm ids.
- Lifecycle `DISABLED → SHADOW → ACTIVE` (Q-M2):
  - **DISABLED** (default): no computation, no state and no records. The API reports disabled.
  - **SHADOW**: compute, persist and display with a shadow marker.
  - **ACTIVE**: production-visible analytical output only, with **no** decision authority of any kind (Decision 1).

  M30 has no decision consumer at any lifecycle, so ADR-023's consumer-usability rule never grants it influence.
- **Dependency and conflict with the ADR-023 draft.** ADR-023 Decision 7 currently rejects ACTIVE for every V1 feature, and its Decision 1 limits the registry to `pattern_engine`. Q-M2's non-decisional ACTIVE for M30 therefore requires either an ADR-023 amendment or a later M30 promotion ADR that ADR-023 permits. Until then, M30 Bias is SHADOW at most. Only Track D may change ADR-023. This ADR does not.
- The lifecycle and `FeatureStatus` in force are recorded in generation provenance, not in hashed content (Decision 4).
- **Before ADR-023 is accepted:** Phase 2A pure core only (Q-M1). It has no `features.py` import, no config, no runtime wiring and no SHADOW activation.

## Decision 14 — Pattern Engine dependency

None in V1. Pattern evidence (ADR-024 `PatternResult`) is an optional future input, only after ADR-024 is accepted, through the ADR-023 usability rule and N1/N8, with a per-result `(result_id, used | ignored, reason_code)` item in `evidence`. Legacy `signals.decision.patterns` may appear only as Signal evidence.

## Decision 15 — Backtest and replay

- The same pure module runs over any recorded canonical tick/quote stream, with ADR-014 manifest and hash checks. There is no second implementation.
- Backtest-materialized records are `REPLAY_GENERATED` / `backtest`, and are written only to isolated journals.
- **Bar datasets are not eligible** until a bar contract defines `interval_start`, `interval_end` and knowledge time (≥ `interval_end`).
- ADR-014 trade metrics do not apply. Evaluation follows Decision 6 reporting rules.
- Replay and backtests never touch PROD storage (AGENTS §8).

## Decision 16 — UI contract (future; not in Phase 1 or 2A)

- A standalone card that is independently hideable, never drawn on the P&F chart. It reads only the API, and no frontend computes or infers a bias.
- Fields:
  - Bias;
  - Evidence;
  - Status (lifecycle, health);
  - Prediction frozen at (`prediction_time`, cutoff);
  - Target candle (Asia/Bangkok display, with UTC available);
  - the last outcome;
  - generation mode.
- Thai explanatory text, for example:
  - "ทิศทางที่หลักฐานเอนเอียงสำหรับแท่ง M30 ถัดไป — ไม่ใช่การคาดการณ์ที่แน่นอนและไม่ใช่สัญญาณเข้าเทรด"
  - "โหมดทดลอง (Shadow): ไม่มีผลต่อการตัดสินใจของระบบ"
  - ACTIVE: "แสดงผลเชิงวิเคราะห์เท่านั้น — ไม่มีผลต่อ BUY/SELL, Matrix, Signal, Entry Readiness หรือ Risk"
  - "แท่ง M30 ของ NEXORA สร้างจากราคาที่สุ่มตัวอย่าง อาจต่างจากแท่ง MT5"
- States stay in English. DISABLED ⇒ "Disabled" and no stale values.

## Decision 17 — Implementation phases

| Phase | Content | Gate |
|---|---|---|
| **2A Pure Core** | `packages/nexora/m30_bias/{__init__,models,candles,freeze,evaluate}.py` plus pure unit tests: bucketing and boundaries; `candle_id`/`algorithm_key`/`prediction_id` golden vectors; freeze and eligibility; evaluation and MFE/MAE; N1–N8 future-injection and prefix-invariance tests; crash-window equivalence at the pure level (recompute from committed-row fixtures). There is no persistence, config, `features.py`, runtime, API or UI. It uses a test-only fixture algorithm. | ADR-026 accepted (Architect scope); Quant items may still be open, and threshold-dependent fields stay null |
| **2B Runtime/Checkpoint integration** | sidecar service and journal writer; generation provenance; wiring in `research/runtime.py`; explicit checkpoint section in `research/checkpoint_state.py`; SHADOW activation via `features.py` | ADR-023 accepted; Track B coordination complete |
| 2C Read API | read-only endpoints | 2B |
| 2D UI card | Decision 16 | 2C |
| Algorithm | the first real bias algorithm | separate Quant ADR (Decision 10) |

## Consequences

- One new pure package, derived journal streams keyed by `algorithm_key` and `candle_id`, and read-only endpoints. No SQL migration, no pipeline output change, no stream-id change.
- Runtime wiring depends on ADR-023 and Track B. Candle identity follows ADR-025 when it lands.
- Outcomes are sampled-data research labels. Nothing in this feature can change a trading decision at any lifecycle.

## Open decisions

| # | Owner | Status |
|---|---|---|
| Q-M1, Q-M2, Q-M5, Q-M6, Q-M7 | Architect | **frozen** (Decision 0) |
| **Q-M3** outcome label and θ policy/parameters | Quant | **QUANT DECISION REQUIRED** |
| **Q-M4** lead time Δ (0 is a candidate only) | Quant | **QUANT DECISION REQUIRED** |
| **Q-M8** eligibility thresholds beyond the structural rule | Quant | **QUANT DECISION REQUIRED** |
| ADR-023 amendment for non-decisional ACTIVE (Decision 13) | Track D / Architect | dependency |
| ADR-025 time-contract version string and `feed_id` availability in recorded provenance | MT5 broker track / Architect | dependency |

## Out of scope

- the bias algorithm;
- model training, fitting or parameter search;
- probability or confidence display;
- chart overlays;
- any change to Signal, Matrix, Entry Readiness, the Pattern Engine or Risk;
- broker timezone logic (Q-M7);
- MT5 bar ingestion;
- TS1 implementation;
- changes to ADR-023, ADR-024 or ADR-025;
- UI before 2D;
- PROD data access or configuration.
