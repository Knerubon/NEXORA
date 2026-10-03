# ADR-034 — Execution Contract Freeze V1

Status: PROPOSED — Rin review required; CONTRACT FREEZE V1 NOT DECLARED until Rin approves
Date: 2026-10-03
Owner: Architecture Developer (Claude Code), acting as ARCHITECT/INTEGRATOR per AGENTS.md section 1
Task: Execution Contract Freeze V1
Sources: [AGENTS.md](../../AGENTS.md), [ADR-033](ADR-033-autonomous-trading-contracts-v1.md),
[ADR-025](ADR-025-mt5-instrument-resolution-v1.md), [ADR-016](ADR-016-paper-trading-simulator-boundary.md),
[MANUAL-EXEC-1](../../tasks/MANUAL-EXEC-1-manual-execution-ui.md)

## 0. Scope and discipline

This document freezes **contract shapes and boundaries** only, for the pipeline that will
eventually sit between Risk/Authority and a real broker:

```text
Manual UI / Future AUTO -> TradeIntent -> Risk -> Authority -> Execution Guard ->
  Execution Request -> Broker Adapter -> Broker Result -> Reconciliation ->
  Position Supervisor / Journal
```

It does **not** implement MT5 `order_send`, real or demo broker execution, Web→broker direct
calls, AUTO execution, a network execution adapter, or any PROD change. AUTO remains
unavailable; broker execution remains disabled; the existing Manual Execution UI
([MANUAL-EXEC-1](../../tasks/MANUAL-EXEC-1-manual-execution-ui.md)) remains execution-locked —
nothing in this ADR changes that panel's code or its lock. No PROD contact, no merge to `main`,
no tag/release (AGENTS.md sections 0, 9, 10).

**Reuse, not a parallel V2.** Every section below extends an existing frozen contract
(`autonomous_contracts.py`, `packages/nexora/autonomous/*`) additively — no existing field,
method signature, or test is changed in a way that breaks it (see section 10, compatibility).
New shapes live in a new sibling package, `packages/nexora/execution/`, mirroring how
`BrokerCapabilities` (ADR-033 section 14) was added as a sibling to
`InstrumentDefinition`/`FeedBinding` rather than merged into them.

## 1. Frozen ManualOrigin contract

**Problem (confirmed by inspection, matching
[MANUAL-EXEC-1](../../tasks/MANUAL-EXEC-1-manual-execution-ui.md)'s finding on `main`):**
`TradeIntent.origin` was `EntryOrigin | PositionOrigin`. `EntryOrigin` requires
`signal_decision_ref`/`entry_readiness_ref`; a manual order has neither. Faking either value
would violate AGENTS.md section 9 ("do not invent trading semantics") and ADR-033 section 10's
"never a synthetic ResearchSignal" rule, applied symmetrically.

**Frozen shape** (`packages/nexora/autonomous_contracts.py`):

```text
ManualOrigin {
  operator_ref: str                 # attribution — who/what issued the command
  manual_request_id: str            # stable identity: dedup/idempotency/journal/replay anchor
  requested_at: datetime (aware)
  position_id: str | None = None    # None for OPEN; required for REDUCE/CLOSE/MODIFY_PROTECTION
  ai_analysis_ref: str | None = None  # same advisory-only role as EntryOrigin.ai_analysis_ref
}
```

`TradeIntent.origin` widens to `EntryOrigin | PositionOrigin | ManualOrigin`.
`TradeIntent.__post_init__` is extended, additively, with:

- `kind == OPEN` now accepts `EntryOrigin` (unchanged) **or** `ManualOrigin` with
  `position_id is None`. `ManualOrigin` with a `position_id` set is rejected for OPEN
  (`manual_open_origin_must_not_reference_position`) — a manual BUY/SELL never references an
  existing position.
- risk-reducing kinds (`REDUCE`/`CLOSE`/`MODIFY_PROTECTION`) now accept `PositionOrigin`
  (unchanged) **or** `ManualOrigin` with `position_id` set. `ManualOrigin` without a
  `position_id` is rejected (`manual_risk_reducing_origin_requires_position_id`).
- Every existing `EntryOrigin`/`PositionOrigin` check and error message is byte-identical to
  before this ADR (see section 10).

`ManualOrigin` carries no `signal_decision_ref`, `entry_readiness_ref`, `exit_decision_ref`, or
`signal_id` field, structurally, so no consumer can mistake it for algorithmic evidence
(tested in `tests/test_execution_contracts_v1.py`).

**Open question, deliberately NOT resolved here (flagged for Quant/Architect):** how a manual
OPEN's `RiskDecision` is produced. The existing entry Risk path
(`RiskProposal{signal: ResearchSignal, price} -> RiskEngine.evaluate() -> RiskDecision`) requires
a `ResearchSignal`, which a manual order does not have — mirroring the identical, already-open
gap between `EntryOrigin`-based `TradeIntent` and `RiskProposal` under ADR-033 (contract-shape
only, never wired). Section 2 of this ADR keeps OPEN entirely on the existing entry Risk path, so
this gap is not closed by this ADR; `authorize_trade_intent`/`NewTradeAuthority.evaluate_manual`
(section 3) still *require* an already-produced `RiskDecision` for a manual OPEN — Risk Guard is
never bypassed — but how that `RiskDecision` is itself constructed for a signal-less manual order
remains open. **Do not implement a path around this without an explicit Architect/Quant
decision** (AGENTS.md section 9).

Status: **FROZEN** (shape + `TradeIntent` discrimination rule). Manual-OPEN RiskDecision
provenance: **OPEN QUESTION**, not resolved.

## 2. Frozen reducing-risk contract — RiskReductionProposal / RiskReductionDecision

`RiskReductionProposal` already existed (`packages/nexora/autonomous/risk_migration.py`, ADR-033
section 12) and is unchanged by this ADR. This ADR adds its missing counterpart:

```text
RiskReductionDecision {
  decision_id, proposal_id, position_id: str
  action: TradeIntentKind                 # restricted to REDUCE | CLOSE | MODIFY_PROTECTION
  allowed: bool
  reason_codes: tuple[str, ...]
  resulting_quantity: Decimal | None      # required when allowed and action in {REDUCE, CLOSE}
  resulting_protection: Decimal | None    # required when allowed and action is MODIFY_PROTECTION
  policy_version: str = ""
  effective_time: datetime | None = None
  source_refs: tuple[str, ...] = ()
}
```

**Deliberately a new, distinct type — never `RiskDecision`, never widened.**
`RiskDecision.signal_id` (`packages/nexora/risk/models.py`) is a required, non-optional `str`
field; ADR-033 section 15 flagged that a future `evaluate_reduction()` must not satisfy it with a
fabricated `signal_id`. This ADR resolves that flagged blocker by freezing
`RiskReductionDecision` as a sibling output type with no `signal_id` field at all — there is
nothing to fake. `RiskProposal`, `RiskDecision`, and `RiskEngine.evaluate()` keep their exact
current shape and behavior (verified unchanged by the full test suite, section 9).

**OPEN stays on the existing entry Risk path, unchanged.** This section covers only
REDUCE/CLOSE/MODIFY_PROTECTION. `RiskEngine.evaluate_reduction()` itself is **not** implemented
here — this freezes the proposal/decision shapes it would consume/return. Quant sign-off on how
`RiskPolicy.max_total_exposure`/`max_drawdown` apply to a reduction (ADR-033 section 12,
carried forward) remains open and is not decided by this ADR.

Status: **FROZEN** (shape only). `evaluate_reduction()` implementation: **BLOCKED** on Quant
sign-off (carried forward from ADR-033 section 12, unchanged).

## 3. Frozen AuthorityDecision structural split

**Problem (the "known Autonomous Core Phase 1 follow-up"):** `AuthorityDecision` had only
`allowed: bool` and `reason_codes: tuple[str, ...]`. `ExistingPositionAuthority`'s
`broker_unhealthy_fail_closed` denial and a genuine policy veto (e.g. `entry_not_ready`) were
both just "denied with some reason codes" — a consumer could only tell them apart by parsing
reason-code strings, which AGENTS.md's evidence discipline and this ADR's instruction both
prohibit.

**Frozen shape** (`packages/nexora/autonomous/authority.py`), additive:

```text
AuthorityPolicyStatus = AUTHORIZED | DENIED
ExecutionTransmissibility = TRANSMITTABLE | NOT_TRANSMITTABLE | NOT_APPLICABLE

AuthorityDecision {
  allowed: bool                                   # unchanged field, unchanged semantics
  reason_codes: tuple[str, ...]                    # unchanged field, unchanged semantics
  policy_status: AuthorityPolicyStatus             # NEW, derived, init=False
  transmissibility: ExecutionTransmissibility       # NEW, derived, init=False
}
```

`policy_status`/`transmissibility` are derived **once**, at construction time, from
`(allowed, reason_codes)` against one fixed, named set of transmission-blocking reason codes
(today: exactly `{"broker_unhealthy_fail_closed"}`). This is the one place a reason code is
matched against a classification; every other consumer reads the two typed fields, never parses
`reason_codes` itself:

| allowed | reason codes contain a transmission-blocking code | policy_status | transmissibility |
|---|---|---|---|
| `True` | (n/a — no codes allowed when `allowed=True`) | `AUTHORIZED` | `TRANSMITTABLE` |
| `False` | yes | `AUTHORIZED` | `NOT_TRANSMITTABLE` |
| `False` | no | `DENIED` | `NOT_APPLICABLE` |

**`NewTradeAuthority.evaluate()` is unchanged** — every degraded `SystemHealthGate` axis
(including `broker`) still produces a plain **policy** denial for OPEN
(`health_degraded:<axis>`), per ADR-033 section 11's invariant that a degraded state always
blocks new exposure outright, never merely "can't transmit it right now". This is intentional:
OPEN has no "authorized in principle" concept under degradation.

**`ExistingPositionAuthority.evaluate()`'s broker-unhealthy branch is reclassified, not
behaviorally changed:** `.allowed` is still `False` for every existing test (byte-identical), but
it is now structurally `AUTHORIZED` + `NOT_TRANSMITTABLE` rather than an undifferentiated denial —
the action (e.g. CLOSE) is policy-legitimate (risk-reducing), it simply cannot be verified over a
broken broker channel (ADR-033 section 11's fail-closed rationale, unchanged).

**Backward compatibility:** the constructor signature, `.allowed`, equality, and every existing
raise/message are unchanged, so `tests/test_autonomous_core_phase1.py` and
`tests/test_autonomous_contracts.py` pass byte-for-byte without modification (verified, section
9). No existing consumer that only reads `.allowed`/`.reason_codes` is affected.

**New:** `NewTradeAuthority.evaluate_manual()` — a separate method (not a modification of
`evaluate()`) that authorizes manual OPEN without an `EntryReadinessState` input, since
`ManualOrigin` never claims one (section 1). It still requires `TradingConfig` and an
already-produced `RiskDecision` — Risk Guard is never bypassed for manual orders. `
authorize_trade_intent()` dispatches to it when `intent.origin` is `ManualOrigin` and
`intent.kind is OPEN`; `ManualOrigin`-based risk-reducing intents dispatch to the existing,
unmodified `ExistingPositionAuthority.evaluate()` exactly like `PositionOrigin` does today (that
method never inspected origin type to begin with).

Status: **FROZEN**. No existing safety behavior weakened (verified by the unmodified existing
test suite passing unchanged, section 9).

## 4. Frozen ExecutionRequest contract

`packages/nexora/execution/models.py`:

```text
ExecutionRequest {
  request_id: str                       # stable request identity
  idempotency_key: str                  # see section 6 — prefer idempotency.build_execution_request()
  intent_proposal_id: str               # TradeIntent.proposal_id audit reference
  origin_ref: str                       # opaque audit reference (entry_readiness_ref /
                                         #   exit_decision_ref / manual_request_id — whichever
                                         #   the originating TradeIntent.origin carries)
  instrument_id: str                    # ADR-025 canonical instrument_id — never a broker symbol
  side: "long" | "short"
  action: TradeIntentKind                # OPEN | REDUCE | CLOSE | MODIFY_PROTECTION, reused
  quantity: Decimal | None               # required>0 for OPEN/REDUCE; optional for CLOSE; absent for MODIFY_PROTECTION
  price_constraint: PriceConstraint | None   # optional limit_price/max_slippage, broker-agnostic
  protection: ProtectionRequest | None       # stop_price/target_prices, broker-agnostic; required for MODIFY_PROTECTION
  position_ref: str | None               # required for REDUCE/CLOSE/MODIFY_PROTECTION; forbidden for OPEN
  created_at: datetime (aware)
}
```

No MT5-specific object, no broker symbol/suffix, no digits/lot-step/filling-mode token, no
broker name anywhere on this type (tested explicitly — `test_execution_request_has_no_broker_
specific_fields`). Those live only in `BrokerCapabilities` (ADR-033 section 14) and are resolved
by a future `BrokerExecutionAdapter`, never carried on this generic request.

Status: **FROZEN** (shape only). No `BrokerExecutionAdapter` consumes it; no I/O.

## 5. Frozen ExecutionResult contract

```text
ExecutionStatus = ACCEPTED | REJECTED | PARTIALLY_FILLED | FILLED | UNKNOWN

ExecutionResult {
  result_id, request_ref: str
  status: ExecutionStatus
  requested_quantity: Decimal
  filled_quantity: Decimal = 0
  remaining_quantity: Decimal | None     # None only when status is UNKNOWN
  broker_order_ref, broker_deal_ref: str | None
  execution_price: Decimal | None
  reason_code: str | None                # normalized, broker-agnostic
  observed_at: datetime (aware)
}
```

**Critical invariant:** `status is UNKNOWN` means the outcome is genuinely unresolved — e.g. a
timeout or connection loss *after* transmission. `remaining_quantity` is left `None` rather than
guessed, and the type rejects asserting one. A timeout/connection loss must never be normalized
to `REJECTED`; that would silently claim the broker never received the order, which cannot be
known without reconciliation.

`is_safe_to_retry_without_reconciliation(result)` is `True` **only** for a clean, zero-fill
`REJECTED`. `UNKNOWN`, `ACCEPTED`, and `PARTIALLY_FILLED` are always `False` (section 9's "UNKNOWN
cannot be treated as a safe retry" invariant); `FILLED` is also `False` — retrying an already-
filled result would create a duplicate execution, not a retry.

Status: **FROZEN** (shape + the retry-safety helper). No adapter produces a real
`ExecutionResult` yet.

## 6. Frozen idempotency / duplicate-order-safety contract

`packages/nexora/execution/idempotency.py`:

- `trade_intent_identity(intent) -> str` — reuses `TradeIntent.proposal_id` verbatim (ADR-033);
  no new identity concept for the intent itself.
- `execution_request_idempotency_key(intent) -> str` — `f"exec:{intent.kind.value}:{intent.
  proposal_id}"`. Deterministic: the same logical intent (same `kind`, same `proposal_id`)
  always derives the same key, whether the caller is a retry, a reconnect, an API-process
  restart, or an operator re-clicking the Manual UI. A `REDUCE` and a `CLOSE` against the same
  `proposal_id` deliberately get different keys — they are different logical executions, not
  duplicates of each other.
- `build_execution_request(intent, ...) -> ExecutionRequest` — the preferred constructor; it
  derives `idempotency_key` and `origin_ref` from `intent` itself so they can never desync from
  the `TradeIntent` that produced them.

**Durable persistence of the dedup store is explicitly out of scope** — this freezes the key a
future durable store would use; it does not implement that store. "Don't rely on process memory
only" (the task instruction) is satisfied by making the key a pure function of `TradeIntent`
identity, so any future persistence layer (DB row, journal entry, whatever) can key on it
directly without inventing its own derivation.

`build_manual_close_all_intents` (section 8) reuses this pattern per position, so a retried
CLOSE ALL batch re-derives the identical key per position rather than colliding across positions.

Status: **FROZEN** (identity rule + key format). Durable dedup storage: **not implemented**,
explicitly deferred.

## 7. Frozen reconciliation contract

`packages/nexora/execution/reconciliation.py`:

```text
ReconciliationStatus = SYNCHRONIZED | UNSYNCHRONIZED | UNKNOWN

ReconciliationFinding = MATCH
                       | LOCAL_OPEN_BROKER_MISSING
                       | BROKER_POSITION_LOCAL_MISSING
                       | QUANTITY_MISMATCH
                       | PROTECTION_MISMATCH
                       | EXECUTION_RESULT_UNKNOWN
                       | RESTART_RECOVERY_PENDING

ReconciliationRecord {
  position_ref: str | None
  finding: ReconciliationFinding
  local_quantity, broker_quantity: Decimal | None
  details_ref: str | None             # opaque reference — no inline broker payload
  observed_at: datetime (aware)
  status: ReconciliationStatus         # derived from finding, never independently settable
}
```

`status` is computed from `finding` by a fixed table (`MATCH -> SYNCHRONIZED`;
mismatch/missing findings `-> UNSYNCHRONIZED`; `EXECUTION_RESULT_UNKNOWN`/
`RESTART_RECOVERY_PENDING -> UNKNOWN`), so a caller can never claim `SYNCHRONIZED` while
reporting a mismatch. This mirrors `SystemHealthGate`'s existing `reconciliation` health axis
(ADR-033 section 18) at the record level.

**Critical invariant:** `blocks_new_trade(status)` is `True` for anything other than
`SYNCHRONIZED`. `UNSYNCHRONIZED` or `UNKNOWN` => **NO NEW TRADE** until resolved. This module
never mutates broker or local state — it classifies only; resolution/remediation remain future
work.

Status: **FROZEN** (shape + the blocking invariant). No real reconciliation algorithm against a
live broker exists; this is classification only.

## 8. Frozen manual CLOSE ALL ownership boundary

`packages/nexora/execution/close_all.py`. "CLOSE ALL" means close every NEXORA-owned/authorized
position — **never** every position in the broker account.

- `owned_open_positions(positions, *, symbol=None)` — the *only* source of ownership: filters the
  durable `PositionRecord` collection explicitly passed in to `state in {OPEN, MANAGING}`
  (optionally scoped to one symbol). It makes no broker call; a position that was never opened
  by NEXORA, or that NEXORA has already dropped from its own records, can never appear — there
  is no implicit universe of positions beyond the collection given to it.
- `build_manual_close_all_intents(positions, *, operator_ref, manual_request_id_prefix,
  requested_at, symbol=None)` — builds one `CLOSE` `TradeIntent` per owned position, each with
  its own `ManualOrigin` scoped to exactly that `position_id`, and a `manual_request_id`/
  `proposal_id` derived from `prefix:position_id` so a retried batch re-derives the identical
  idempotency key per position (section 6) rather than merging or colliding across positions.

This function does not decide whether each resulting intent is ultimately authorized or
transmitted — `ExistingPositionAuthority`/the future Execution Guard still gate every one
individually, exactly as they would for any other CLOSE.

Status: **FROZEN** (ownership rule + intent-building helper). Not wired to any real position
store, UI, or broker.

## 9. Execution safety invariants

Restated explicitly, each with where it is enforced and tested
(`tests/test_execution_contracts_v1.py` unless noted):

| Invariant | Enforced by |
|---|---|
| AUTO remains unavailable | `TradingMode.AUTO` denied by both `NewTradeAuthority.evaluate()` and `.evaluate_manual()` (ADR-033 section 21, unchanged; `test_auto_mode_never_authorized_in_phase_1`) |
| Broker execution remains disabled | no `BrokerExecutionAdapter`/broker call exists anywhere in this ADR's code; `ExecutionRequest`/`ExecutionResult` are pure shapes |
| Manual UI remains locked | this ADR makes no change to `apps/web/app/manual-execution.tsx` or its `EXECUTION_LOCKED` constant (MANUAL-EXEC-1) |
| AI cannot bypass Risk/Authority | no function in the authority chain (including `evaluate_manual`) accepts an `AIAnalysis` parameter (`test_manual_open_authority_has_no_ai_analysis_parameter`, extending ADR-033 section 19's existing proof) |
| Risk cannot bypass Execution Guard | `ExecutionRequest` is never constructed directly from a `RiskDecision`/`RiskReductionDecision` by any code in this ADR — only from a `TradeIntent` via `build_execution_request`; wiring a real Execution Guard remains future work |
| OPEN requires entry `RiskDecision` (or, for manual OPEN, *an* allow `RiskDecision` — provenance open per section 1) | `NewTradeAuthority.evaluate()`/`.evaluate_manual()` both require `risk_decision.action == "allow"` |
| REDUCE/CLOSE/MODIFY_PROTECTION uses `RiskReductionDecision`, never a faked `RiskDecision` | section 2; `RiskReductionDecision` has no `signal_id` field (`test_risk_decision_and_risk_reduction_decision_are_distinct_types`) |
| Denied authority cannot create an `ExecutionRequest` | `AuthorityDecision.policy_status is DENIED` carries `transmissibility is NOT_APPLICABLE`, never `TRANSMITTABLE` (`test_denied_authority_decision_cannot_be_treated_as_transmittable`) |
| Non-transmittable authority cannot create a broker transmission | `transmissibility is NOT_TRANSMITTABLE` is structurally distinct from `TRANSMITTABLE` (section 3) |
| UNKNOWN execution outcome cannot be blindly retried | `is_safe_to_retry_without_reconciliation` is `False` for `UNKNOWN` (`test_unsafe_statuses_are_never_safe_to_retry`) |
| Duplicate intent cannot create a second logical execution | `execution_request_idempotency_key` is a deterministic function of `(kind, proposal_id)` (section 6) |
| UNSYNCHRONIZED/UNKNOWN reconciliation blocks new positions | `reconciliation_blocks_new_trade` (section 7) |
| CLOSE ALL only operates on NEXORA-owned positions | `owned_open_positions`/`build_manual_close_all_intents` take only an explicit `PositionRecord` collection, no broker call (section 8) |
| No synthetic `ResearchSignal`/fake `signal_id` | `ManualOrigin` has no such fields (section 1); `RiskReductionDecision` has no `signal_id` field (section 2) |

## 10. Compatibility / verification

- `autonomous_contracts.py`: `EntryOrigin`, `PositionOrigin`, `TradeIntentKind`, `TradeState`,
  `TRADE_STATE_TRANSITIONS` unchanged. `TradeIntent.origin`'s type widens additively; every
  existing `__post_init__` check and its exact error message is preserved.
- `packages/nexora/autonomous/authority.py`: `AuthorityDecision`'s constructor signature,
  `.allowed`, and equality are unchanged. `NewTradeAuthority.evaluate()` and
  `ExistingPositionAuthority.evaluate()` are unchanged in logic (only newly exposed via the two
  derived fields). `evaluate_manual()` is additive.
- `packages/nexora/autonomous/risk_migration.py`: `RiskReductionProposal` unchanged.
  `RiskReductionDecision` is additive.
- `packages/nexora/risk/models.py`, `packages/nexora/paper/*`, `packages/nexora/position/*`:
  **not modified** by this ADR.
- Verified: `tests/test_autonomous_contracts.py` (16 tests) and
  `tests/test_autonomous_core_phase1.py` (36 tests) pass unmodified against this branch —
  byte-identical files, byte-identical results — proving no existing behavior changed.
  `tests/test_position_supervisor.py` and `tests/test_risk.py` likewise pass unmodified.
  `tests/test_pattern_engine.py::test_pattern_core_has_no_decision_or_runtime_dependencies`
  passes, confirming this ADR's new `packages/nexora/execution/` package does not cross the
  pattern-engine isolation boundary (nothing in it imports pattern/signal/feature code).
- New: `tests/test_execution_contracts_v1.py` (60 tests) covering every item in section 9 plus
  the shapes in sections 1–8.
- `ruff check`/`ruff format --check` and `mypy` are clean on every new/changed file (see task
  handoff for exact commands and output).

## 11. Open questions (not decided by this ADR)

- **Manual-OPEN `RiskDecision` provenance** (section 1) — how a manual order without a
  `ResearchSignal` gets Risk-checked sizing. Mirrors the pre-existing, still-open
  `EntryOrigin`→`RiskProposal` wiring gap under ADR-033.
- **`RiskEngine.evaluate_reduction()` implementation and Quant limits** (section 2, carried
  forward from ADR-033 section 12) — not implemented; shape only.
- **Durable idempotency persistence** (section 6) — the key is frozen; the store is not built.
- **Real reconciliation algorithm against a live broker** (section 7) — classification contract
  only; no broker query exists.
- **BrokerExecutionAdapter / Execution Guard wiring** — remains entirely unbuilt and BLOCKED by
  governance (AGENTS.md section 0) until a dedicated governance ADR narrowly lifts it, per
  ADR-033 section 21 (unchanged, not revisited here).

EXECUTION CONTRACT FREEZE V1 READY FOR RIN REVIEW
