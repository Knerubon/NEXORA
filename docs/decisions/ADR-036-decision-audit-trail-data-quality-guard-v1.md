# ADR-036 — Decision Audit Trail V1 and Data Quality Guard V1

Status: Proposed (draft; hardening round 1 applied; requires independent re-review and Rin approval)
Issue: #78 (SYSTEM-READINESS-1, Wave 2A Track B)
Task: [AUDIT-DQ-1](../../tasks/AUDIT-DQ-1-decision-audit-data-quality.md)
Related: ADR-004, ADR-009, ADR-011, ADR-012, ADR-015, ADR-021, ADR-024, ADR-034, ADR-035

## Context

System Test readiness needs (1) a mandatory, reconstructable audit record for every
important BUY/SELL/WAIT decision and (2) a deterministic fail-closed data-quality gate.
Repository reconnaissance (see task record) found:

- The only decision origin is `SignalEngine.evaluate`, called from `ResearchPipeline._process`
  (`packages/nexora/research/pipeline.py`). WAIT decisions have **no** `signal_id`; only
  actionable BUY/SELL decisions create a `ResearchSignal`. When the engine suppresses a
  duplicate signal it appends nothing, so `latest` keeps pointing at the *earlier* signal
  while the decision is BUY/SELL again.
- `nexora.storage.Journal` is already transactional, append-only and idempotent by key.
- DQ1/ADR-012 provides a stateful *observability* sidecar (`MarketDataQualityMonitor`). It does
  not check bid/ask validity, spread, non-finite values, instrument identity or snapshot
  completeness, and it defaults to wall-clock `datetime.now(UTC)` (not replay-deterministic).

Independent review of the first revision returned CHANGES REQUESTED with three MAJOR findings
(F1 verdict forgery, F2 invalid Guard configuration, F3 missing emitted signal) and a set of
security items. This revision resolves them; the resolution is recorded in the task record.

## Decisions

### D1. Separate additive packages; no competing engines
- `nexora.decision_audit` (schema, builder, store, fail-closed gate, EVALUATION_BLOCKED record)
  and `nexora.data_quality` (pure validator) are new, pure-domain packages with no UI/API/DB
  imports. DQ1 files, `SignalEngine`, `ResearchPipeline` and `execution/**` are **not modified**.
- The Guard reuses the `QualityStatus` vocabulary and does not replace the DQ1 monitor;
  DQ1 stays the live observability sidecar, the Guard is the per-decision verdict.

### D2. Data Quality Guard verdict
- Input: a market-data snapshot (arrival-ordered `NormalizedPriceEvent` tuple plus a declared
  `QualityExpectation` of source/symbol/units) and an explicit `evaluated_at` supplied by the
  caller. The Guard never reads a wall clock, so historical replay and live paths share one
  code path.
- Output: `QualityVerdict` with `state` one of `ok | blocked | unknown`, `new_trade_permitted`,
  sorted `findings` (stable `code`, severity, raw evidence), `evaluated_at`, `event_keys`,
  `snapshot_hash` and `config_version`. `blocked` means the data is proven invalid (duplicate,
  out-of-order, stale, crossed book, non-finite, identity mismatch, gap, clock anomaly,
  inconsistent OHLC/price); `unknown` means validity cannot be established (missing data,
  incomplete snapshot, missing bid/ask, unverifiable price source, unsynchronized clock, guard
  error). Nothing is repaired or interpolated. A separate "degraded" state is intentionally not
  defined: every non-`ok` state already means NO NEW TRADE.
- **Invariants are enforced at construction** (`QualityVerdict.__post_init__`): the permission
  bit equals `state == "ok"` and cannot be set independently; `ok` requires no findings, a
  timezone-aware `evaluated_at`, a 64-hex `snapshot_hash` and non-empty unique `event_keys`;
  `blocked` requires a blocking finding; `unknown` requires findings and none blocking. An
  inconsistent verdict cannot exist as a value. A *self-consistent* invented verdict can, which
  is why a verdict is never accepted as proof (D2b).
- If a snapshot contains a value canonical hashing rejects (for example NaN), the finding is
  still reported by its real cause (`non_finite_value`), `snapshot_hash` is `None`, and the
  state cannot be `ok`.
- **Strict configuration (F2).** `QualityGuardConfig` rejects, with a stable `ValueError` code:
  non-`int` thresholds (including `bool`, `float`, numeric strings, `Decimal`, NaN/Infinity),
  non-`bool` flags (including `"false"`, `0`, `1`), spread caps that are not a finite, strictly
  positive `Decimal` (floats, ints and strings are never coerced), a spread-to-price ratio above
  1, a malformed `version`, zero where a positive value is required, and values above generous
  sanity ceilings (so `10**12` cannot silently turn a check into a no-op). Contradictions are
  rejected: spread caps with `require_bid_ask=False` (never evaluated), and
  `require_bid_ask=True` with no spread cap (unbounded). Every field is required; there are no
  defaults. `DataQualityGuard.__init__` type-checks and **re-validates** its inputs, so a
  configuration built by bypassing the constructor still cannot reach a verdict. The ceilings
  and the two combination rules are configuration-sanity bounds, not trading semantics; Quant
  owns the real thresholds (open item 4).
- **Price / OHLC trust boundary.** `NormalizedPriceEvent` is a plain dataclass with no
  constructor validation and can arrive from replay, storage or tests, so the Guard (the trust
  boundary for eligibility) re-validates: known event `kind`, `precision` in 0..10, every price
  field finite and `> 0`, bars carry complete OHLC with `low <= open, close <= high`, and
  `price` equals the field named by `price_source` (`mid` = (bid+ask)/2) within one unit in the
  last place. New additive codes: `invalid_price`, `invalid_ohlc`, `incomplete_ohlc`,
  `price_source_mismatch`, `invalid_precision`, `invalid_event_kind`.

### D2a. The Guard is stateless; a sequence-history provider is required
The Guard remembers nothing between calls. Ordering, sequence-gap, duplicate and staleness
checks cover **only the events inside the snapshot it is handed**. A one-event snapshot yields a
verdict about that event alone and no continuity guarantee: a replayed or out-of-order event
whose predecessors are absent looks clean. Consequently:

- `ok` means "this snapshot is internally consistent", not "this stream is continuous".
- Production composition **must** build every snapshot from a trusted history source that
  returns the contiguous, arrival-ordered events ending in the evaluated event, and must set
  `min_events` to match. The contract is `data_quality.SequenceHistoryProvider`
  (`history_ending_at(event) -> MarketDataSnapshot`); **no implementation exists in this PR**.
- The audit binds the verdict to the snapshot's recomputed hash and requires the snapshot to end
  in the decided event (D2b), so a provider cannot substitute different evidence undetected.
- Until a provider exists, no component may treat `ok` on a one-event snapshot as continuity.
  Choosing the provider (live stream buffer vs. journal read) is an architecture decision
  (open item 1).

### D2b. Verdict trust boundary (F1)
A caller-constructed `QualityVerdict` is never trusted proof, so **no public API accepts one**.
`DecisionAuditGate`, `build_decision_audit_record`, `build_evaluation_blocked_record` and
`EvaluationBlockedRecorder.record_blocked` take the market-data `snapshot`, the explicit
`evaluated_at` and (the gate/recorder own) a `DataQualityGuard`, and obtain the verdict
themselves. `DataQualityGuard` is **final** (`__init_subclass__` raises), so `evaluate` cannot be
overridden. The obtained verdict is then verified (`decision_audit.invariants`):

1. it is a `QualityVerdict` that still satisfies its invariants and carries the Guard's
   `config_version`;
2. the snapshot is non-empty and **ends in exactly the evaluated event** (equality, not just an
   identity key), and `event_keys` equals the snapshot's keys, so a verdict for another event,
   a reused verdict, or mismatched keys is rejected (`quality_event_mismatch`);
3. an `ok` verdict's `snapshot_hash` must equal the independently recomputed canonical hash
   (`quality_snapshot_mismatch`); a missing hash on `ok` is rejected;
4. its `evaluated_at` equals the supplied time (`quality_evaluation_time_mismatch`).

The public `verify_quality_binding` additionally **re-derives the verdict through the Guard** and
requires equality (`quality_not_reproducible`), so a self-consistent invented verdict (for
example a blocked verdict relabelled `ok` with its findings removed) cannot pass. Threat model:
this defends against forged, mismatched, reused and hash-less verdicts, Guard-shaped doubles and
accidental misuse. It does not defend against arbitrary code already running in the same process
(which could equally patch the gate); that is an accepted limit of an in-process pure-domain
library.

### D3. Decision Audit record
- `DecisionAuditRecord` (schema_version 1, frozen, canonical-serializable) carries: `decision_id`,
  `correlation_id`, `decided_at` (UTC), market-data reference (including the verified
  `snapshot_hash`), instrument identity, regime/structure/pattern/trendline/entry-readiness
  evidence references, pipeline/signal/engine/regime/readiness config versions, `action`
  (BUY/SELL/WAIT), `score`, reasons, blockers, confirmation requirements, the data-quality
  verdict reference, `trade_eligibility`, and optional risk/authority and lifecycle references.
- `decision_id` is a deterministic content hash of `{kind, symbol, environment, signal_sequence,
  source event identity key, pipeline config version, engine version, schema}`. It deliberately
  excludes the quality evidence, so re-deciding the same decision with different evidence
  collides on the same id and is a hard conflict rather than a second record (D4). It is an
  **audit** identifier, never used as or substituted for a `signal_id`.
- `correlation_id` is `corr:` + the first 32 hex characters of a structured hash of
  `{symbol, source event identity key}`, stable across replays and environments and not
  ambiguous under delimiter characters.
- Evidence is referenced/copied only from engine outputs. Missing evidence is recorded as
  explicitly absent with a reason (`evidence_gaps`); the builder never fabricates evidence, IDs
  or a score: a missing or non-integer `score` is an unreconstructable decision (`invalid_score`),
  not a default of 0.
- **Structured allowlist (P1-1).** Redaction by regex alone is a denylist. `payload_policy`
  declares every persisted field of every record type with an explicit kind (identifier token,
  opaque reference, bounded printable text, 64-hex hash, int, bool, UTC datetime). Exact
  field-set equality is enforced, so an undeclared field cannot reach the journal, and each value
  must satisfy its kind's grammar. Caller-supplied references (`instrument_id`,
  `risk_authority_outcome`, `lifecycle_ref`) get a stricter short-token grammar that rejects
  credential-shaped and long opaque values. The builder copies only the fields it selects, so
  unknown engine keys are never persisted; **credential-like keys anywhere inside the sections the
  audit reads** (`signals` excluding unread `history`, `entry_readiness`, `regime`, `structure`,
  `trendline`, and top-level keys) are rejected outright (`audit_unexpected_sensitive_field`).
  The secret-value patterns remain only as a last layer on free text. Errors carry fixed codes
  and never echo content.
- **Record invariants** are checked when built and again inside the store, so a hand-constructed
  record cannot claim eligibility it has not earned (D3a).

### D3a. Signal emission and eligibility (F3)
`trade_eligibility` is `eligible_for_downstream_gates` **only** when all of the following hold;
otherwise exactly one denial value is recorded and the engine's own decision is still audited
unchanged:

| Condition (evaluated in this order) | `trade_eligibility` |
|---|---|
| action is WAIT | `not_applicable` (never reads `latest`, never gets a `signal_id`) |
| verified quality is not `ok` | `denied_data_quality` |
| `latest` present at this instant but its identity disagrees with the decision | `denied_inconsistent_output` |
| no signal genuinely emitted for this decision (absent `latest`, or `latest` decided at another instant, i.e. suppressed duplicate) | `denied_no_emitted_signal` |
| otherwise | `eligible_for_downstream_gates` |

"Genuinely emitted" means `latest` was decided at this event's instant and `signal_id` is a
non-empty bounded string, `symbol` equals the event symbol, `sequence` equals the decision
sequence, `side` matches the action (BUY=long, SELL=short), `status` is `active`, the embedded
decision action matches, and `latest.reason_codes` equals the decision's positive-evidence codes.
An actionable decision with **no** positive evidence is treated as inconsistent (fail closed;
open item 5). When not eligible, `signal_id` is `None` and `signal_emitted` is `False`: a
signal id is never borrowed from an earlier decision and never invented.

### D4. Mandatory, append-only, fail-closed, replay-safe
- No feature flag, environment variable or config field disables auditing; there is no
  "disabled" code path in the gate. The single entry point is
  `DecisionAuditGate.record_decision(output, event, snapshot, evaluated_at, ...)`.
- Storage reuses `Journal.append` on a dedicated stream; an existing key with a different content
  hash is a hard conflict (`audit_identity_conflict`), never an overwrite. `DecisionAuditGate`
  returns `new_trade_eligible=True` **only** after the append succeeded and only for the call that
  *created* the record. `DENIED_AUDIT_FAILURE` (NO NEW TRADE) covers every persistence failure,
  conflict, verification failure and exception. A WAIT is audited like BUY/SELL; an audit failure
  on WAIT is reported but cannot open a trade.
- **Replay (P1-3).** An identical record already stored is an idempotent no-op reported as
  `AuditGateOutcome.RECORDED_REPLAY` with `new_trade_eligible=False` (the same record is returned
  for reference). A replay, retry or crash-recovery re-run therefore never presents a seen
  decision as a newly authorized trade. Consequence: if a process crashes after the append but
  before downstream consumption, the restart cannot re-authorize; that is deliberately NO NEW
  TRADE and any recovery path is a downstream (Risk/Authority) concern (open item 6).
- **Stream identity (P1-2).** Streams are `audit:v1:<env>:<symbol>` where each component is
  validated (non-empty, no edge whitespace, printable, at most 64 characters, never normalized)
  and percent-escaped (`:` and `%` included) before joining, so the encoding is injective: the old
  collision of environment `x:y` + symbol `S` with environment `x` + symbol `y:S` is impossible.
  EVALUATION_BLOCKED uses a different prefix (`audit-blocked:v1:...`).
- The store trusts the `Journal.append` contract (transactional, hash-verified, raises on
  identity conflict) and performs **no separate read-back**; a journal that reports success
  without persisting is outside V1's threat model and is a Security-review item (open item 7).

### D5. Integration contract (contracts only in V1)
```
Market Data -> SequenceHistoryProvider -> Data Quality Guard -> existing Analysis/SignalEngine
            -> Decision Audit Trail (gate owns the Guard) -> existing Risk/Authority/Execution
```
The gate is a pure, side-effect-isolated adapter that consumes existing pipeline output. V1 does
**not** rewire `ResearchRuntime.ingest`, `ResearchPipeline`, paper trading or any execution code.
End-to-end enforcement is **not claimed**; wiring is a follow-up (below).

### D6. EVALUATION_BLOCKED audit record (additive, isolated contract)
For the case where the SignalEngine cannot safely evaluate, `nexora.decision_audit.blocked`
defines `EvaluationBlockedRecord`:

- separate `record_kind = "EVALUATION_BLOCKED"`, separate id namespace (`blocked:` + sha256 of
  `{kind, environment, symbol, event, snapshot_hash, reason, recorded_at, schema}`) and separate
  stream prefix; it cannot collide with or be confused with a `decision_id`;
- **no** `action`, `score`, `signal_id`, `signal_emitted` or eligibility field exists on the type,
  so a BUY/SELL/WAIT, score or signal id cannot be fabricated for it;
- carries the event reference (identity key, times, sequence, snapshot hash), the verified quality
  reference, a stable `reason_code` (`data_quality_blocked` | `data_quality_unknown`, derived
  from the verdict) and `recorded_at` (the verdict's caller-supplied `evaluated_at`, or the event's
  `received_at` if the clock was unsynchronized; never the wall clock);
- durable, append-only, idempotent via the same Journal contract; `record_blocked` obtains and
  verifies the verdict exactly like the gate, refuses to record an `ok` evaluation, and its
  result type has no eligibility field.

This is a **contract only**. It does not decide when the engine is skipped (a trading-semantics
question reserved for Quant/Rin, open item 2) and is not wired into any runtime. The dependency
"SignalEngine/runtime emits EVALUATION_BLOCKED instead of an audited-then-denied decision" is
**BLOCKED** on that decision plus runtime wiring (open items 2 and 3).

## Non-goals / hard boundaries
No change to BUY/SELL/WAIT scoring, thresholds or Pattern Engine feature flags. No order
submission, no REAL/DEMO/PAPER-unlock/AUTO, no PROD access, no Web->MT5 path, no AI override of
Risk/Execution. `ExecutionPreflight` stays DENY-ONLY.

## Open items / blockers (documented, not guessed)
1. **Sequence-history provider** (D2a): contract exists, implementation does not. Decide the
   source (live stream buffer vs. journal) and set `min_events` accordingly before wiring.
2. **Skip-engine-on-bad-data** is a trading-semantics question for Quant/Rin (AGENTS.md
   section 9). Under non-`ok` quality the engine's decision is still produced and audited
   unchanged; the gate marks BUY/SELL `denied_data_quality`. EVALUATION_BLOCKED (D6) is the record
   to emit if the answer is "skip"; that integration is BLOCKED until decided.
3. **Runtime wiring** touches `research/runtime.py` (shared with recovery/checkpoint,
   ADR-022/029) and checkpoint equivalence; needs Architect review before changing ingest
   semantics.
4. **Configuration-sanity ceilings and combination rules** (D2) are generic guards against
   disabled checks, not Quant-approved thresholds; confirm or replace them with adapter-supplied
   capability data.
5. **Actionable decision with no positive evidence** is denied as inconsistent (fail closed). It
   does not change engine output, but Quant should confirm the engine can never legitimately
   produce one.
6. **Replay never re-authorizes** (D4): confirm the downstream recovery story with Risk/Authority.
7. The store relies on the `Journal.append` contract and does no separate read-back; a
   journal that reports success without persisting is a Security-review item.
8. **Risk/authority outcome field** is populated only if a stable ADR-015/ADR-035 outcome contract
   exists at the call site; V1 carries it as an optional opaque reference (validated, never
   derived).
9. **Edge Validation (#77/#79)** may reuse audit records for backtest provenance; coordinate
   before any shared-file change. No #77/#79 files are touched here (verified: PR #79 changes only
   `packages/nexora/edge/**`, `docs/research/edge-validation/**` and `tests/*edge*`).
10. Eligibility is not authorization: `new_trade_eligible=True` only means audit, verified data
    quality and a genuinely emitted signal did not deny. Risk, Authority and Execution gates keep
    full, independent authority.
