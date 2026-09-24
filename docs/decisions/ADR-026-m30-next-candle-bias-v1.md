# ADR-026 — M30 Next Candle Bias V1: time, prediction and evaluation contract

Status: **proposed — draft, NOT accepted** (awaiting Rin architecture review; Quant review for Decisions 6, 7 and 10)
Date: 2026-09-24
Workstream: `claude/m30-next-candle-bias-v1`, worktree `D:\NEXORA\NEXORA-M30-BIAS` (ARCHITECT role for the proposal; no implementation)
Base: `origin/main` `4f69e9c38803796ccfc8afc01480deffd8499c38`
Task: [M30B1](../../tasks/M30B1-next-candle-bias-v1.md)
Related: [ADR-004](./ADR-004-market-data.md), [ADR-011](./ADR-011-signal-evidence-policy.md), [ADR-014](./ADR-014-backtest-lab-reproducibility.md), [ADR-019](./ADR-019-explicit-feed-time-correction.md), [ADR-022](./ADR-022-startup-recovery-checkpoint-v1.md), [EX1 Experience contract](../../tasks/EX1-experience-engine-v1.md), [DC1 Decision Clarity](../../tasks/DC1-decision-clarity-bias-v1.md), [TS1 time-semantics proposal](../../tasks/TS1-time-semantics-proposal.md). ADR-023 (Feature Lifecycle) and ADR-024 (Pattern Engine) are Track D drafts on `claude/pnf-pattern-engine-v1`, not on `main`.

## Context

The M30 Next Candle Bias is a deterministic, measurable **bias/evidence record** for the next 30-minute candle. Its historical performance can be evaluated later. It is **not** a prediction claim, a probability, a trade permission or an input to any decision. This ADR freezes only the time, prediction, evaluation and no-look-ahead contracts. It deliberately does **not** define a bias algorithm.

Repository facts that constrain the design (verified at `4f69e9c`):

1. **No M30 or candle concept exists.** `ResearchPipeline` consumes a single canonical stream of `NormalizedPriceEvent`s. Live, those are polled MT5 quotes (`apps/api/nexora_api/quotes.py`, `symbol_info_tick`), recorded only when the quote changes. No `copy_rates`, timeframe or interval field exists anywhere in `packages/` or `apps/`.
2. **The bar contract has no interval semantics.** `MarketBar` carries one `event_time` and complete OHLC. The fixtures set `received_at = event_time + 2s` with the bar's close already known. Whether `event_time` is bar open or bar close is undefined. EX1 already refused bar high/low excursion inference for this reason.
3. **Time fields.** `event_time` is market occurrence time in UTC. For MT5 it is corrected by `NEXORA_MT5_TIME_OFFSET_SECONDS` (ADR-019), a workaround with unverified broker timezone and DST semantics. `received_at` is knowledge time. The pipeline requires `received_at >= event_time`, and rejects `event_time` going backwards or `source_sequence` not increasing (`out_of_order_event`). Events reach the pipeline in journal order.
4. **Pipeline output is causal per row.** The output of row *i* is computed from rows ≤ *i* only (`matrix.process(event, now=event.received_at)`). Structure pivots carry `confirmation_time`, and candidate levels and trendlines are as-of snapshots that can change later.
5. **Experience (EX1) already fixes knowledge-time conventions.** T0 = `received_at`. Freezing uses the originally recorded output, never a recomputation from the current engine. Records are derived append-only journal streams keyed for write-once with conflict detection. Excursions are sampled lower bounds. Late data never revises a completed label. Experience reserves a `"bias": null` context slot. This ADR does not use or fill that slot.
6. **"Bias" is already taken.** DC1 `DecisionContext.bias` (`BULLISH`, `BEARISH`, `*_LEAN`, `MIXED`, `UNAVAILABLE`) is a Matrix-direction summary computed at the API layer. It is not persisted and has no time horizon.
7. **Recovery cost.** ADR-022 measured `ExperienceService.observe` at 93% of full-replay time. Checkpoint state lists every component attribute in `COVERED_FIELDS`, and a contract test fails when a component gains uncovered state.
8. **Stream identity = config hash.** Any new field on `RuntimeConfig`/`PipelineConfig` changes the research stream id and the Experience scope (ADR-023 draft, Context 1).

## Decision 1 — Scope and non-goals

In scope for V1 (after acceptance):
- a pure core module that builds NEXORA M30 buckets from the canonical stream, freezes bias records at a defined cutoff, and evaluates outcomes;
- an append-only persisted record set;
- read-only API;
- later, a separate UI card.

Never in scope:
- a feedback path into Signal, Entry Readiness, Risk, Paper, Matrix, Structure, Trendline, Pattern Engine or chart overlays;
- any probability or win-rate claim;
- learning from outcomes;
- broker orders.

The bias algorithm itself is out of scope for this ADR. It requires a separate Quant-approved decision (Decision 10).

## Decision 2 — Time vocabulary and the NEXORA M30 bucket

| Term | Definition |
|---|---|
| `event_time` | UTC market occurrence time of a canonical event (after the ADR-019 correction, as recorded) |
| `received_at` | UTC knowledge time of a canonical event |
| canonical order | journal row order of the research stream. It is monotonic in `event_time` because the pipeline rejects backward times. |
| bucket `k` | half-open interval `[B_k, E_k)`, where `B_k = floor(event_time_epoch / 1800) × 1800` in UTC and `E_k = B_k + 1800s` |
| NEXORA M30 candle | sampled OHLC of the canonical event `price` (the pipeline `price_source`) over the events in bucket `k`: open = first, close = last, high/low = sampled max/min, plus `sample_count`, first/last event identities and `max_sample_gap_seconds` |

- An event at exactly `B_k` belongs to bucket `k`. An event at `B_k − 1µs` belongs to bucket `k−1`.
- Buckets are UTC-aligned, so the host, browser and display timezones never affect them. Bangkok display is presentation only (TS1).
- **A NEXORA M30 candle is not an MT5 M30 bar.** It is built from sampled quotes in the configured price source, so its high and low are lower bounds on the true range. It matches broker M30 boundaries only if the broker server offset is a multiple of 30 minutes and the ADR-019 correction is right for the recorded period. The UI and reports must say "NEXORA M30 (sampled)".
- **Bucket closure.** Bucket `k` is closed by the first canonical event with `event_time ≥ E_k` (the *closing event*). Canonical `event_time` is monotonic, so no later canonical event can belong to `k`. Out-of-order ticks never enter the canonical stream (ADR-004/pipeline). That is a recorded limitation, not a revision path.
- No wall clock is read in core code. There is no scheduler, and there are no synthetic clock events. A bucket without events simply has no candle. A market that stays closed leaves the closure pending until the next event.

## Decision 3 — Prediction cutoff and freeze point

For target bucket `k`, with `Δ = freeze_lead_seconds` (integer, `0 ≤ Δ < 1800`):

- **Prediction cutoff** `C_k = B_k − Δ`.
- **Freeze trigger event** `F_k` = the first canonical event with `event_time ≥ C_k`.
- **Information set** `I_k` = all canonical rows before `F_k` in canonical order. This is identical to "all canonical events with `event_time < C_k`", because canonical `event_time` is monotonic. `F_k` itself is **not** in `I_k`.
- **Snapshot** = the originally recorded pipeline output of the last row of `I_k` (row `F_k − 1`), plus the M30 module's own state built from `I_k`. It is never recomputed from the current engine code.
- The prediction is **frozen** when `F_k` is processed. From that moment the record is immutable. Later data, later snapshots, late ticks, restarts and replays cannot change it. A replay recomputes the record and must produce byte-identical content; otherwise the write fails visibly (journal conflict guard).

Recorded timestamps:

| Field | Value |
|---|---|
| `cutoff_time` | `C_k` |
| `snapshot_event_time` / `snapshot_received_at` | `event_time` / `received_at` of the last event in `I_k` |
| `snapshot_event_identity` | its `identity_key` (journal sequences are backend-local, identities are not) |
| `prediction_time` | `F_k.received_at` — the knowledge time at which freezing became possible |
| `freeze_event_identity` | `F_k.identity_key` |
| `target_start` / `target_end` | `B_k` / `E_k` |

**Eligibility.** A record is `FROZEN` only if `I_k` contains at least one event with `event_time ∈ [B_k − 1800s, C_k)`, meaning the feed was alive in the 30 minutes before the cutoff. Otherwise one `SKIPPED` record is written for the bucket containing `F_k`, with `reason_codes = ["discontinuous_feed"]`, and nothing is written for the empty buckets in between. This covers weekends, session breaks and outages. Warmup, disabled evidence and invalid inputs produce `bias = UNAVAILABLE` with reason codes. They do not produce a skip.

**Late freeze.** If `prediction_time > B_k`, the record carries `freeze_after_target_open = true` and `freeze_lag_seconds = prediction_time − B_k`. It remains leak-free, because `I_k` is defined by `C_k`, not by arrival. It was simply not available before the candle opened, and reports must be able to separate such records.

**Choice of Δ.**
- `Δ = 0` uses all information up to the boundary. The record then typically freezes a fraction of a second after the target opens, on the first tick of the target.
- `Δ > 0` freezes before the target opens whenever the feed is live, at the cost of ignoring the last `Δ` seconds.

The contract supports both. **Recommended V1 value: `Δ = 0`**, subject to Quant decision (Q-M4). Δ is part of the record and of its scope.

A wall-clock or journaled synthetic clock-event freeze was considered and rejected for V1. It adds a non-market event type to the canonical journal and couples to TS1, which is blocked.

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
class M30BiasPrediction:
    schema_version: Literal[1]
    policy_version: str       # "m30-bias-v1" (measurement/time policy, this ADR)
    prediction_id: str        # sha256(policy_version, scope, target_start)
    scope: str                # Decision 11
    source: str; symbol: str; price_source: str; units: str
    target_start: datetime; target_end: datetime
    freeze_lead_seconds: int
    cutoff_time: datetime
    prediction_time: datetime
    snapshot_event_time: datetime; snapshot_received_at: datetime
    snapshot_event_identity: str; freeze_event_identity: str
    freeze_after_target_open: bool; freeze_lag_seconds: Decimal
    status: M30BiasRecordStatus
    bias: M30BiasValue        # SKIPPED => UNAVAILABLE
    reason_codes: tuple[str, ...]
    reference_price: Decimal | None    # P_ref = price of the last event in I_k
    threshold: Decimal | None          # θ frozen at cutoff (Decision 6); null if unavailable
    threshold_policy: str              # e.g. "fixed:<v>" | "box:<k>" | "range:<n>x<k>"; Quant-locked
    evidence: tuple[M30BiasEvidence, ...]
    algorithm_id: str; algorithm_version: str
    feature_status: dict      # ADR-023 FeatureStatus block in force at freeze (once accepted)
    provenance: dict          # runtime stream, runtime config hash, pipeline/engine versions,
                              # snapshot output hash, ADR-019 time_offset_seconds
```

- No `probability`, `confidence` or `win_rate` field exists. An algorithm-specific score, if the Quant ADR allows one, must be named as a score and never displayed as a likelihood.
- `NO_EDGE` is an abstention: the algorithm ran on valid inputs and found no directional lean. `UNAVAILABLE` means it could not evaluate. Reports must show coverage, meaning the share of `UP`/`DOWN` records among `FROZEN` ones, next to any directional result.
- The record payload is purely deterministic. Wall-clock "first materialized at" and origin (`live` versus `replay`) go to a separate audit stream (Decision 11). Otherwise replay would conflict with the live-written record.

## Decision 5 — Target candle and evaluation window

- **Target candle** = bucket `k` = `[B_k, E_k)`, the bucket whose start `B_k` defines `C_k`. With `Δ = 0` it is the bucket that `F_k` opens.
- **Evaluation window** `W_k` = the canonical events with `event_time ∈ [B_k, E_k)`.
- **Evaluation time** = `received_at` of bucket `k`'s closing event (Decision 2). The outcome is written exactly once, when that event is processed.
- **MFE window = MAE window = `W_k`**. When `Δ > 0`, the price path in `[C_k, B_k)` is not part of the target. Only `pre_target_drift` (below) summarizes it.
- `W_k` empty, which is possible only when `Δ > 0` and the feed stops before `B_k`: the outcome is `NO_DATA`, with all price fields null and `no_in_window_samples`.
- The closing event is not yet seen (market closed, outage): the outcome stays pending. Pending state is not persisted as a result. No price is invented for downtime.

## Decision 6 — Candidate outcome definitions (Quant decision required)

All candidates are computed from `W_k` and the frozen `P_ref` and `θ`. They must not be selected by headline accuracy.

| # | Definition | Strengths | Weaknesses |
|---|---|---|---|
| O1 | Candle body direction `sign(close_k − open_k)` | Matches the usual chart reading | `open_k` is post-cutoff information that is not the prediction reference. Doji and noise count as full wins or losses. Ignores the gap from `P_ref`. A sampled open is fragile. |
| O2 | Raw close return `r_k = close_k − P_ref` (signed; also `/θ`) | Consistent with the information set; continuous; no threshold | Not a class label by itself |
| O3 | **Thresholded close return** (ternary): `UP` if `r_k ≥ +θ`, `DOWN` if `r_k ≤ −θ`, else `FLAT` | Separates noise from moves. `NO_EDGE` can be judged against `FLAT`. θ is frozen causally. | Depends on the choice of θ. Band edges are sensitive. |
| O4 | First-touch excursion: which of `P_ref ± θ` the samples cross first in `W_k`; `NONE` / `AMBIGUOUS` when both are crossed between two samples | Path-aware, closer to a trade-like reading | Path-dependent on sampled data. The ambiguity rate must be reported. |
| O5 | Directional excursion pair (MFE, MAE; Decision 7) and `MFE − MAE` | Captures opportunity versus adverse move | Not a single label; sampled lower bounds |

**Recommendation.**
- The **primary label is O3**, with **θ volatility-scaled and frozen at the cutoff** from information in `I_k` only. Candidate θ policies:
  - `k ×` the current structure-resolution adaptive box size at the snapshot;
  - `k ×` the mean sampled range of the last `n` closed NEXORA M30 candles.
- O2, O4 and O5 are always recorded. O1 is recorded as a reference only and is never the headline metric.
- The θ policy, `k` and `n` are Quant decisions (Q-M3). Until then the contract stores `threshold` and `threshold_policy` but no default.

**Evaluation reporting rules (for later work, frozen here).**
- Always report:
  - coverage;
  - per-class counts;
  - a confusion table against O3;
  - mean and median O2 conditioned on bias;
  - MFE/MAE distributions;
  - the O4 ambiguity rate.
- Compare against causal baselines: persistence (sign of the previous candle's O2), the majority class over the prior window, and always-`NO_EDGE`.
- Use chronological splits only.
- Separate live-materialized records from replay-materialized ones, and `freeze_after_target_open` records from the rest.
- No probability or win-rate claim appears in the UI or API.

## Decision 7 — Evaluation record, MFE and MAE

```python
M30BiasOutcomeStatus = Literal["EVALUATED", "NO_DATA"]

@dataclass(frozen=True, slots=True)
class M30BiasOutcome:
    schema_version: Literal[1]
    policy_version: str
    prediction_id: str
    status: M30BiasOutcomeStatus
    evaluation_time: datetime          # closing event received_at
    closing_event_identity: str
    open: Decimal | None; high: Decimal | None; low: Decimal | None; close: Decimal | None
    sample_count: int
    first_sample_time: datetime | None; last_sample_time: datetime | None
    max_sample_gap_seconds: Decimal | None
    gap_or_incomplete_samples: int     # events flagged is_gap / completeness != complete
    close_return: Decimal | None       # O2 = close - P_ref
    body: Decimal | None               # O1 = close - open
    pre_target_drift: Decimal | None   # open - P_ref
    label_threshold: Literal["UP","DOWN","FLAT"] | None   # O3; null if θ null
    first_touch: Literal["UP","DOWN","NONE","AMBIGUOUS"] | None  # O4
    mfe: Decimal | None; mae: Decimal | None               # UP/DOWN only
    mfe_time: datetime | None; mae_time: datetime | None
    up_excursion: Decimal | None; down_excursion: Decimal | None   # always
    reason_codes: tuple[str, ...]
```

With reference `P_ref` and samples `p ∈ W_k`:

- `up_excursion = max(0, max(p − P_ref))`
- `down_excursion = max(0, max(P_ref − p))`

These are always recorded, for every bias value, like Experience's WAIT handling.

- **MFE** (bias `UP`) = `up_excursion`. (bias `DOWN`) = `down_excursion`.
- **MAE** (bias `UP`) = `down_excursion`. (bias `DOWN`) = `up_excursion`.
- `NO_EDGE` / `UNAVAILABLE` / `SKIPPED`: `mfe = mae = null`. No direction is fabricated.
- `mfe_time` / `mae_time` = `event_time` of the first sample that attains the extreme.
- Values are in price `units`. Reports may divide them by the frozen θ.
- The reference is `P_ref`, not `open_k`: `open_k` is unknown at the cutoff, and `P_ref` is the price the record was made against.

**Semantics.**
- Values are sampled lower bounds on the true excursion.
- There is no intrabar or path inference, and no interpolation.
- There is no bid/ask fill model, and no costs or P&L.
- Empty window: null and `no_in_window_samples`.
- The outcome never reads the algorithm's evidence. It reads the bias only to orient MFE and MAE.

## Decision 8 — No-look-ahead boundary (frozen rules)

| # | Rule |
|---|---|
| N1 | Every evidence item has `as_of_event_time < cutoff_time` and `as_of_received_at ≤ snapshot_received_at`. Violation ⇒ the record is `UNAVAILABLE` with `evidence_after_cutoff`, never a silent drop. |
| N2 | Bias computation reads only the snapshot (the recorded output of the last row of `I_k`) and M30 state built from `I_k`. It never reads rows at or after `F_k`, the current engine's recomputation, or any later snapshot of repaintable objects (candidate levels, trendlines). |
| N3 | θ and `P_ref` are computed from `I_k` and frozen in the record. |
| N4 | Bias computation never reads outcomes of any M30 record, Experience outcomes or labels, backtest results, or evaluation statistics. There is no learning or feedback in V1. |
| N5 | Records are write-once. A recompute with different content is a visible failure, never an overwrite. A changed algorithm, policy, θ policy or Δ means a new scope or namespace (Decision 11). |
| N6 | Core code reads no wall clock and no environment. Every timestamp in a record comes from canonical events. |
| N7 | Outcomes use only `W_k` and the closing event. They never update the prediction record. |
| N8 | Pattern Engine results, if ever used, must satisfy N1 through the result's own `confirmation`/`status_time` and ADR-023's usability record (Decision 9). |

## Decision 9 — Evidence available at prediction time

All of the following are causal by construction when read from the snapshot. Which of them the algorithm uses is decided by Quant (Decision 10).

| Source (snapshot field) | Usable as evidence | Notes |
|---|---|---|
| `matrix.resolutions[*]` direction, status, latest transition | yes | Warmup and stale resolutions must map to `UNAVAILABLE` evidence, not to a direction |
| P&F `columns`/`transitions` (structure resolution): current column direction, length in boxes, box size, distance to reversal | yes | The current column is still developing. Use its as-of state only. |
| `structure` confirmed pivots | yes, only if `confirmation_time < cutoff_time` | Candidate levels are as-of only (N2) |
| `trendline` lines/anchors | yes, as-of snapshot | Lines can be redrawn later, so a later snapshot must never be used |
| `regime.state` | yes | |
| `signals.decision` action and evidence polarity | yes, as **evidence only** | M30 bias never feeds back into Signal |
| `entry_readiness` | not recommended | It is a permission filter, and using it would blur permission with evidence |
| DC1 `derive_decision_context` | may be recomputed from the snapshot's matrix and decision with the same pure function | Do not duplicate its logic |
| Prior NEXORA M30 candles (closed buckets in `I_k`) | yes | Simple statistics only. No candlestick-pattern detection (that is pattern logic, AGENTS §2). |
| Spread and feed age of the last `I_k` event | yes (quality evidence) | |
| Session, news, calendar, `data_version` | **not available** | EX1 records them as null. No inference. |
| Experience outcomes, M30 outcomes, backtest statistics | **forbidden** (N4) | |

## Decision 10 — Algorithm is a separate decision

Any bias algorithm (`algorithm_id`, rules, evidence codes, thresholds) needs its own Quant-approved ADR that references this one. The first algorithm should be rule-based and fully explainable from its `evidence` tuple. It must not be trained or fitted. Parameter search against M30 outcomes is out of scope until a separate evaluation-protocol ADR defines in-sample and out-of-sample splits.

## Decision 11 — Persistence, scope and runtime placement

- **Pipeline output is not changed.** No field is added to `RuntimeConfig`/`PipelineConfig`, so the research stream id and Experience scope do not change. Journal rows gain nothing, which keeps ADR-022 costs and the Journal V2 track unaffected.
- **Placement.** A sidecar service beside `ExperienceService` observes each committed runtime row after the commit. It is pure core logic plus a thin journal writer.
- **Scope** = `canonical_hash(runtime stream, source, symbol, price_source, units, policy_version, algorithm_id, algorithm_version, feature_config_hash, Δ, threshold_policy, time_offset_seconds)`.
- **Streams.** These reuse migration 007 `research_journal` (no SQL migration):

| Stream | `event_key` | Payload |
|---|---|---|
| `m30bias:v1:{scope}:predictions` | `target_start` ISO | `M30BiasPrediction` |
| `m30bias:v1:{scope}:outcomes` | `target_start` ISO | `M30BiasOutcome` |
| `m30bias:v1:{scope}:audit` | `target_start` ISO + `:` + origin | first materialization origin (`live` / `replay`) and wall-clock time. Non-deterministic by design and never part of a record's content hash. |

- **Write order.** Runtime row commit → Paper → Experience → M30 prediction (on `F_k`) → M30 outcome (on the closing event). Each append is transactional, and replay fills only missing keys.
- **Retroactive generation.** When the feature is enabled on an existing journal, replay produces records for historical buckets. They are causal and identical to what live would have produced, and the audit stream marks them as `replay`. Reports must be able to separate live-materialized records (Q-M6).

## Decision 12 — Restart and recovery

- **Bounded state.** The sidecar keeps:
  - the in-progress bucket;
  - the last `n` closed candles, if the algorithm needs them;
  - the last row's extracted evidence;
  - pending outcomes (at most one open target);
  - a seen-key set bounded to open keys.

  It never keeps full history. Per-row cost must be O(1) amortized, so as not to repeat the Experience recovery problem (ADR-022 Context).
- **Checkpoint.** The sidecar state must be added to ADR-022 explicit checkpoint state (`COVERED_FIELDS`, a `STATE_VERSION` bump, a restore validation that includes the M30 scope and `feature_config_hash`). This is a change to Track B / DEV-PERF files (`research/checkpoint_state.py`, `research/runtime.py`) and needs coordination.
- **Equivalence.** Cold full replay, checkpoint restore plus delta, restart at every row boundary (including between `F_k` and the prediction write, and between the closing event and the outcome write), a missing or corrupt checkpoint, and appending after a restart must all yield identical prediction and outcome content.
- A lifecycle or config change takes effect only at process start, and under a new scope when the scope inputs change (ADR-023 draft 6c). Records written under an old scope are never rewritten.

## Decision 13 — Feature Lifecycle (depends on draft ADR-023; no competing contract)

- M30 Bias adopts the ADR-023 contract **unchanged**: feature id `m30_next_candle_bias`, with units = algorithm ids.
  - **DISABLED** (default when the features file is unset): no computation, no state, no records, and the API reports disabled.
  - **SHADOW**: compute, persist and display with a shadow marker.
  - The `FeatureStatus` block in force at the freeze is embedded in each record.
- M30 Bias has **no decision consumer**. Under ADR-023 Decision 7, ACTIVE is not selectable in V1, and for this feature ACTIVE would not differ from SHADOW in decision effect. Whether ACTIVE ever means more than "validated, still non-decisional" is an Architect question (Q-M2). Until it is answered, M30 Bias is SHADOW at most.
- ADR-023 Decision 1 limits the V1 registry to `pattern_engine`. Registering `m30_next_candle_bias` requires ADR-023 acceptance plus this ADR's acceptance.
- **Until ADR-023 is accepted,** only the lifecycle-independent pure core may be implemented, if Rin allows it (Phase 2a: bucketing, freeze, outcome evaluation, tests). That core has no runtime wiring, config, API or persistence.

## Decision 14 — Pattern Engine dependency

None in V1. Pattern evidence (ADR-024 `PatternResult`) is an optional future input. It may be added only after ADR-024 is accepted, through the ADR-023 Decision 5 usability rule and N1/N8, with a per-result `(result_id, used | ignored, reason_code)` record inside `evidence`. The legacy `signals.decision.patterns` pivot patterns may appear only as Signal evidence (Decision 9), never as a Pattern contract.

## Decision 15 — Backtest and replay

- The same pure module runs over any recorded **canonical tick/quote stream**: a research journal copy, or an isolated dataset of `NormalizedPriceEvent`s with ADR-014 manifest and hash checks. There is no second implementation.
- **Bar datasets are not eligible** until a bar contract defines `interval_start`, `interval_end` and knowledge time (≥ `interval_end`). Consuming current `MarketBar`s would be look-ahead-ambiguous (Context 2).
- ADR-014 trade metrics (win rate, profit factor) do not apply. Evaluation follows Decision 6 reporting rules in a separate report artifact.
- Replay never touches PROD storage. Tests and backtests use temporary journals (AGENTS §8).

## Decision 16 — UI contract (future; not implemented in Phase 1)

- A standalone card that is independently hideable, **never drawn on the P&F chart**. It reads only the API. No frontend computes, infers or recolors a bias.
- Fields:
  - Bias (`UP`/`DOWN`/`NO_EDGE`/`UNAVAILABLE`/`SKIPPED`);
  - evidence (list of code, polarity, as-of);
  - status (`SHADOW`/`DISABLED`, health);
  - Prediction frozen at (`prediction_time`, cutoff);
  - Target candle (`target_start`–`target_end`, shown in Asia/Bangkok with UTC available);
  - the last evaluated outcome.
- Thai explanatory text, for example:
  - "ทิศทางที่หลักฐานเอนเอียงสำหรับแท่ง M30 ถัดไป — ไม่ใช่การคาดการณ์ที่แน่นอนและไม่ใช่สัญญาณเข้าเทรด"
  - "โหมดทดลอง (Shadow): ไม่มีผลต่อการตัดสินใจของระบบ"
  - "แท่ง M30 ของ NEXORA สร้างจากราคาที่สุ่มตัวอย่าง อาจต่างจากแท่ง MT5"
- States stay in English.
- DISABLED ⇒ the card shows "Disabled" and no stale values.

## Consequences

- One new pure package (proposed `packages/nexora/m30_bias/`), three derived journal streams, and read-only endpoints. No SQL migration, no pipeline output change, no stream-id change.
- It depends on ADR-023 acceptance for runtime wiring, and on Track B coordination for checkpoint state.
- Outcomes are sampled-data research labels. Nothing in this feature can change a trading decision.

## Open questions / Architect and Quant decisions

- **Q-M1 (Architect)** Is M30 Bias acceptable as the second ADR-023 registry feature, and may the lifecycle-independent pure core (Phase 2a) start before ADR-023 is accepted?
- **Q-M2 (Architect)** What does ACTIVE mean for a feature with no decision consumer? The options are to keep SHADOW as its permanent maximum, or have ADR-023 define non-decisional ACTIVE.
- **Q-M3 (Quant)** Primary outcome label (O3 recommended), θ policy, and `k`/`n`.
- **Q-M4 (Quant)** `freeze_lead_seconds` (0 recommended).
- **Q-M5 (Architect / DEV-PERF)** Sequencing of the checkpoint-state extension with Track B.
- **Q-M6 (Architect)** Retroactive replay-materialized records: keep them (recommended, marked through audit) or restrict records to buckets after an explicit `effective_from`.
- **Q-M7 (Architect)** Should the ADR-019 `time_offset_seconds` be carried in scope as proposed? A change in the offset shifts bucket membership. This interacts with blocked TS1.
- **Q-M8 (Quant)** Eligibility thresholds beyond "feed alive in the prior 30 minutes", such as a minimum sample count or a maximum gap.

## Out of scope

- the bias algorithm;
- model training, fitting or parameter search;
- probability or confidence display;
- chart overlays;
- changes to Signal, Entry Readiness, Pattern Engine or Risk;
- MT5 bar ingestion;
- TS1 implementation;
- UI in Phase 1;
- PROD data access or configuration.
