# ADR-036 — Decision Audit Trail V1 and Data Quality Guard V1

Status: Proposed (draft; requires independent review and Rin approval)
Issue: #78 (SYSTEM-READINESS-1, Wave 2A Track B)
Task: [AUDIT-DQ-1](../../tasks/AUDIT-DQ-1-decision-audit-data-quality.md)
Related: ADR-004, ADR-009, ADR-011, ADR-012, ADR-015, ADR-021, ADR-024, ADR-034, ADR-035

## Context

System Test readiness needs (1) a mandatory, reconstructable audit record for every
important BUY/SELL/WAIT decision and (2) a deterministic fail-closed data-quality gate.
Repository reconnaissance (see task record) found:

- The only decision origin is `SignalEngine.evaluate`, called from `ResearchPipeline._process`
  (`packages/nexora/research/pipeline.py`). WAIT decisions have **no** `signal_id`; only
  actionable BUY/SELL decisions create a `ResearchSignal`.
- `nexora.storage.Journal` is already transactional, append-only and idempotent by key.
- DQ1/ADR-012 provides a stateful *observability* sidecar (`MarketDataQualityMonitor`). It does
  not check bid/ask validity, spread, non-finite values, instrument identity or snapshot
  completeness, and it defaults to wall-clock `datetime.now(UTC)` (not replay-deterministic).

## Decisions

### D1. Separate additive packages; no competing engines
- `nexora.decision_audit` (schema, builder, store, fail-closed gate) and
  `nexora.data_quality` (pure validator) are new, pure-domain packages with no UI/API/DB
  imports. DQ1 files, `SignalEngine`, `ResearchPipeline` and `execution/**` are **not modified**.
- The Guard reuses the `QualityStatus` vocabulary and does not replace the DQ1 monitor;
  DQ1 stays the live observability sidecar, the Guard is the per-decision verdict.

### D2. Data Quality Guard verdict
- Input: a market-data snapshot (one or more `NormalizedPriceEvent` plus declared expected
  instrument/source identity) and an explicit `evaluated_at` supplied by the caller.
  The Guard never reads a wall clock, so historical replay and live paths share one code path.
- Output: `QualityVerdict(state, findings, config_version)` where `state` is one of
  `ok | blocked | unknown`; each `QualityFinding` has a stable `code`, severity, and the raw
  evidence values it was derived from. `blocked` means the data is proven invalid (duplicate,
  out-of-order, stale, crossed book, non-finite, identity mismatch, gap, clock anomaly);
  `unknown` means validity cannot be established (missing data, incomplete snapshot, missing
  bid/ask, unsynchronized clock, guard error). Nothing is repaired or interpolated. A
  separate "degraded" state is intentionally not defined: it would add no behavior, because
  every non-`ok` state already means NO NEW TRADE.
- `new_trade_permitted` is `True` **only** for `ok`. `blocked`, `unknown` and any internal
  error all yield NO NEW TRADE (fail closed).
- If a snapshot contains a value canonical hashing rejects (for example NaN), the finding is
  still reported by its real cause (`non_finite_value`) and `snapshot_hash` is `None`.
- Thresholds live in `QualityGuardConfig` (explicit, versioned, no broker defaults baked in
  beyond conservative generic values); instrument/broker-specific values are supplied by
  adapter capability data.

### D3. Decision Audit record
- `DecisionAuditRecord` (schema_version 1, frozen, canonical-serializable) carries: `decision_id`,
  `correlation_id`, `decided_at` (UTC), market-data reference + source, instrument identity,
  P&F/structure/pattern/trendline/regime evidence references, rule/engine/strategy/config
  versions, action (BUY/SELL/WAIT), reasons, blockers, confirmation requirements, data-quality
  verdict, optional risk/authority outcome, optional lifecycle/execution reference.
- `decision_id` is a deterministic content hash of stable decision inputs (stream, symbol,
  decision sequence, source event identity key, config/engine versions). It is an **audit**
  identifier and is never used as, or substituted for, a `signal_id`. `signal_id` is copied
  from the `ResearchSignal` when one exists and is `null` otherwise (including every WAIT).
- `correlation_id` is the source event `identity_key` chain so replays correlate stably.
- Evidence is referenced/copied only from engine outputs. Missing evidence is recorded as
  explicitly absent with a reason; the builder never fabricates evidence or IDs.
- A redaction filter rejects records containing credential-like keys/values.

### D4. Mandatory, append-only, fail-closed
- No feature flag, environment variable or config field disables auditing; there is no
  "disabled" code path in the gate.
- Storage reuses `Journal.append` on a dedicated stream (`audit:v1:<env>:<symbol>`); an
  existing key with a different content hash is a hard conflict, never an overwrite.
- `DecisionAuditGate.authorize(...)` returns a permit **only after** the audit append succeeded
  and read-back hash matched. Any persistence failure, conflict or exception yields
  `AuditOutcome.DENIED_AUDIT_FAILURE` (NO NEW TRADE). A WAIT is audited like BUY/SELL; an audit
  failure on WAIT is reported but cannot open a trade.

### D5. Integration contract (contracts only in V1)
```
Market Data -> Data Quality Guard -> existing Analysis/SignalEngine
            -> Decision Audit Trail -> existing Risk/Authority/Execution gates
```
The gate is a pure, side-effect-isolated adapter that consumes existing pipeline output.
V1 does **not** rewire `ResearchRuntime.ingest`, `ResearchPipeline`, paper trading or any
execution code. End-to-end enforcement is **not claimed**; wiring is a follow-up (below).

## Non-goals / hard boundaries
No change to BUY/SELL/WAIT scoring, thresholds or Pattern Engine feature flags. No order
submission, no REAL/DEMO/PAPER-unlock/AUTO, no PROD access, no Web->MT5 path, no AI override of
Risk/Execution. `ExecutionPreflight` stays DENY-ONLY.

## Open items / blockers (documented, not guessed)
1. **Runtime wiring** touches `research/runtime.py` (shared with recovery/checkpoint, ADR-022/029)
   and checkpoint equivalence; needs Architect review before changing ingest semantics.
2. **Risk/authority outcome field** is populated only if a stable ADR-015/ADR-035 outcome
   contract exists at the call site; V1 carries it as an optional opaque reference.
3. **Edge Validation (#77)** may reuse audit records for backtest provenance; coordinate before
   any shared-file change. No #77 files are touched here.
4. Under non-`ok` quality the engine's decision is still produced and audited unchanged;
   the gate only marks BUY/SELL as `denied_data_quality` (WAIT is always audited and opens
   nothing). Whether the SignalEngine itself should be skipped on bad data is a trading-semantics
   question reserved for Quant/Rin (AGENTS.md section 9); V1 does not decide it.
5. The store relies on the `Journal.append` contract (transactional, hash-verified, raises on
   identity conflict) and does no separate read-back; a journal that reports success without
   persisting is outside V1's threat model and is a Security-review item.
6. Eligibility is not authorization: `new_trade_eligible=True` only means audit and data
   quality did not deny. Risk, Authority and Execution gates keep full, independent authority.
