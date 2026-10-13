# ADR-035 — Execution Integration Safety Amendment

Status: PROPOSED — Rin review required. Only items marked **RESOLVED_BY_RIN** (section 12) are decided; nothing else is frozen until Rin approves
Date: 2026-10-03 (Rin decision delta D1-D8 applied 2026-10-04; see "Revision record")
Owner: Architecture Developer (Claude Code), acting as ARCHITECT/INTEGRATOR per AGENTS.md section 1 (author only; independent review of the original draft is recorded on PR #58; the 2026-10-04 Rin-decision delta is **self-reviewed by its author; independent delta review pending**)
Task: PR-0 — Execution Integration Safety Amendment (docs only)
Base: `origin/main` `3f7aeb12c17472bcd12e0547e01d082bf4a23277` (includes Wave 1: #53 close-all plan, #54 durable dedup store, #55 broker adapter interface + simulator, #56 execution guard, #57 reconciler)
Sources: [AGENTS.md](../../AGENTS.md), [ADR-034](ADR-034-execution-contracts-v1.md),
[ADR-033](ADR-033-autonomous-trading-contracts-v1.md), [ADR-025](ADR-025-mt5-instrument-resolution-v1.md),
[ADR-016](ADR-016-paper-trading-simulator-boundary.md),
[MANUAL-EXEC-1](../../tasks/MANUAL-EXEC-1-manual-execution-ui.md),
[EXEC-GUARD-1](../../tasks/EXEC-GUARD-1-execution-guard.md),
[IDEMPOTENCY-1](../../tasks/IDEMPOTENCY-1-durable-dedup-store.md),
[BROKER-EXEC-1](../../tasks/BROKER-EXEC-1-broker-adapter-interface.md),
[RECON-1](../../tasks/RECON-1-reconciliation-algorithm.md),
[CLOSE-ALL-OWNERSHIP-1](../../tasks/CLOSE-ALL-OWNERSHIP-1-close-all-plan.md)

Decisions recorded here are Rin's approved decisions (binding input to this draft). Anything not
determined by those decisions or by existing ADR/code text is marked **OPEN** (section 11) and has
**no policy chosen for it**. Where this ADR and ADR-025/033/034 conflict, only the sections listed in
section 2 are superseded or clarified; ADR-025/033/034 are **not edited** and remain the audit
trail (no in-place history rewrite).

**Terminology (binding for this document).** Wherever this ADR states what happens "until decided"
for an OPEN item, that behavior is **forced by already-frozen invariants and fail-closed
constraints** (for example "an unestablished safety fact denies"). It is **not** a selected policy,
not a default chosen by this ADR, and must not be read as the resolution of the OPEN item. An
unresolved policy question stays unresolved.

**Revision record (auditability).** Draft commits on PR #58, in order: `70695ff` (original
draft), `0cd8051` (review round 1 fixes), `85fb3d3` (review round 2 fixes), `352229e` (review
round 3 minors), `ad391b1` (the Rin decision delta of 2026-10-04, D1-D8, relayed to the author in the
Owner's instruction). A **final minor docs delta**, the commit that follows `ad391b1` on the same
branch, applies Rin decisions **M-1** and **M-2** and records Rin clarifications **R1** and **R2** as
RESOLVED_BY_RIN (summarized at the end of this record). The D1-D8 delta: (D1) approves the pipeline order
of section 3.1; (D2) makes PR-3 required before PR-4 and removes the section 8 / section 9
contradiction; (D3) approves that REDUCE requires `SYNCHRONIZED`; (D4) approves authority
re-evaluation after resolution for every action kind; (D5) adds OPEN-20 and states that
`operator_ref` is not authorization; (D6) repairs the section 12 table and the "until decided" /
"default" terminology; (D7) restates that Track E stays blocked; (D8) restates that nothing is
unlocked. OPEN-1 through OPEN-19 remain unresolved except where an approved decision narrows their
existing text, and each such narrowing is marked **RESOLVED_BY_RIN** in section 12.

The final minor delta (2026-10-04): (M-1) **OPEN-1 is not a pre-integration blocker and is not
required to be resolved before PR-3 can merge**; PR-3 may implement the freshness-validation
mechanism but must take the bound from approved policy/configuration, must not invent or hard-code a
numeric or temporary bound, and must fail closed when operational use needs a bound and none is
approved; OPEN-1 stays OPEN and stays a BEFORE-PAPER-DEMO gate (section 6, section 8, section 9, OPEN-1);
(M-2) quarantine tooling is separated from EMERGENCY recovery tooling in the CAN-DO-DURING-INTEGRATION
rows of section 8; (R1, **RESOLVED_BY_RIN**) PR-3 may be implemented and merged while OPEN-20 is
unresolved provided `recover_from_emergency` stays fail-closed at its authorization boundary and
cannot be operationally enabled; OPEN-20 blocks operational enablement, not code merge, and this does
not authorize inventing an authorization mechanism; (R2, **RESOLVED_BY_RIN**) OPEN-20 is a prerequisite
for both BEFORE-PAPER-DEMO and BEFORE-REAL-TRANSMISSION operational enablement; this adds a
prerequisite and unlocks neither gate. OPEN-20 itself remains OPEN. No other OPEN item, no
crash/retry semantics and none of D1-D8 changed.

## 0. Scope and discipline

ADR-034 froze execution contract *shapes*. Wave 1 then built five pure/simulated components
(`guard.py`, `dedup_store.py`, `broker_adapter.py`, `reconciler.py`, `close_all_plan.py`) in
isolation. Reading them together against ADR-033/034 shows that they do **not yet compose
safely**: the order in which they may run, the meaning of a claim that has no result, the handling
of `MODIFY_PROTECTION` under a kill switch, the identity conversion between local symbol,
`instrument_id` and broker symbol, and the quantity of a CLOSE are each underspecified, and a few
current behaviors are unsafe if wired as-is (section 1). This ADR freezes, on approval, the
**ordering, state and contract rules** a later integration must follow. It is an amendment, not a
new design.

This ADR does **not**:

- implement anything (no code, tests, wiring, exports, task-record edits; docs only);
- unlock demo or real broker transmission. Demo requires a **later dedicated governance ADR**
  after integration and review gates; real transmission stays **LOCKED**; no `order_send` or
  equivalent exists or is authorized; broker adapter scope remains **interface + simulator
  only** (AGENTS.md section 0; ADR-033 section 21; ADR-034 section 0);
- enable AUTO, change the Manual Execution UI lock, or touch PROD;
- touch Track E (RISK-MANUAL-OPEN-1, PR #51, **BLOCKED and independent**). See section 10.

Naming used below (all **proposed**, none exist yet): `ExecutionPipeline`
(`packages/nexora/execution/pipeline.py`), `ExecutionPreflight`
(`packages/nexora/execution/preflight.py`). `ExecutionPipeline` is defined by responsibility and
ordering only.

## 1. Baseline findings (verified in code at base SHA)

| # | Finding | Where |
|---|---|---|
| F1 | `ExecutionGuard` exposes no `ProtectionChange` input. `is_risk_reducing(kind)` is true for all of `MODIFY_PROTECTION`, so a protection **WIDEN** is not blocked by the kill switch and not blocked by reconciliation. | `execution/guard.py` (`evaluate_execution_guard`: kill switch and `reconciliation_blocks_new_trade` only inside `if not is_risk_reducing(intent.kind)`); `autonomous_contracts.RISK_REDUCING_KINDS` |
| F2 | The guard docstring and `test_risk_reducing_not_blocked_by_reconciliation_alone` pin "risk-reducing intents are not blocked by reconciliation alone". | `execution/guard.py` module docstring; `tests/test_execution_guard.py` |
| F3 | `ProtectionChange = Literal["TIGHTEN","WIDEN"]` exists and is consumed only by `ExistingPositionAuthority`, and only **under degradation** (`protection_change_unclassified`, `degraded_blocks_exposure_increase`). With a fully HEALTHY snapshot the authority allows any `MODIFY_PROTECTION`. | `autonomous/authority.py` |
| F4 | `ExecutionResult.requested_quantity` is a required `Decimal > 0`; `ExecutionResult` has no action field. `SimulatedBrokerAdapter.submit` raises `simulated_adapter_requires_quantity` for CLOSE-without-quantity and for `MODIFY_PROTECTION`. | `execution/models.py`; `execution/broker_adapter.py` |
| F5 | `ExecutionRequest.quantity` is optional for CLOSE (ADR-034 s4), but nothing resolves it. The guard passes through whatever `quantity` its caller supplies. `PositionSupervisor.apply_exit_decision(CLOSE)` sets local `quantity = 0` and state `EXIT_PENDING` **at decision time**, i.e. before any broker outcome. A CLOSE resolved from a post-decision `PositionRecord` would read quantity 0. | `execution/guard.py`; `position/supervisor.py`; `position/models.py` (`EXIT_PENDING`/`CLOSED` require quantity 0) |
| F6 | `dedup_store` releases a key only after a clean zero-fill `REJECTED`. A claim with no result stays claimed forever and the store cannot tell "claimed, never sent" from "claimed, sent, outcome lost". A result appearing after a release raises `DedupStoreCorruptError` on every later read of that key. | `execution/dedup_store.py` (`release_for_retry`, `_state`) |
| F7 | The reconciler **assumes** `PositionRecord.symbol == BrokerPositionSnapshot.instrument_id` (`candidate.symbol != broker.instrument_id` is an identity conflict). `PositionRecord.symbol` is a caller-supplied string. In legacy mode the broker symbol is the only identity (ADR-025 s1 item 3, s9.1), so every legacy position would classify as broker-only (fail-closed but unusable) — or, if a caller "fixes" it by comparing symbols loosely, ownership could be inferred from a symbol. | `execution/reconciler.py`; `position/models.py`; ADR-025 s1/s9 |
| F8 | `TRADE_STATE_TRANSITIONS[EMERGENCY] = frozenset()`; the code comment cites "terminal states have no outgoing transitions". `owned_open_positions` and the reconciler also treat EMERGENCY specially (excluded from CLOSE ALL; "expected on the broker", fail closed). `PositionRecord` places **no quantity invariant on EMERGENCY**. | `autonomous_contracts.py`; `execution/close_all.py`; `execution/reconciler.py`; `position/models.py` |
| F9 | The reconciliation vocabulary cannot express "close order pending at broker" or "broker confirmed flat". A local `EXIT_PENDING` position (quantity 0) that is still open at the broker classifies as `QUANTITY_MISMATCH`; a local-open position absent from the broker classifies as `LOCAL_OPEN_BROKER_MISSING`, which the reconciler also emits for a same-ref **identity conflict** (different instrument or side). `BrokerSnapshot` carries no completeness attestation and no pending-order information. `EXECUTION_RESULT_UNKNOWN` is emitted with `position_ref=None` (the result has no position attribution). | `execution/reconciler.py`; `execution/reconciliation.py` |
| F10 | `BrokerExecutionAdapter` has only `capabilities()` and `submit()`; there is no read-only broker-state query, so no component can *produce* the `BrokerSnapshot` the reconciler consumes. The simulated adapter's volume check lives **inside** the adapter and anchors the step grid at `volume_min` implicitly. | `execution/broker_adapter.py`; `autonomous/broker_capabilities.py` |
| F11 | There is no position-level in-flight exclusion: the idempotency key is per intent (`exec:<kind>:<proposal_id>`), so two *different* intents closing the same position can each claim and each transmit. | `execution/idempotency.py`; `execution/dedup_store.py` |

## 2. Supersedes / clarifies

Legend: **superseded** = the cited text/behavior no longer applies; **clarified** = still valid,
meaning made precise or extended additively; **unchanged** = deliberately retained.

### 2.1 ADR-025

| Section | Disposition | Reason |
|---|---|---|
| s1 item 3 (broker symbol is the only instrument identity) and s9.1 (legacy-mode promises) | **clarified** | Still true for market data and research. For **execution**, legacy mode needs an explicit operator-declared `ExecutionInstrumentBinding` (section 5); a legacy symbol is never accepted as canonical identity by execution. |
| s3 (`instrument_id` canonical, immutable once used by a stream), s5/s6 (exact binding, `feed_id`) | **unchanged** | Reused as the canonical identity authority for execution. |
| s4.3 ("No `symbol_select`, `market_book_*` or `order_*` call is introduced.") | **clarified** | The no-order rule is reaffirmed. |
| s15 row "Future Trade Journal" (records `instrument_id`, `feed_id`, `broker_symbol`) | **superseded in part** | An execution record carries `instrument_id`, `feed_id` (binding mode; it lives on `ResolvedFeed`) and the binding reference. The **broker symbol stays adapter-owned** and is not recorded by core, which departs from the ADR-025 row. |

### 2.2 ADR-033

| Section | Disposition | Reason |
|---|---|---|
| s8 formula `ExecutionGuard.allow (SystemHealthGate + dup/spread/slippage/margin)` | **superseded** | Split. `ExecutionGuard` = pure authority/mode/kill-switch/reconciliation-by-kind. Duplicate detection = durable idempotency claim (section 3 step 5; ADR-034 s6, #54). Spread/slippage/margin/session/capability = separate `ExecutionPreflight` (section 3.5). The authority conjunction otherwise stands. |
| s10 enforcement note ("never increases exposure" PROVISIONAL) | **clarified** | For `MODIFY_PROTECTION` the guard now requires an explicit `ProtectionChange` (section 3.4); WIDEN is risk-increasing for kill-switch purposes. The invariant stays PROVISIONAL for quantity-bearing kinds. |
| s11 (`ExistingPositionAuthority` allows REDUCE/CLOSE/TIGHTEN under non-broker degradation; "reconciliation after reconnect is explicit") | **clarified** | Authority-level allow is unchanged. Allowing is not transmitting: every position-bound action additionally passes the reconciliation gate (section 3.3), which is where the s11 requirement that reconciliation precede any new action is enforced (exact s11 text: "before `ExistingPositionAuthority` allows any new action on that position"). Broker `UNHEALTHY` fail-closed is unchanged. |
| s13 "checks in this order: SystemHealthGate, duplicate-order detection, spread, slippage, margin" and "Only after all pass does it emit an `ExecutionIntent`" | **superseded** | Replaced by the nine-step order in section 3. The `ExecutionIntent` wording was already superseded by ADR-034 s4 (`ExecutionRequest`); reaffirmed. "`ExecutionResult` (filled/rejected/error)" is superseded by ADR-034 s5 (there is no `error`; unresolved is `UNKNOWN`). "keyed on `TradeIntent.provenance.proposal_id`" is superseded by the ADR-034 s6 key. |
| s14 `BrokerCapabilities` | **clarified** + one additive field | Shape otherwise unchanged. Adds an optional volume-step anchor and places shared volume validation with `BrokerCapabilities` (section 4.5). "BLOCKED until a real provider" is unchanged. |
| s15 partial-close lifecycle clarification | **clarified** | Adds the position-mutation timing rule (section 3.11): no local quantity/state mutation is applied before a persisted `ExecutionResult`, and lifecycle application is driven by a new result-driven helper (`apply_execution_result`), not by an `ExitDecision`. |
| s17 "consumes `ExecutionIntent`, returns `ExecutionResult`" | **clarified** | Wording follows ADR-034. The adapter owns `instrument_id` <-> broker symbol (section 5). Interface-only/simulator-only scope unchanged. |
| s18 `SystemHealthGate` (`reconciliation` axis) | **clarified** | The `reconciliation` health axis must be derived from the same evidence set the reconciliation gate uses (INV-12). |
| s25 row "TradeLifecycle / TradeState — FROZEN (table adopted verbatim)"; code comment in `autonomous_contracts.py` "terminal states have no outgoing transitions" | **clarified** | ADR-033's body contains no EMERGENCY-specific sentence; the "terminal" wording is the comment on `TRADE_STATE_TRANSITIONS` and TASK 1A instruction E. The **table is not changed**: `TRADE_STATE_TRANSITIONS[EMERGENCY]` stays `frozenset()`. ADR-035 adds one guarded exit that does **not** go through the table (section 6). |
| s21 governance blockers | **unchanged** | Not lifted. |

### 2.3 ADR-034

| Section | Disposition | Reason |
|---|---|---|
| s1 `ManualOrigin` (`operator_ref` attribution) | **unchanged** | `operator_ref` is attribution/provenance (audit identity). In EMERGENCY recovery it is **not authorization** (a non-empty `operator_ref` alone never authorizes recovery, OPEN-20) and never selects a target state (section 6). Manual-OPEN `RiskDecision` provenance remains OPEN under Track E. |
| s3 `AuthorityDecision` split | **unchanged** | `NOT_TRANSMITTABLE` still blocks every transmission (section 3.4). |
| s4 `ExecutionRequest` | **clarified** + one additive field | `position_ref` == local `position_id`. Adds optional `new_position_ref` for OPEN. "CLOSE quantity optional" stays valid at type level; a pipeline-built request for CLOSE/REDUCE/OPEN always carries the resolved quantity (section 4.1). |
| s5 `ExecutionResult` (`requested_quantity: Decimal`) | **superseded** in part | `requested_quantity` becomes optional (None only for `MODIFY_PROTECTION`); adds `action` and `nexora_position_ref`; per-action invariants (section 4.3). `UNKNOWN` semantics and `is_safe_to_retry_without_reconciliation` are **unchanged**. |
| s6 idempotency | **clarified** | Key derivation **unchanged**. "Durable store not implemented" is superseded by #54. The store's claim-without-result gap is closed by the write-ahead marker (section 3.6). |
| s7 reconciliation ("`blocks_new_trade` ... blocks new trades") | **clarified** | `reconciliation_blocks_new_trade` itself is unchanged. ADR-035 adds that REDUCE/CLOSE/MODIFY_PROTECTION also require a `SYNCHRONIZED` reconciliation (section 3.3). Vocabulary extensions proposed in section 4.6. |
| s8 CLOSE ALL ownership | **clarified** | Ownership requires a round-trip/durable attribution (section 5.3); a symbol filter is on the execution-domain symbol (section 5.1); EMERGENCY positions stay excluded until recovered (section 6). |
| s9 invariant rows: "UNSYNCHRONIZED/UNKNOWN reconciliation blocks new positions", "Duplicate intent cannot create a second logical execution", "UNKNOWN execution outcome cannot be blindly retried", "CLOSE ALL only operates on NEXORA-owned positions", "Risk cannot bypass Execution Guard" | **clarified** | Extended and made testable in section 7; none is weakened. |
| s9 other rows (AUTO unavailable, broker execution disabled, Manual UI locked, AI cannot bypass, no synthetic `ResearchSignal`, denied/non-transmittable authority) | **unchanged** | Restated in section 7. |
| s11 open questions | **clarified** | *Durable idempotency persistence*: **built** (#54), with the gaps in 3.6/3.8/3.9 open. *Real reconciliation algorithm*: classifier **built** (#57), still classification-only, no broker query port (F10). *BrokerExecutionAdapter/Guard wiring*: interface + simulator (#55) and guard (#56) **built**, not wired, real transmission still blocked. *Manual-OPEN provenance* and *`evaluate_reduction()`*: **unchanged, still open**. |

## 3. Execution pipeline — frozen order

### 3.1 The order

`ExecutionPipeline` composes the components below in exactly this order. **This order is approved
by Rin (decision D1, 2026-10-04) and frozen.** It contains **no policy**:
every decision is delegated to the named component, and the pipeline only enforces the order and
the stop-on-first-denial rule. Any step that cannot establish safety (missing, stale, unreadable,
malformed, or contradictory input; any exception) denies and **nothing proceeds**. Only step 7
(`adapter.submit`) may become broker-reachable, and real transmission remains **LOCKED**.

```text
Inputs: TradeIntent, SystemHealthSnapshot (used by step 2b and by the INV-12 axis), TradingConfig,
kill switch, and the evidence inputs that authority consumes under ADR-033 s8/s11. Any
AuthorityDecision computed before step 2 is NOT an input: step 2b recomputes it for every action
kind (3.4, D4). Then:

 1  RECONCILIATION GATE      acquire verified evidence -> aggregate status          [read-only, no write]
 2  RESOLUTION               owned position, identity, quantity, ProtectionChange   [read-only, no write]
                             -> ResolvedExecution
 2b AUTHORITY RE-EVALUATION  authority re-evaluated from the facts resolved/derived in step 2,
                             including the derived ProtectionChange (3.4, D4)       [pure, no write]
 3  EXECUTION GUARD          pure authority / mode / kill switch / reconciliation   [pure, builds the request]
 4  EXECUTION PREFLIGHT      capability / volume / session / spread / slippage /    [read-only, no durable write]
                             margin on the exact request that would be sent
 5  IDEMPOTENCY CLAIM        durable claim(key)                                     [FIRST durable write]
 6  LAST LOOK + ATTEMPT      re-check freshness; durable write-ahead attempt marker [durable write]
    MARKER
 7  TRANSMISSION             adapter.submit(request)                                [only step that can reach a broker]
 8  RESULT PERSISTENCE       record_result; release only if clean zero-fill REJECTED
 9  POST-EXECUTION           apply the PERSISTED result to local state through the result-driven
                             lifecycle helper (3.11), then classify_reconciliation on fresh evidence;
    RECONCILIATION           to local position state; gate for the next intent
```

Relation to the sequence given in the approval brief (a)-(h): the brief's (a) is step 1, (b) step 3,
(c) step 5, (d) step 2, (e) step 4, (f) step 7, (g) step 8, (h) step 9. Two deliberate changes, each
justified in 3.2 and **approved by Rin (D1)**: **resolution (d) moves before the guard (b) and before the
claim (c)**, and a **last-look + write-ahead attempt marker** (step 6) is added between claim and
transmission. Step 2b (authority re-evaluation after resolution) is likewise part of the approved order.

### 3.2 Why each step sits where it does

1. **Reconciliation gate first.** It is the cheapest fail-closed check, it is read-only, and its
   output (a status plus an evidence reference) is an *input* to steps 2, 3 and 6. Nothing may be
   resolved against local state until that state is known to agree with the broker (decision 5:
   quantity mismatch => UNSYNCHRONIZED => block). Failing to obtain evidence yields `UNKNOWN`
   (never `SYNCHRONIZED`), which the later steps deny.
2. **Resolution before the guard.** The guard is pure and cannot look anything up, yet its
   inputs (`instrument_id`, `position_ref`, `quantity`, `protection`, and now `protection_change`)
   are *outputs* of resolution. If the guard ran first it would build an `ExecutionRequest` with
   `quantity=None` for CLOSE and something after the guard would have to patch it — letting a request
   change after the last pure gate approved it, which contradicts the guard's role as "the last gate before an
   `ExecutionRequest` may exist" (guard module docstring; ADR-033 s13: "the last deterministic gate before
   `BrokerExecutionAdapter`"). Resolution is
   read-only, so running it before the guard cannot burn a key or touch a broker. It requires a
   `SYNCHRONIZED` reconciliation for the position (3.3).
2b. **Authority re-evaluation right after resolution (every action kind; Rin D4).** Authority
   is a function of facts that only exist after resolution (the position, its identity and
   quantity, and for MODIFY_PROTECTION the derived `ProtectionChange`, which
   `ExistingPositionAuthority` consumes under degradation). So authority is computed **after** step 2,
   by the pipeline, from the inputs it consumes under ADR-033 s8/s11 together with every fact resolved
   or derived in step 2; an `AuthorityDecision` computed before step 2 is discarded and never
   reaches the guard (3.4). It precedes the guard because the guard consumes the resulting
   `AuthorityDecision` and must see exactly one. If a required fact is missing, `UNKNOWN`, or
   unclassified, authority is not evaluated and the pipeline denies (fail closed). It is pure, so it
   cannot burn a key. Whether any resolved fact beyond `ProtectionChange` must become an authority
   input is **not decided here**. Step numbering stays 1-9 with this sub-step 2b; the order is
   "1, 2, 2b, 3-9".
3. **Guard before any durable write.** The guard is pure, so a denial leaves no trace. Authority
   `NOT_TRANSMITTABLE` (broker unhealthy, ADR-033 s11), mode, assisted confirmation, kill switch,
   and reconciliation-by-kind are all decided here. The guard remains **pure and free of
   spread/slippage/margin/session/capability** (decision 3).
4. **Preflight before the claim.** Preflight reads capabilities and quotes (read-only). Placing it
   before the claim means a spread/margin/volume/session denial — a *pure denial* — never creates a
   durable claim, so it never burns an idempotency key and never needs an abort record. It needs
   the resolved quantity, which is why resolution precedes it. It is kept as close to
   transmission as possible and its verdict carries `evaluated_at`; step 6 enforces its age.
5. **Claim before anything can be transmitted.** `claim(key)` is the first durable write and the
   only duplicate arbiter (`FIRST_CLAIM` for exactly one caller, atomic via the journal's
   first-writer-wins). Nothing may be transmitted before the claim is durable because (a) two
   concurrent callers could both transmit, and (b) a crash after transmission but before a claim
   would leave **no** record that an order may exist, so a restart could send it again. A
   `DUPLICATE` claim stops the pipeline: it returns the stored state and **never transmits** — for an
   in-flight, UNKNOWN, ACCEPTED, PARTIAL or FILLED key alike.
6. **Last look + write-ahead attempt marker between claim and transmission.** Two reasons.
   (i) *Freshness*: the guard, preflight and reconciliation inputs were evaluated before the claim
   write; the kill switch, health, evidence or quote may have changed. Step 6 re-runs the guard (pure)
   with current inputs and checks that the request is byte-identical, and checks the ages of the
   reconciliation evidence and the preflight verdict against the bounds in OPEN-1. A failure here
   writes `abort` (3.6) and stops: the claim is provably never-transmitted (releasable only if OPEN-14 is approved).
   (ii) *Crash semantics*: the attempt marker is written durably **before** `submit` is called
   (3.6), which is the only way a later reader can distinguish "never sent" from "may have been sent".
   If the marker write fails for any reason, `submit` is **not** called.
   **Residual window (stated, not closed):** step 6 re-runs the guard with the **step-1 evidence**
   and checks only ages; it does not re-resolve the quantity; and nothing sits between the marker
   write and `submit`. A kill switch armed, a health change, or a broker change after the last look
   is therefore not seen, and the window is unbounded until the OPEN-1 bounds exist.
7. **Transmission.** The only step that can reach a broker. It is entered only with a durable
   claim and a durable attempt marker for the current generation. In Phase 1 the only adapter
   is the simulator (section 0).
8. **Result persistence.** The outcome is recorded against the claimed generation. Only a clean
   zero-fill `REJECTED` is releasable (`is_safe_to_retry_without_reconciliation`); everything else
   keeps the key claimed. The pipeline **never loops**: a release only makes a *new, explicit*
   submission possible; it never resubmits.
9. **Post-execution reconciliation last.** The next intent is gated by evidence taken *after* this
   one, and the result is applied to local position state only through this persisted result
   (3.11).

### 3.3 Reconciliation gate rules by action kind

Status used by the gate is `aggregate_reconciliation_status(records)` over the evidence set
(existing function; no records or a non-record item yields `UNKNOWN`). **Scope constraint (forced by
the fail-closed rule; not a selected policy):** until OPEN-4 is decided, only the **aggregate** over the
whole evidence set may be used; narrowing to a per-position subset is **not permitted**, because
nothing in the frozen contracts establishes that a subset is safe. (Operational cost of this
constraint: one unrelated mismatch blocks every position action. OPEN-4 itself remains unresolved.)

| Kind | Required reconciliation status | Source of the rule |
|---|---|---|
| OPEN | `SYNCHRONIZED` | ADR-034 s7 (unchanged) |
| REDUCE | `SYNCHRONIZED` (resolution compares the requested quantity to the resolved position quantity). A REDUCE is **not** permitted merely because it is risk-reducing when reconciliation is `UNSYNCHRONIZED`/`UNKNOWN`. | **Rin decision D3 (2026-10-04, RESOLVED_BY_RIN)**: extends the frozen CLOSE fail-closed principle to REDUCE |
| CLOSE | `SYNCHRONIZED`. **Local quantity != broker-observed quantity => `UNSYNCHRONIZED` => BLOCK.** No automatic cap, no automatic quantity resolution, no transmission. | Rin decision 5 |
| MODIFY_PROTECTION, TIGHTEN | `SYNCHRONIZED`; TIGHTEN additionally passes every other safety/ownership/broker-health gate | Rin decision 4 |
| MODIFY_PROTECTION, WIDEN | `SYNCHRONIZED`; additionally blocked while the kill switch is armed (3.4) | Rin decision 4 |
| MODIFY_PROTECTION, UNKNOWN/UNCLASSIFIED | blocked (fail closed) | Rin decision 4 |

Consequence: today's rule "risk-reducing intents are not blocked by reconciliation alone" (guard
docstring; `test_risk_reducing_not_blocked_by_reconciliation_alone`, F2) is **superseded**. A
risk-reducing intent is policy-legitimate (authority) but **not automatically transmissible**.
Whether a `PROTECTION_MISMATCH` may be repaired by a TIGHTEN (the gate as written blocks it) is
OPEN-12. The status is evaluated once at step 1 and passed to step 2 and the guard; the guard
re-checks it as a pure input (defense in depth), and the two must agree.

### 3.4 `ExecutionGuard` contract (changes required; implemented later — MUST-FIX)

The guard stays pure. Frozen additional inputs: `protection_change: ProtectionChange | None` and
`new_position_ref: str | None` (4.1). Frozen rules, evaluated in the existing fixed reason order
with the new codes appended where shown:

| Rule | Kind | Condition | Reason code (names frozen on approval) |
|---|---|---|---|
| G1 | any | mode `AUTO` / `SHADOW` / `ASSISTED` without confirmation (OPEN only) | unchanged: `auto_mode_not_governed`, `trading_mode_shadow_observes_only`, `assisted_confirmation_missing` |
| G2 | any | authority not `AUTHORIZED` / `NOT_TRANSMITTABLE` / not `TRANSMITTABLE` | unchanged: `authority_denied`, `authority_not_transmittable`, `authority_not_applicable` |
| G3 | OPEN | reconciliation != `SYNCHRONIZED`; kill switch armed | unchanged: `reconciliation_blocks_new_trade:<status>`, `kill_switch_armed` |
| G4 | REDUCE, CLOSE | reconciliation != `SYNCHRONIZED` | new: `reconciliation_blocks_position_action:<status>` |
| G5 | MODIFY_PROTECTION | reconciliation != `SYNCHRONIZED` | `reconciliation_blocks_position_action:<status>` |
| G6 | MODIFY_PROTECTION | `protection_change` is `None` (or not exactly `TIGHTEN`/`WIDEN`) | new: `protection_change_unclassified` |
| G7 | MODIFY_PROTECTION | `protection_change == WIDEN` and kill switch armed | new: `kill_switch_blocks_protection_widen` |
| G8 | REDUCE, CLOSE | kill switch armed | **no rule — unchanged**: the kill switch does not block REDUCE/CLOSE (guard docstring: blocking them would be a new trading semantic; Rin decision 4 addresses only MODIFY_PROTECTION) |

Notes. (1) G6 applies regardless of kill-switch/health state, unlike `ExistingPositionAuthority`
(F3), which requires classification only under degradation; the guard needs it unconditionally
because the kill switch is not a health axis. (2) TIGHTEN **passes the kill switch** (risk-reducing)
but still needs G1, G2, G5 and the ownership/identity checks of step 2. (3) `protection_change` is
**never caller-asserted**: step 2 derives it with a pure `classify_protection_change(position,
protection)` and a UI- or AI-supplied value is ignored/denied. **Authority is re-evaluated after
step 2, for every action kind** (step 2b; **Rin decision D4, RESOLVED_BY_RIN**): the pipeline calls
`authorize_trade_intent(...)` itself from the inputs authority consumes under ADR-033 s8/s11
together with the facts resolved or derived in step 2 (for MODIFY_PROTECTION, `protection_change=<derived
value>`), and any `AuthorityDecision` computed before step 2 (including one built with a
caller-supplied value) is **discarded and never passed to the guard**. Step 2 produces the
classification; step 2b consumes it to produce the only `AuthorityDecision` the guard sees, so the
same change can never be classified twice differently. **`UNKNOWN`, unclassified, or a missing
required fact => authority is not evaluated and the pipeline denies (fail closed).** This does not
decide the payload origin (OPEN-17) or whether further resolved facts become authority inputs. Classification is `TIGHTEN` only for
a `ProtectionRequest` that changes **only the stop** and moves it strictly in the favorable
direction under the same rule `PositionSupervisor._require_tightening_only` uses; every other shape
(target changes, stop removal, mixed) is `UNKNOWN` => fail closed until OPEN-10. (4) A WIDEN when the
kill switch is not armed and health is fully HEALTHY is still permitted by Authority and Guard
today; ADR-035 does not change that and does not decide whether it should be (OPEN-10; the
supervisor itself forbids widening, `protection_must_only_tighten`). (5) Duplicate detection stays
out of the guard (unchanged).

### 3.5 `ExecutionPreflight` (separate layer; contract only)

`ExecutionPreflight` is **not** part of `ExecutionGuard` (decision 3). Proposed shape:

```text
ExecutionPreflight.evaluate(request, capabilities: BrokerCapabilities, market_refs, *, now) -> PreflightDecision
PreflightDecision { allowed: bool, reason_codes: tuple[str, ...], request_ref: str,
                    evaluated_at: datetime (aware), capabilities_observed_at: datetime (aware) }
```

Structural checks (not trading policy; may be implemented immediately): the request's
`instrument_id` equals the capability binding's `instrument_id`; volume validation by the shared
validator (4.5) for quantity-bearing kinds; `stops_level`/`freeze_level` distance checks for
`MODIFY_PROTECTION`; capabilities health `HEALTHY` and fresh. Policy checks — session, spread
(`spread_policy_ref`), slippage (`price_constraint`/policy), margin (`margin_policy_ref`) — consume
**opaque policy references** whose numeric content is Quant-owned and **not decided here**
(ADR-033 s13/s14 status "BLOCKED until a real provider" is unchanged). For **risk-reducing** kinds,
whether the policy checks apply at all is **OPEN-11**; until decided, preflight returns
`preflight_policy_undecided` (deny) for risk-reducing policy checks rather than choosing a default.
A preflight denial happens before the claim and creates no durable state.

### 3.6 Claim, write-ahead attempt marker, abort (dedup store semantics)

Key derivation is **unchanged** (`exec:<kind>:<proposal_id>`, ADR-034 s6). Today the store knows
`claim#g`, `result#g|<id>`, `release#g`. The following events are **added** (a later PR; not
implemented here):

```text
Per generation g of one idempotency key, in one journal stream (existing mechanism):
  claim#g     existing   generation g claimed
  attempt#g   NEW        "transmission is about to start" — written durably BEFORE adapter.submit
                         payload: request digest (canonical hash of the exact request sent),
                                  resolved quantity | None, reconciliation_evidence_ref,
                                  preflight decision ref, written_at   (no broker payload)
  abort#g     NEW        "transmission was never started" — written only by the pipeline's own
                         pre-send abort, or by startup recovery of an orphaned claim
  result#g|id existing   an ExecutionResult observed in generation g
  reconciled#g NEW (PROVISIONAL name; mechanism OPEN-2)  operator/automated record that verified evidence resolved an
                         attempted-without-result or UNKNOWN generation (see 3.8; mechanism OPEN-2)
  release#g   existing   generation g released for retry
```

Frozen rules:

- **W1 (write-ahead).** `attempt#g` must be durably appended before `submit` is invoked. If the
  append raises, loses a race, or is unconfirmed, `submit` is not called.
- **W2 (mutual exclusion).** `attempt#g` and `abort#g` are appended with `expected_count` against
  the observed stream, so after `claim#g` exactly one of them can win. A stalled live pipeline that
  loses to a recovery `abort#g` fails its marker append and does not transmit. No timeout is
  needed to make this safe.
- **W3 (derived state).** A key's state is a pure function of its events:

| Stored events (current generation) | State | Meaning |
|---|---|---|
| none | `UNCLAIMED` | claimable |
| `claim` only | `CLAIMED_NOT_ATTEMPTED` | provably never transmitted (W1); an orphan or a pipeline between steps 5 and 6 |
| `claim` + `attempt`, no `result` | `ATTEMPTED_NO_RESULT` | **UNKNOWN-equivalent**: an order may exist |
| `result` with status `UNKNOWN`/`ACCEPTED`/`PARTIALLY_FILLED`/`FILLED` | `RESULT_UNSAFE` | never auto-released |
| `result` clean zero-fill `REJECTED` | `RESULT_CLEAN_REJECTED` | releasable |
| `claim` + `abort` | `ABORTED_NEVER_ATTEMPTED` | releasable only if OPEN-14 is approved; otherwise stays claimed |
| **integrity violation** in the stored events (e.g. `dedup_released_generation_not_safe`, `dedup_release_sequence_broken`, `dedup_event_malformed`) | `QUARANTINED` | deny everything on this key (3.9) |
| **I/O failure** (`dedup_store_unreadable`, `dedup_store_write_failed`) | not a state | plain deny for this attempt; nothing recorded as quarantined; retry the read later |

- **W4 (release set).** (`quarantine_resolved`, 3.9, is likewise a PROVISIONAL name; mechanism OPEN-3.) A new generation may be started only after `RESULT_CLEAN_REJECTED`
  (existing rule) **or** `ABORTED_NEVER_ATTEMPTED` (new, provable by W1/W2), or after a
  `reconciled#g` outcome that proves "not sent" (OPEN-2). `ATTEMPTED_NO_RESULT` and `RESULT_UNSAFE`
  are never released by any timeout. **W4(ii) is conditional on OPEN-14 and is not a pure
  clarification:** it widens the store's release set beyond the clean-REJECTED rule (ADR-034 s5's
  `is_safe_to_retry_without_reconciliation` helper stays unchanged; the store would need a second,
  separate release predicate). If Rin declines OPEN-14, `ABORTED_NEVER_ATTEMPTED` is not released:
  the key stays claimed and the operator issues a **new intent** with a new `proposal_id`; every
  statement below that a caller may resubmit "as generation g+1" after an abort then does not apply.
- **W5.** The dedup store must additionally support: the new events; a state accessor returning
  the W3 state; `abort` and `attempt` with `expected_count`; an explicit-generation late-result
  append (3.9); and an **enumeration accessor** listing the keys currently `ATTEMPTED_NO_RESULT` or
  whose latest result is `UNKNOWN` (the store has no list API today; required by the OPEN-16 forced
  rule and INV-12). Two current behaviors must change: (1) `_events` whitelists only
  `claim`/`result`/`release` and would raise `dedup_event_malformed` on `attempt`/`abort`/`reconciled`/
  `quarantine_resolved`, so the new names must be recognized; (2) `_state` raises
  `dedup_released_generation_not_safe` for any released generation whose latest result is not a clean
  REJECTED, so, **only if OPEN-14 is approved**, a generation released after `abort` must be accepted,
  and a generation with `quarantine_resolved` must stop raising on every read. In all cases
  late-result-after-release detection is preserved. Integrity violations are mapped by the pipeline to
  a denial and quarantine; I/O failures to a plain denial. Neither is ever read as "unseen".

**Crash after claim, before transmission** is therefore handled as: claim durable, no marker =>
`CLAIMED_NOT_ATTEMPTED`; **startup recovery** (store-level, deterministic, no broker evidence, no
timeout) appends `abort#g`. **If OPEN-14 is approved** the caller may then submit again, which
re-runs steps 1-5 from scratch (conditions, resolved quantity and preflight may differ) as
generation g+1; **if declined** the key stays claimed and a new intent is required. Either way a
retry under the same key additionally depends on OPEN-15 (adapter idempotency identity). If the process died after
the marker, the state is `ATTEMPTED_NO_RESULT` and the UNKNOWN rules apply.

### 3.7 Retry, new intent, duplicate

- **Duplicate** = same key, same unreleased generation: always stops at step 5, returns the stored
  state, never transmits. Concurrent callers: exactly one gets `FIRST_CLAIM`; the rest get
  `DUPLICATE`.
- **Legitimate retry** = the *same intent* (same key) after `RESULT_CLEAN_REJECTED` (settled) or,
  **only if OPEN-14 is approved**, `ABORTED_NEVER_ATTEMPTED`, submitted explicitly by the caller as
  generation g+1. **Adapter collision (OPEN-15):** `BrokerExecutionAdapter` must be idempotent per
  `request.idempotency_key` and `SimulatedBrokerAdapter` caches `_results[idempotency_key]`, so a
  same-key retry would receive the cached REJECTED and never retransmit. Until OPEN-15 is decided a
  same-key retry cannot be implemented and a new intent is required. It re-runs
  steps 1-9 in full; **resolved quantity is recomputed** (never reused from the prior generation) and
  recorded in the new `attempt#g+1`.
- **New intent** = a different `proposal_id` (hence a different key). Required for everything else,
  including: any remainder after a partial fill; any follow-up to an UNKNOWN/ACCEPTED/FILLED outcome;
  any retry the operator wants after a `RESULT_UNSAFE`. A new intent never inherits or bypasses the
  old key's state, and its resolution rules are unchanged. Position-level exclusion between two
  *different* intents on one position is OPEN-5 (F11).
- **CLOSE after `PARTIALLY_FILLED`.** The key `exec:CLOSE:<proposal_id>` is `RESULT_UNSAFE`; a repeat
  with the same `proposal_id` is a duplicate. The remainder is a **new CLOSE intent** (new
  `proposal_id`), resolved only after step 9 reconciliation reports `SYNCHRONIZED` against the
  post-fill quantity. Nothing infers the remainder from the old request. This depends on OPEN-7:
  how the local quantity is updated from a `PARTIALLY_FILLED` result is not decided, so the
  "post-fill quantity" cannot be reconciled until it is. **Stated consequence:** until OPEN-7 is decided a `PARTIALLY_FILLED` CLOSE leaves local quantity unchanged while the broker holds less, so reconciliation reports `QUANTITY_MISMATCH`/`UNSYNCHRONIZED`, which blocks the remainder CLOSE and every other action with the exposure left unmanaged.
- **Resolved quantity and the key.** The key does not include quantity. The quantity that is
  authoritative for a generation is the one written into that generation's `attempt#g` and into
  `ExecutionResult.requested_quantity`. A duplicate never re-resolves or substitutes a quantity.

### 3.8 `UNKNOWN` outcome and attempted-without-result

A timeout or connection loss after transmission (ADR-034 s5 and the `broker_adapter.py` docstring) yields `UNKNOWN`,
never `REJECTED`, never a blind retry. **Clarification of the adapter contract:** if the adapter
cannot *prove* the request was not transmitted, it must return `UNKNOWN`, not `REJECTED`; a clean
`REJECTED` requires broker-attested rejection or a proof of non-transmission. The same
rule applies to `ATTEMPTED_NO_RESULT`: it is `UNKNOWN`-equivalent.

- **Who resolves:** verified reconciliation evidence only — a fresh, complete broker observation
  attributed (by `nexora_position_ref` round-trip or durable adapter mapping, section 5.3) to the
  **same NEXORA position**, and, for non-position-visible effects, an adapter-attested lookup by
  request/idempotency reference. The shape of the read-only query port that produces this, and
  whether manual operator attestation is acceptable, is **OPEN-2** (no timeout-based clearing, no
  inference from symbol, no re-submission as a probe).
- **What the evidence may conclude:** `CONFIRMED_NOT_SENT` (releasable per W4), `CONFIRMED_EXECUTED`
  (attach the final `ExecutionResult`; stays claimed), `INCONCLUSIVE` (stays blocked). Anything the
  evidence cannot establish stays blocked.
- **Until OPEN-2 is decided there is no release path out of `UNKNOWN`**; the key stays blocked.
  That is intentional fail-closed behavior, not a gap to be papered over by a timeout.
- Feed to reconciliation: only a key in `ATTEMPTED_NO_RESULT`, or whose stored result has status
  `UNKNOWN`, is an unresolved outcome that must make the aggregate `UNKNOWN` (blocking new trades
  and, by 3.3, every position action). A determinate `ACCEPTED`/`PARTIALLY_FILLED`/`FILLED` result is
  **not** fed as `EXECUTION_RESULT_UNKNOWN` (`classify_reconciliation` emits it only for an
  `ExecutionResult` with status `UNKNOWN`; row 13 and the CLOSE-after-partial flow depend on this).
  `ATTEMPTED_NO_RESULT` has no `ExecutionResult` object, so how it is represented to the classifier
  (an explicit marker input, or a vocabulary addition in 4.6) is **OPEN-16**; until decided the
  gate cannot establish safety while such a key exists, so it treats any `ATTEMPTED_NO_RESULT` key
  as a blocking `UNKNOWN` at step 1 (forced by the fail-closed rule; not a chosen policy).

### 3.9 Late unsafe result after a release; quarantine

A result that becomes visible for generation g **after** `release#g` (journal order) is a broker or
adapter contract violation and may mean a retry (g+1) was sent while g was in fact live.
`_state` already raises `DedupStoreCorruptError("dedup_released_generation_not_safe")` for it.
Quarantine covers **integrity violations only**; transient I/O failures (`dedup_store_unreadable`,
`dedup_store_write_failed`) are plain denials (3.6, row 20) and are never recorded as quarantine.
Frozen handling:

- The key becomes `QUARANTINED`. The pipeline denies every intent that maps to it
  (`dedup_quarantined`). It is never read as "unseen" and never auto-cleared.
- The late result must not be dropped. The store needs an explicit-generation late-result append
  (W5) so the evidence is durable and visible; the late result is also fed to reconciliation
  (`EXECUTION_RESULT_UNKNOWN`-class finding) so position state is re-verified. The position-state
  consequence (e.g. EMERGENCY) is **OPEN-7**.
- **Clearing is manual/verified only, append-only, and never timeout-based.** A quarantine is
  cleared by appending a `quarantine_resolved` record that references (a) verified reconciliation
  evidence for the affected NEXORA position showing the broker state is fully explained, (b) the late
  result id, and (c) `operator_ref` as audit provenance. Existing events are never edited or
  deleted. `_state` must recognize `quarantine_resolved` (today it would raise on every read of such
  a stream; W5). Who may append it (authorization model, Security review) and the exact record format
  are **OPEN-3**. Absent that record the key stays quarantined.

### 3.10 Failure / crash points

State left behind, what is safe next, who/what resolves it. "Evidence" means verified reconciliation
evidence per 3.8. "Recovery" is the startup store recovery of 3.6.

| # | Failure or crash point | State left behind | Safe to do next | Resolved by |
|---|---|---|---|---|
| 1 | Any denial in steps 1-4 | nothing durable (no claim) | fix cause, resubmit the same intent | caller; pure denial, key not burned |
| 2 | Evidence unobtainable / stale / malformed at step 1 | none (status `UNKNOWN`) | block; obtain fresh evidence | evidence provider; bounds OPEN-1 |
| 3 | Quantity mismatch / `UNSYNCHRONIZED` at step 1-2 (decision 5) | none | block; **never cap or auto-resolve**; reconcile first | operator + reconciliation; repair procedure OPEN-4/OPEN-13 |
| 4 | Process dies at step 5 before claim is durable | none | resubmit | caller |
| 5 | Process dies after claim, before marker (`CLAIMED_NOT_ATTEMPTED`) | `claim#g` | recovery appends `abort#g`; resubmit as g+1 **only if OPEN-14 approved and OPEN-15 decided**, else new intent | store recovery (deterministic, no evidence needed) |
| 6 | Step 6 last-look fails (kill switch armed, evidence/preflight too old, guard now denies) | `claim#g` then `abort#g` | resubmit as g+1 after cause cleared **only if OPEN-14 approved and OPEN-15 decided**, else new intent | pipeline writes abort |
| 7 | Marker write fails or loses a race | `claim#g` (no marker) | **do not transmit**; same as row 5 (including its OPEN-14/15 conditions) | pipeline stops; recovery |
| 8 | Process dies after marker, before/inside `submit` (`ATTEMPTED_NO_RESULT`) | `claim#g`, `attempt#g` | **nothing**: no resubmit, no release, no new intent on the same position until resolved (**not enforced**; OPEN-5) | evidence (3.8; OPEN-2) |
| 8a | `adapter.submit` **raises** (e.g. `BrokerAdapterError`) after the marker | `claim#g`, `attempt#g`, no result | same as row 8: the key is `ATTEMPTED_NO_RESULT` (UNKNOWN-equivalent) **even if non-transmission is provable**; the pipeline does not downgrade it | evidence (3.8) |
| 9 | `submit` returns `UNKNOWN` (timeout after transmission) | `result#g` = UNKNOWN | same as row 8 | evidence (3.8) |
| 10 | `submit` returned but `record_result` fails/crashes | marker without result | treat as row 8; surface the in-memory result to the caller as UNKNOWN-pending; alert | evidence (3.8) |
| 11 | Adapter result inconsistent with the request (action, quantity, position ref differ) | persist as `UNKNOWN` (reason `result_inconsistent_with_request`), never as the claimed status | same as row 8 | evidence (3.8) |
| 12 | Clean zero-fill `REJECTED` | `result#g`, then `release#g` | explicit new submission (g+1) **once OPEN-15 is decided** (adapter idempotency would otherwise return the cached REJECTED); no automatic loop | caller |
| 13 | `ACCEPTED` / `PARTIALLY_FILLED` / `FILLED` | `RESULT_UNSAFE` | no same-key resubmit; remainder = new intent after step 9 `SYNCHRONIZED` | caller creates new intent |
| 14 | Concurrent claim | one `FIRST_CLAIM`, others `DUPLICATE` | duplicates return stored state | store |
| 15 | Result durable, process dies before it is applied to local position state | `result#g`, local state not yet advanced | restart sets `RESTART_RECOVERY_PENDING` (status `UNKNOWN`) until step 9 reconciles; **application must not be applied twice** (OPEN-19: `apply_execution_result` cannot itself guarantee idempotence) | step 9 on restart |
| 16 | Step 9 finds mismatch (`UNSYNCHRONIZED`) | status blocks next intents | none until reconciled | reconciliation/operator; remediation not designed (OPEN-4/13) |
| 17 | Broker unhealthy before transmission (authority `NOT_TRANSMITTABLE`) | none | block; restore connectivity, then step 1 | guard denial G2; no claim |
| 18 | Broker becomes unreachable during `submit` | adapter returns `UNKNOWN` unless non-transmission is proven | as row 9 | evidence (3.8) |
| 19 | Late unsafe result after release | `QUARANTINED` | deny the key; reconcile the position | manual + evidence (3.9; OPEN-3, OPEN-7) |
| 20 | Dedup store I/O failure (`dedup_store_unreadable`, `dedup_store_write_failed`) | none recorded; plain deny | block this attempt; retry later | operator/infrastructure; never treated as unseen. (An **integrity** violation is row 19 / quarantine.) |

### 3.11 Position-state mutation timing

`PositionSupervisor.apply_exit_decision(CLOSE)` is a pure function that returns a record with
quantity 0 and `EXIT_PENDING` (F5). Frozen: **its output is never applied to the durable position
store** (not before step 9 and not after: lifecycle application is result-driven, below). Quantity and state changes are consequences of a **persisted
`ExecutionResult`** (consistent with `mark_closed`'s "a future phase drives this from
`ExecutionResult`"). Step 2 resolves CLOSE/REDUCE quantity from a record in `OPEN`/`MANAGING` only
(`owned_open_positions` states); an `EXIT_PENDING` record has quantity 0 and cannot resolve a CLOSE.
Which local state represents "outcome UNKNOWN/ACCEPTED/PARTIAL while the close is unresolved" is
**OPEN-7**.

**Result-driven lifecycle application (step 9; new contract).** `apply_exit_decision` requires an
`ExitDecision` with non-empty evidence (and `reduce_quantity` for a partial close). A manual CLOSE,
CLOSE ALL or REDUCE carries only a `ManualOrigin`, so no `ExitDecision` exists, and the pipeline must
**never fabricate one** (same anti-pattern as a synthetic `ResearchSignal`; AGENTS.md s9, ADR-033
s10/s15). The pipeline therefore does **not** route through `ExitDecision` at all. Frozen instead is
a new pure helper (proposed name `apply_execution_result(position, request, result) -> PositionRecord`,
new module under `packages/nexora/position/`) that derives the lifecycle change from the **persisted
`ExecutionResult` alone**, reusing the supervisor's `_require_transition` and `mark_closed`
invariants and `PositionRecord`'s quantity/state invariants:

| Persisted result | Applied to the position (must be `OPEN`/`MANAGING`) |
|---|---|
| CLOSE, `FILLED` (filled == resolved quantity) | quantity 0 and `EXIT_PENDING`, then `CLOSED` (`mark_closed` requires quantity 0). Without this a local `EXIT_PENDING` (quantity 0) record would be reported `LOCAL_OPEN_BROKER_MISSING`/`UNSYNCHRONIZED` forever against a flat broker; a local `CLOSED` record against a flat broker is a `MATCH`. |
| REDUCE, `FILLED` | quantity reduced by the filled quantity, state stays `MANAGING` (the ADR-033 s15 partial-close rule; a reduction leaving zero is refused, as `apply_exit_decision` refuses it) |
| any action with `PARTIALLY_FILLED`, `ACCEPTED` or `UNKNOWN`; MODIFY_PROTECTION of any status | **nothing applied until OPEN-7 is decided** (OPEN-7 now also covers when/how local protection is updated for MODIFY_PROTECTION). **Consequence until OPEN-7 is decided:** a successful stop TIGHTEN leaves the broker stop new and the local stop old, which reads as `PROTECTION_MISMATCH` and blocks every action, with OPEN-12 giving no repair (a TIGHTEN is one-shot-and-lock); a partial close likewise leaves `QUANTITY_MISMATCH`. The system is globally blocked after either, so OPEN-7 belongs in the paper-demo gate (section 8) |

**Single path for algorithmic closes.** A close that **does** have an `ExitDecision` (a
`PositionOrigin` intent) uses the **same** result-driven application: `ExitDecision` stays the
evidence/origin of the intent (`exit_decision_ref`), and its pure `apply_exit_decision` output is
**not** applied to the durable store. Where both can be computed, the helper's output for a `FILLED`
CLOSE/REDUCE must equal what `apply_exit_decision` (+ `mark_closed`) would produce (equivalence test,
INV-29). INV-23 is preserved: local state is mutated only after a persisted result. Whether Rin
accepts this resolution is Decisions-for-Rin #7.

## 4. Frozen contract changes (on approval)

Changes are **additive in shape** (new fields default `None`) with **one type widening**:
`ExecutionResult.requested_quantity` becomes `Decimal | None`, so `dedup_store.deserialize_result`
(which requires a non-None quantity) and every consumer that reads it must change together with it.
Existing constructors otherwise keep working until each is migrated; invariants are enforced at the type level where
possible and by the pipeline where the shape cannot express them. No existing field or method
signature is removed.

### 4.1 `ExecutionRequest` (ADR-034 s4)

```text
ExecutionRequest  (existing fields unchanged) + {
  new_position_ref: str | None = None   # NEXORA position id pre-allocated for the position an OPEN will create
}
```

| Action | quantity | position_ref | new_position_ref | protection |
|---|---|---|---|---|
| OPEN | required > 0 | forbidden | required for transmission; type-level optional during migration | optional (initial protection) |
| REDUCE | required > 0, strictly less than the resolved position quantity | required (= local `position_id`) | forbidden | optional |
| CLOSE | required > 0 on any request that reaches an adapter; equals the resolved quantity; type level still allows `None` | required | forbidden | unchanged (not used; not newly forbidden) |
| MODIFY_PROTECTION | **absent** (never fabricated) | required | forbidden | required |

`position_ref` **is** the local `position_id` (ADR-034 left this implicit). For OPEN,
`new_position_ref` must be allocated **before** the claim, stable across retries of the same
intent, never reused, independent of any broker ticket or symbol, and a deterministic function
of intent identity so a restart re-derives it (exact format is a PR-1 detail; for illustration only,
`pos:<proposal_id>`). The adapter-facing attribution reference is `position_ref` (non-OPEN) or
`new_position_ref` (OPEN).

### 4.2 `ResolvedExecution` (new; output of step 2)

```text
ResolvedExecution {
  intent_proposal_id: str
  action: TradeIntentKind                    # reused; no parallel enum (there is no ExecutionKind)
  instrument_id: str                         # canonical (section 5)
  side: "long" | "short"
  nexora_position_ref: str                   # position_ref (non-OPEN) | new_position_ref (OPEN)
  resolved_quantity: Decimal | None          # >0 for OPEN/REDUCE/CLOSE; None for MODIFY_PROTECTION
  protection_change: ProtectionChange | None # MODIFY_PROTECTION only; derived by step 2, never caller-asserted
  reconciliation_evidence_ref: str           # identifies the evidence set (observed_at + details_ref)
  reconciliation_observed_at: datetime (aware)
  resolved_at: datetime (aware)
}
```

Invariants: CLOSE `resolved_quantity` equals local quantity **and** broker-observed quantity (both
from the same evidence set; mismatch => no `ResolvedExecution`); REDUCE `0 < requested < resolved
position quantity`; `protection_change` present iff action is MODIFY_PROTECTION; the position must be
`OPEN`/`MANAGING` and its instrument/side must equal the intent's. It is persisted (by digest and
reference) in `attempt#g`, never inferred later.

### 4.3 `ExecutionResult` (ADR-034 s5)

```text
ExecutionResult  (existing fields) with changes:
  requested_quantity: Decimal | None      # was Decimal (required > 0); None only for MODIFY_PROTECTION
  action: TradeIntentKind | None = None   # NEW; None = legacy result (legacy invariants)
  nexora_position_ref: str | None = None  # NEW; attribution for EXECUTION_RESULT_UNKNOWN
```

| Action | `requested_quantity` | `filled_quantity` / `remaining_quantity` | Allowed statuses |
|---|---|---|---|
| OPEN, REDUCE, CLOSE | required > 0 (CLOSE: the **resolved** quantity, preserved in the record) | existing invariants unchanged | all five |
| MODIFY_PROTECTION | **None** | filled 0; remaining `None` | `ACCEPTED`, `REJECTED`, `UNKNOWN` only; `FILLED`/`PARTIALLY_FILLED` invalid |
| `None` (legacy) | required > 0 | existing invariants unchanged | all five |

For MODIFY_PROTECTION, `ACCEPTED` means only that the broker acknowledged the modification;
whether it is applied is established by reconciliation (`PROTECTION_MISMATCH` vs `MATCH`). The
pipeline (step 7/8) rejects an adapter result whose `action` is `None`, or whose `action`,
`nexora_position_ref` or `requested_quantity` disagree with the request, by persisting it as
`UNKNOWN` (row 11 of 3.10), never as the claimed status.
`is_safe_to_retry_without_reconciliation` is unchanged.

### 4.4 Identity types

`ExecutionInstrumentBinding` and the resolver are defined in section 5.2.

### 4.5 `BrokerCapabilities` volume stepping (ADR-033 s14)

Core must not hard-code broker stepping; validation is driven by `BrokerCapabilities`.

```text
BrokerCapabilities  (existing fields) + {
  volume_step_anchor: Decimal | None = None   # None => the grid is anchored at volume_min
}
validate_volume(capabilities, quantity) -> reason_code | None     # shared, pure, lives with BrokerCapabilities
   valid iff  volume_min <= q <= volume_max  and  (q - anchor) % volume_step == 0,
   anchor = volume_step_anchor if declared else volume_min
```

The current check in `SimulatedBrokerAdapter._volume_violation` already anchors at `volume_min`;
the shared function replaces it (the adapter calls it as defense in depth; preflight calls it as the
gate). Normalized reason codes stay those already in `broker_adapter.py`
(`volume_below_min`, `volume_above_max`, `volume_not_multiple_of_step`). **A real capability provider
must declare the anchor explicitly before any real transmission** (readiness table, section 8).
Whether the step/min rules apply to a full-position CLOSE of a non-conforming residual is OPEN-8;
until decided there is no exemption, so such a CLOSE is blocked.

### 4.6 Reconciliation vocabulary (ADR-034 s7)

Current vocabulary **cannot express** "confirmed close pending" or "confirmed flat" (F9). Minimal
additive extension, **shape proposed and marked OPEN-6 (not guessed)**:

- a broker-agnostic attestation on the snapshot that the observation is **complete**
  (`BrokerSnapshot.complete: bool`, default `False` => cannot confirm flat);
- a per-position adapter attestation that a **close order is working** at the broker
  (`BrokerPositionSnapshot.close_pending: bool = False`);
- two new findings that separate these cases from the ambiguous ones:
  `CLOSE_PENDING_CONFIRMED` (derived status `UNKNOWN`: exposure still exists and is in transition,
  so new trades stay blocked) and `BROKER_FLAT_CONFIRMED` (a local non-`CLOSED` position, attributed
  by `nexora_position_ref`, absent from a **complete** snapshot with **no identity conflict**;
  derived status `UNSYNCHRONIZED` until the local record is brought to `CLOSED`).
  `LOCAL_OPEN_BROKER_MISSING` keeps meaning "absent or identity-conflicting" and never proves flat.
- `EXECUTION_RESULT_UNKNOWN` records use `ExecutionResult.nexora_position_ref` for `position_ref`
  when present.

Section 6 rule 1 requires a **complete** observation, so until OPEN-6 is decided and an adapter can
attest `complete`, **EMERGENCY recovery is unavailable for every target, including `MATCH` ->
`MANAGING`** (fail closed). If the adapter additionally cannot attest `close_pending`, the
EXIT_PENDING target stays unavailable. Whether a `MATCH` target may skip `complete` is a decision
requested of Rin (section 12 #8), not decided here.

### 4.7 Consumers affected (for planning the contract PR)

| Consumer | Affected by | Required change (later PR) |
|---|---|---|
| `execution/models.py` | 4.1, 4.3 | new/optional fields, per-action invariants |
| `execution/idempotency.py` (`build_execution_request`) | 4.1, OPEN-15 | pass `new_position_ref`; key derivation **unchanged** (ADR-034 s6) unless Rin decides otherwise under OPEN-15 |
| `execution/guard.py` | 3.3, 3.4, 4.1 | `protection_change`, `new_position_ref` inputs; rules G4-G7; supersede the F2 docstring/test |
| `execution/dedup_store.py` | 3.6, 3.9, 4.3 | `attempt`/`abort`/`reconciled`/`quarantine_resolved` recognized in `_events`; `_state` changes of W5 (released-after-abort only if OPEN-14; `quarantine_resolved` accepted; late-result-after-release detection kept); W3 state accessor; integrity-vs-I/O error split; late-result append; unresolved-key enumeration accessor; `deserialize_result` accepts `requested_quantity=None` and the new fields |
| `execution/broker_adapter.py` | 4.3, 4.5, 3.7, 3.8, INV-25 | action-aware `submit` (no refusal of MODIFY_PROTECTION/CLOSE once resolved), shared `validate_volume`, "UNKNOWN unless non-transmission proven", attribution ref handling, **idempotency identity for same-key retries (OPEN-15: generation-qualified adapter idempotency vs a new request_id/key per retry; today `_results[idempotency_key]` caches)**, and a read-only `adapter_mode` accessor on the Protocol (INV-25) |
| `execution/reconciler.py` | 4.6, 5.1, 5.3 | explicit instrument resolver (no `symbol == instrument_id` assumption), new findings/attestations, result attribution |
| `execution/reconciliation.py` | 4.6 | new findings in `_FINDING_STATUS` |
| `execution/close_all.py`, `close_all_plan.py` | 5.1, 6 | symbol filter on execution-domain symbol; EMERGENCY stays excluded; plan entries unchanged in shape |
| `position/models.py`, `position/supervisor.py` | 3.11, 6 | no shape change to `PositionRecord`; `apply_exit_decision` output is never applied to the durable store (3.11); **new** result-driven helper `apply_execution_result` (PR-8; no `ExitDecision` fabricated); guarded EMERGENCY recovery function |
| `autonomous/broker_capabilities.py` | 4.5 | `volume_step_anchor`, `validate_volume` |
| `tests/test_execution_contracts_v1.py`, `test_execution_guard.py`, `test_execution_dedup_store.py`, `test_execution_broker_adapter.py`, `test_execution_reconciler.py`, `test_execution_close_all_plan.py` | all | migrate constructors; supersede F2 test; add the tests named in section 7 |

## 5. Identity and ownership

### 5.1 Rules

- **Core canonical identity is `instrument_id`** (ADR-025 s3). Ownership and identity are **never
  inferred from a symbol alone**.
- `TradeIntent.symbol`, `PositionRecord.symbol` and `owned_open_positions(symbol=...)` carry the
  **execution-domain symbol**: in binding mode it is the canonical `instrument_id` (ADR-025 s9.2: the
  event symbol is the `instrument_id`); in legacy mode it is the legacy broker symbol, which is not
  a canonical identity.
- Exactly **one** conversion exists in core, `local symbol -> instrument_id`, performed by the
  `ExecutionInstrumentBinding` resolver (5.2) at step 2 and in the reconciler. A caller or UI never
  supplies `instrument_id` to the guard: the pipeline derives it from the resolver (today
  `evaluate_execution_guard(instrument_id=...)` is caller-supplied; the pipeline must own it).
- The **adapter boundary** owns `instrument_id <-> broker_symbol` through its `BrokerCapabilities`
  binding. `ExecutionRequest` and `BrokerPositionSnapshot` carry `instrument_id` only. An adapter
  must convert a broker symbol back to `instrument_id` through its declared binding and must report a
  broker position with no declared binding as unattributable broker-only, never guess.

### 5.2 Binding contract (both modes)

```text
ExecutionInstrumentBinding {
  mode: "binding" | "legacy"
  execution_symbol: str      # the exact string PositionRecord.symbol / TradeIntent.symbol carries
  instrument_id: str         # canonical (ADR-025 s3)
  feed_id: str | None        # binding mode: ADR-025 feed_id (a field of ResolvedFeed, not FeedBinding); legacy: None
  binding_ref: str           # opaque audit reference to the operator declaration
}
resolve_execution_instrument(execution_symbol) -> ExecutionInstrumentBinding   # fail closed
```

Rules: exact, case-sensitive match on `execution_symbol`; no trimming, prefix/suffix or similarity
logic (as ADR-025 s5); no declaration => deny `execution_binding_missing`. **Binding mode:** the
resolver is satisfied by the ADR-025 `FeedBinding`/`InstrumentDefinition` already validated at startup
(`execution_symbol == instrument_id`). **Legacy mode:** requires an **explicit operator-declared**
mapping `execution_symbol -> instrument_id` (never derived from the symbol's spelling); its
declaration source and format are not decided here (OPEN-9), and whether legacy mode may ever reach
transmission is OPEN-9. The reconciler takes the resolver as a required explicit parameter; there is
no implicit identity default (F7).

### 5.3 Ownership attribution (`nexora_position_ref`)

`nexora_position_ref` identifies a stable NEXORA-owned position and equals the local `position_id`.
The adapter must make it recoverable after a process restart by **either** (a) round-tripping it
through broker metadata where the broker supports it, so it appears on a later
`BrokerPositionSnapshot.nexora_position_ref`, **or** (b) a **durable adapter-owned mapping**
`broker_position_ref <-> nexora_position_ref` that survives restart. If neither holds, the adapter
reports `nexora_position_ref=None`, the reconciler classifies the position broker-only, and every
action is blocked (fail closed). Ownership never comes from symbol, side or quantity matching.

### 5.4 What `PositionRecord` / `ExecutionRequest` / reconciler carry

| Component | Carries | Does not carry |
|---|---|---|
| `PositionRecord` | `position_id` (= `nexora_position_ref`), execution-domain `symbol` (shape unchanged) | broker symbol, broker ticket |
| `ExecutionRequest` | `instrument_id`, `position_ref` / `new_position_ref`, `side` | broker symbol, suffix, digits, lot step, broker name (ADR-034 s4, unchanged) |
| `BrokerPositionSnapshot` | `instrument_id`, opaque `broker_position_ref`, `nexora_position_ref` | broker symbol |
| Reconciler | resolver `execution_symbol -> instrument_id`; compares `instrument_id` and `side` for the attributed position | any symbol-equality assumption |

## 6. EMERGENCY recovery

`TRADE_STATE_TRANSITIONS[EMERGENCY]` stays `frozenset()` and the generic `_require_transition`
keeps rejecting any `EMERGENCY -> X`. **EMERGENCY does not become a normal state.** The only exit is
a dedicated function that consumes verified evidence; no generic transition, no operator-chosen state:

```text
recover_from_emergency(position: PositionRecord, evidence: EmergencyRecoveryEvidence,
                       *, operator_ref: str, recovered_at: datetime) -> PositionRecord
   precondition: position.state is EMERGENCY; no target-state parameter exists

EmergencyRecoveryEvidence {
  position_ref: str                          # must equal position.position_id (the SAME NEXORA position)
  records: tuple[ReconciliationRecord, ...]  # all records for that position_ref from ONE classification run
  evidence_ref: str
  observed_at: datetime (aware)
}
```

**"Verified reconciliation evidence"** (all required):

1. one classification run (same `observed_at`), **fresh** (the freshness bound itself is supplied by
   approved policy/configuration and remains **OPEN-1**, see "Freshness" below), from a **complete**
   broker observation (4.6);
2. attribution to the same position by `nexora_position_ref` (5.3) with identical `instrument_id`
   and `side` — never by symbol alone;
3. for that `position_ref`, the **single confirming record** that selects the target
   (`MATCH`, `CLOSE_PENDING_CONFIRMED`, or `BROKER_FLAT_CONFIRMED`) is allowed even though the two new
   findings derive `UNKNOWN`/`UNSYNCHRONIZED`; **every other record for that `position_ref` must be
   absent or `MATCH`**; exactly one confirming finding may be present (two different confirming
   findings are ambiguous => no recovery); and the run contains no unattributed `UNKNOWN` record.
   **Relationship to 3.3:** this rule alone is *narrower than the aggregate rule* (it looks only at
   records for the recovered position plus unattributed `UNKNOWN` records, and would tolerate foreign
   broker-only `UNSYNCHRONIZED` records). That scope is not decided by Rin and is part of OPEN-4:
   until OPEN-4 is decided, recovery **additionally** requires the run's aggregate status, ignoring
   only the single confirming record, to be `SYNCHRONIZED` (the stricter 3.3 reading);
4. `operator_ref` non-blank, recorded as **provenance/audit identity only. It is NOT authorization**:
   a non-empty `operator_ref` alone **never** authorizes recovery (Rin decision D5; OPEN-20).

**Authorization boundary (OPEN-20; Rin decision D5).** Verified evidence (rules 1-3) is necessary but
is not itself authority to recover. Frozen here, and nothing more: (a) `operator_ref` is
provenance/audit identity, not authorization; (b) there is no timeout reset and no blind automatic
recovery; (c) the target state remains derived from the verified evidence (table below), never
chosen by an operator; (d) the **authorization model requires Security review** and is
**not defined by this ADR** (OPEN-20: owner Architect + Security, Rin approves the final freeze);
(e) **until OPEN-20 is resolved, any implementation of `recover_from_emergency` MUST fail closed at
the authorization boundary (deny) and MUST NOT be enabled for operational use.** The mechanism that
would satisfy the boundary is deliberately left undefined, so no implementation may substitute a
default (for example "any non-blank `operator_ref`", "any caller", or "any local process").
**R1 (RESOLVED_BY_RIN):** PR-3 **may be implemented and merged while OPEN-20 remains unresolved**,
provided `recover_from_emergency` stays fail-closed at this authorization boundary and cannot be
operationally enabled. OPEN-20 blocks **operational enablement**, not code merge. This is not
permission to invent an authorization mechanism, and recovery code or tooling is not operationally
usable merely because it exists.

**Freshness (OPEN-1; Rin M-1).** Rule 1 requires the evidence to be fresh, and that requirement is
preserved. The mechanism that validates freshness **may be implemented and merged in PR-3 without
OPEN-1 being resolved**, but it MUST (a) accept and use a freshness bound **supplied by approved
policy/configuration**; (b) **not invent a numeric value** and **not hard-code a temporary freshness
policy**; and (c) **fail closed** (no recovery) whenever operational use requires a freshness bound
and no approved bound is available. Implementation capability is not approved operational policy.
**OPEN-1 remains unresolved** and remains a BEFORE-PAPER-DEMO gate (section 8); it is **not** a
MUST-FIX-BEFORE-INTEGRATION blocker and not a condition for merging PR-3.

**Target state is derived, not selected:**

| Verified evidence | Target | Notes |
|---|---|---|
| `MATCH` for the position with `local_quantity == broker_quantity > 0` and protection matching | `MANAGING` | quantity = the matched quantity; still requires a complete observation (rule 1, OPEN-6, section 12 #8) |
| `CLOSE_PENDING_CONFIRMED` (4.6, OPEN-6) | `EXIT_PENDING` | local quantity 0 per the `PositionRecord` invariant |
| `BROKER_FLAT_CONFIRMED` (4.6, OPEN-6) with a complete snapshot | `CLOSED` | local quantity 0 |
| anything else (mismatch, unknown, ambiguous, missing, conflicting records) | **no recovery**; stays `EMERGENCY` | |

No timeout reset and no blind automatic recovery: elapsed time is never evidence, and the function is
invoked explicitly. If the local EMERGENCY record's quantity differs from the broker (a
`QUANTITY_MISMATCH`), the evidence is not `MATCH` and recovery is refused; adopting the broker
quantity into the local record is a state-repair capability that is **not decided** (OPEN-13).
Each recovery appends an immutable `EmergencyRecoveryRecord { position_ref, from_state=EMERGENCY,
to_state, evidence_ref, operator_ref, recovered_at }` to the journal (`operator_ref` there is audit
identity, not proof of authorization). Recovering a position does
**not** resolve any UNKNOWN idempotency keys for it (3.8). After recovery the normal gates apply
unchanged (the next action still needs a `SYNCHRONIZED` reconciliation).

## 7. Execution safety invariants

Restated from ADR-034 s9 (unchanged) and extended. Each names the test a later PR must add or
update.

| ID | Invariant | Test (new unless noted) |
|---|---|---|
| INV-01 | AUTO unavailable (`TradingMode.AUTO` denied by authority and guard) | existing `test_auto_mode_never_authorized_in_phase_1`, `test_auto_mode_never_yields_request` |
| INV-02 | Broker execution disabled: no real adapter, no `order_send` or equivalent, no network/broker import in `nexora.execution` | `tests/test_execution_pipeline.py::test_execution_package_has_no_broker_sdk_or_order_send` (static scan; extends `test_guard_module_has_no_forbidden_imports`) |
| INV-03 | Manual UI remains locked; ADR-035 and PR-1..PR-8 change no file under `apps/web` | per-PR diff check (no new test); `EXECUTION_LOCKED` in `apps/web/app/manual-execution.tsx` untouched (ADR-034 s9) |
| INV-04 | AI cannot bypass: no pipeline/preflight/recovery function takes an `AIAnalysis` | `test_pipeline_and_preflight_have_no_ai_analysis_parameter` |
| INV-05 | No synthetic `ResearchSignal`/`signal_id`/`signal_decision_ref`/`entry_readiness_ref`; `ResolvedExecution` and `EmergencyRecoveryEvidence` have no such field | `test_resolved_execution_has_no_signal_fields` |
| INV-06 | Pipeline order is exactly 1, 2, 2b, 3-9 as approved by Rin (D1); 2b applies to every action kind; only step 7 can reach a broker; a pure denial at steps 1-4 never calls `claim` | `test_pipeline_pure_denial_never_claims_key`, `test_pipeline_step_order_is_frozen` |
| INV-07 | Nothing is transmitted before `claim` and `attempt#g` are durable; marker failure => no `submit` | `test_submit_requires_durable_claim_and_attempt_marker`, `test_marker_write_failure_prevents_transmission` |
| INV-08 | Claim without marker is `CLAIMED_NOT_ATTEMPTED`; attempt without result is `ATTEMPTED_NO_RESULT` (UNKNOWN-equivalent, never auto-released); `attempt`/`abort` are mutually exclusive | `test_dedup_states_follow_w3_table`, `test_attempt_and_abort_mutually_exclusive`, `test_attempted_no_result_never_released` |
| INV-09 | `UNKNOWN` is never `REJECTED`, never blindly retried; adapter returns `UNKNOWN` unless non-transmission is proven | existing `test_unsafe_statuses_are_never_safe_to_retry`; new `test_adapter_returns_unknown_when_nontransmission_unproven` |
| INV-10 | Retry = same key after clean `REJECTED` (and, **only if OPEN-14 is approved**, `ABORTED_NEVER_ATTEMPTED`), with quantity re-resolved, and only once OPEN-15 is decided; everything else needs a new `proposal_id`; same-key CLOSE after partial fill is a duplicate | `test_retry_set_is_clean_rejected` (+ `test_retry_set_includes_never_attempted` only if OPEN-14 approved; otherwise `test_never_attempted_requires_new_intent`), `test_close_remainder_after_partial_fill_requires_new_intent`, `test_duplicate_never_reresolves_quantity` |
| INV-11 | Concurrent claim: exactly one `FIRST_CLAIM` | existing dedup concurrency test; extend for `attempt` race |
| INV-12 | The `reconciliation` health axis is derived from the same evidence the gate uses (including the enumeration of unresolved dedup keys, W5/OPEN-16); they cannot disagree | `test_health_reconciliation_axis_matches_gate_evidence` |
| INV-13 | REDUCE (Rin D3), CLOSE (Rin decision 5) and MODIFY_PROTECTION (Rin decision 4) require `SYNCHRONIZED`; a REDUCE is never allowed merely because it is risk-reducing; CLOSE local != broker quantity blocks without capping | `test_close_quantity_mismatch_blocks_without_cap`, `test_risk_reducing_requires_synchronized` (replaces `test_risk_reducing_not_blocked_by_reconciliation_alone`) |
| INV-14 | MODIFY_PROTECTION: TIGHTEN passes kill switch (but not other gates); WIDEN blocked while kill switch armed; unclassified/UNKNOWN denied; classification is derived, not caller-asserted | `test_guard_tighten_passes_kill_switch`, `test_guard_widen_blocked_by_kill_switch`, `test_guard_unclassified_protection_denied`, `test_pipeline_derives_protection_change` |
| INV-15 | CLOSE quantity is never fabricated at intent creation; resolved at step 2 from an `OPEN`/`MANAGING` record; recorded in `ExecutionResult.requested_quantity` and `attempt#g` | `test_close_quantity_resolved_before_guard`, `test_execution_result_preserves_resolved_quantity` |
| INV-16 | MODIFY_PROTECTION never carries or fabricates quantity; result invariants per action kind | `test_modify_protection_result_has_no_quantity`, `test_result_status_set_per_action` |
| INV-17 | Guard purity: no spread/slippage/margin/session/capability/idempotency logic inside `ExecutionGuard` | extend `test_guard_module_has_no_forbidden_imports` |
| INV-18 | Volume validation is driven by `BrokerCapabilities` (anchor defaults to `volume_min`); core hard-codes no stepping | `test_validate_volume_anchor_defaults_to_volume_min`, `test_validate_volume_honors_declared_anchor` |
| INV-19 | Identity: exactly one local-symbol -> `instrument_id` conversion; no ownership/identity from symbol alone; legacy mode needs an explicit binding | `test_missing_execution_binding_denies`, `test_legacy_symbol_never_accepted_as_instrument_id`, `test_reconciler_requires_explicit_resolver` |
| INV-20 | `nexora_position_ref` survives restart or the position is broker-only and blocked | `test_adapter_attributes_positions_after_restart`, `test_unattributed_position_blocks_actions` |
| INV-21 | `QUARANTINED` keys deny all intents; clearing is append-only, evidence-referenced, never timeout-based | `test_quarantined_key_denies`, `test_quarantine_clear_is_append_only` |
| INV-22 | EMERGENCY exits only through `recover_from_emergency`; table unchanged; target derived from evidence; refuses on mismatch; no operator-selected target | `test_emergency_transition_table_unchanged`, `test_emergency_exit_only_via_recovery_function`, `test_recovery_target_derived_from_evidence`, `test_recovery_refused_without_verified_evidence` |
| INV-23 | Position quantity/state is mutated only from a persisted `ExecutionResult`, only through the result-driven helper (3.11), never by fabricating an `ExitDecision` | `test_position_not_mutated_before_persisted_result`, `test_lifecycle_applied_without_exit_decision`, `test_no_fabricated_exit_decision` |
| INV-29 | The result-driven helper equals `apply_exit_decision` + `mark_closed` for FILLED CLOSE/REDUCE where an `ExitDecision` exists | `test_result_driven_application_equals_exit_decision_application` |
| INV-24 | Pipeline never loops or auto-resubmits | `test_pipeline_never_resubmits_after_release` |
| INV-25 | Phase 1 simulator-only: the pipeline reads a read-only `adapter_mode` accessor added to the `BrokerExecutionAdapter` Protocol (the adapter's `mode` is private and `BrokerCapabilities.execution_mode` is an opaque token, so neither is usable) and refuses unless it equals `SIMULATION_MODE` (`"simulation"`). This is adapter **self-attestation**, not a safeguard against a malicious adapter; the real barrier is that no real adapter exists and demo/real stay locked. Because the Protocol is `@runtime_checkable`, `SimulatedBrokerAdapter` must implement the accessor | `test_pipeline_refuses_non_simulation_adapter` |
| INV-26 | Outside pure unit tests the pipeline's dedup store is journal-backed (durable); `InMemoryExecutionDedupStore` is refused. Whether a simulated pipeline run may use in-memory is OPEN-18 | `test_pipeline_refuses_in_memory_dedup_store_outside_tests` |
| INV-27 | Quantity and protection payload come only from the sources fixed by OPEN-17; the pipeline denies when absent and never defaults or fabricates | `test_pipeline_denies_missing_quantity_or_protection_payload` |
| INV-28 | Authority is re-evaluated after step 2 for every action kind from the resolved/derived facts (for MODIFY_PROTECTION, the derived `ProtectionChange`); a decision computed before step 2 is never passed to the guard; `UNKNOWN`/unclassified/missing required facts deny (Rin D4) | `test_pipeline_reevaluates_authority_after_resolution`, `test_pipeline_reevaluates_authority_with_derived_protection_change`, `test_pipeline_denies_when_required_resolved_fact_missing` |
| INV-30 | `recover_from_emergency` fails closed at the authorization boundary until OPEN-20 is resolved and is not enabled for operational use; a non-empty `operator_ref` alone never authorizes recovery; no timeout or blind reset; the target stays derived (Rin D5) | `test_recovery_denied_at_authorization_boundary_until_open20`, `test_nonblank_operator_ref_alone_does_not_authorize_recovery`, `test_recovery_has_no_timeout_reset` |

## 8. Readiness gates

| Gate | Item | Evidence / PR |
|---|---|---|
| **MUST-FIX-BEFORE-INTEGRATION** (before `ExecutionPipeline` is merged) | Guard `ProtectionChange` + kill-switch/reconciliation rules G4-G7 (F1, F2) | PR-2; INV-13/14 |
| | `ExecutionResult` operation-aware (`requested_quantity` optional, `action`, `nexora_position_ref`) | PR-1; INV-15/16 |
| | `new_position_ref` on `ExecutionRequest`; `ResolvedExecution` | PR-1 |
| | Instrument binding/resolver and reconciler without the symbol-equality assumption (F7) | PR-1 types + PR-7 behavior; INV-19/20 |
| | Dedup write-ahead marker, `abort`, W3 state, late-result append (F6) | PR-5; INV-07/08/21 |
| | Shared `validate_volume` + anchor (F10) | PR-1/PR-7; INV-18 |
| | Position-mutation timing (3.11) | PR-4 + PR-8; INV-23/29 |
| | Reconciliation vocabulary decision (OPEN-6) and BrokerStateQuery port decision (OPEN-2) | Rin decision before PR-3/PR-4 depend on them |
| | EMERGENCY recovery, all targets (blocked on OPEN-6 `complete`; section 12 #8). **PR-3 MUST be merged before PR-4 (Rin D2)**; it is not optional for the first complete `ExecutionPipeline` integration. Merging PR-3 does **not** enable operational use: until OPEN-20 is resolved the implementation fails closed at the authorization boundary (section 6, INV-30) | PR-3 |
| | Result-driven lifecycle helper (3.11, INV-23/29) | PR-8 |
| **CAN-DO-DURING-INTEGRATION** | `ExecutionPreflight` structural checks | PR-6 |
| | `ExecutionPipeline`, exports in `execution/__init__.py`, task-record cleanup | PR-4 |
| | Denial audit logging (non-key-burning) | with PR-4 |
| | **Quarantine tooling**: may be developed as the dependency graph (section 9) allows; quarantine resolution and its authorization remain governed by **OPEN-3** | per section 9; OPEN-3 |
| | **EMERGENCY recovery code/tooling** (`recover_from_emergency`): may be implemented and merged (PR-3, R1), but it **MUST remain fail-closed at the authorization boundary and MUST NOT be operationally enabled until OPEN-20 is resolved**; no authorization mechanism may be invented before OPEN-20; a non-empty `operator_ref` alone is not authorization; the existence of recovery tooling is not operational enablement | PR-3; OPEN-20 gates enablement |
| **BEFORE-PAPER-DEMO** (simulator through the pipeline; not a broker demo) | OPEN-1 freshness bounds decided. This is the **policy value** only: the freshness-validation mechanism may be implemented and merged earlier (it takes the bound from approved policy/configuration, invents no number, hard-codes no temporary policy, and fails closed when none is approved). OPEN-1 is **not** a MUST-FIX-BEFORE-INTEGRATION blocker and not a condition for merging PR-3 (Rin M-1) | Quant/Architect |
| | OPEN-11 risk-reducing preflight policy decided (else risk-reducing stays denied) | Quant |
| | OPEN-5 position-level exclusivity decided (no enforcement exists until then) | Architect |
| | OPEN-7 local state while a close is unresolved, partial-fill quantity update and MODIFY_PROTECTION local update (without it a partial close or stop tighten leaves the system globally blocked) | Architect/Quant |
| | OPEN-19 idempotent application of persisted results | Architect |
| | OPEN-20 `recover_from_emergency` authorization model resolved (Security review) before the recovery function is enabled for any operational use, including the simulator path. **(R2, RESOLVED_BY_RIN: this is a required prerequisite of the BEFORE-PAPER-DEMO operational enablement; it adds a prerequisite and does not unlock the gate.)** | Architect + Security; Rin approves the final freeze |
| | OPEN-2 UNKNOWN resolution mechanism at least for the simulator | Architect |
| | Independent review of PR-1..PR-8 recorded; Security review (AGENTS.md s12: persistence, paper boundary) | review evidence |
| | **Broker demo** (non-simulated) is not in this row: it requires a later dedicated governance ADR | not decided here |
| **BEFORE-REAL-TRANSMISSION** | Dedicated governance ADR narrowly lifting AGENTS.md s0 (ADR-033 s21 a-c) and Security review | not drafted here |
| | A real `CapabilityProvider` declaring `volume_step_anchor`, `session/spread/margin` refs (ADR-033 s14 BLOCKED) | provider PR |
| | Real read-only BrokerStateQuery with completeness and order lookup; verified restart attribution (5.3) | OPEN-2/OPEN-6 |
| | OPEN-9 legacy-mode eligibility; Track E resolved for manual OPEN | Architect; Track E |
| | OPEN-20 authorization model for EMERGENCY recovery resolved and Security-reviewed. **(R2, RESOLVED_BY_RIN: this is a required prerequisite of the BEFORE-REAL-TRANSMISSION operational enablement; it adds a prerequisite and does not unlock the gate.)** | Architect + Security; Rin |
| | Duplicate/order-state/reconciliation safety (INV-06..13, 20, 21) all passing and independently reviewed | blocking |
| | Human approval; PROD isolation per AGENTS.md section 8 | human |

## 9. Implementation PR dependency graph

```text
PR-0  ADR-035 (this doc) ── Rin approval ──┐
                                           ▼
PR-1  contracts: ExecutionRequest.new_position_ref, ExecutionResult changes, ResolvedExecution,
      ExecutionInstrumentBinding types, BrokerCapabilities.volume_step_anchor + validate_volume,
      reconciliation vocabulary extension (after OPEN-6). Touches models.py, idempotency.py,
      reconciliation.py, broker_capabilities.py, tests/test_execution_contracts_v1.py.  No guard.py.
                                           │
        ┌────────────┬─────────────┬───────┴──────┬──────────────┐   (parallel; file-disjoint)
        ▼            ▼             ▼              ▼              ▼
   PR-2 guard   PR-3 EMERGENCY  PR-5 dedup     PR-6 preflight  PR-7 consumers: reconciler.py
   ProtectionCh. recovery       write-ahead    (new file)      (resolver, attribution),
   (guard.py,    (position/,    marker, abort,                 broker_adapter.py (action-aware,
   test_exec_    new module)    quarantine,                    shared validate_volume,
   guard)        needs OPEN-6   unresolved-key                 adapter_mode, OPEN-15 retry
                                enumeration                    identity)
                                (dedup_store.py)
        └────────────┴─────────────┴──────┬───────┴──────────────┘
                                          ▼
   PR-8 position/ result-driven helper (depends on PR-1; parallel with 2/5/6/7)
        │
        └───────────────────────────────┐
                                          ▼
              PR-4  ExecutionPipeline + exports + task-record cleanup
                    (depends on PR-1, 2, 3, 5, 6, 7, 8; PR-3 REQUIRED before PR-4 — Rin D2)
                                          ▼
                    integration review (independent) ── paper-demo gate (section 8)
```

PR-8 (result-driven lifecycle helper, new module under `position/`, 3.11) depends on PR-1 only and
may run in parallel with PR-2/5/6/7; PR-4 needs it. **PR-3 and PR-8 both add modules under
`packages/nexora/position/`** and may both want `position/__init__.py` exports, so they are not
file-disjoint on that file: serialize any `__init__.py` edit (the later-merging PR rebases and adds
only its own export; per AGENTS.md section 7).

PR-2's ProtectionChange logic is independent of PR-1; its `new_position_ref` passthrough needs
PR-1, so merge order is PR-1 then PR-2. PR-2/5/6/7 touch disjoint files and may run in parallel
after PR-1 (shared files, if any, per AGENTS.md section 7). **PR-3 (EMERGENCY recovery) MUST precede PR-4 (Rin D2)**: it is not optional for the first complete
`ExecutionPipeline` integration (this resolves the earlier contradiction between this section and
section 8). PR-3 additionally needs the OPEN-6
decision for **every** recovery target (including `MATCH` -> `MANAGING`, which needs `complete`);
PR-3 must not be built with an early MATCH-only path. **PR-3 does not need OPEN-1 resolved (Rin M-1):**
section 6 rule 1 requires fresh evidence, and PR-3 may implement the freshness-validation
mechanism, but it must take the bound from approved policy/configuration, must not invent or
hard-code any numeric or temporary bound, and must fail closed when operational use needs a bound and
none is approved; OPEN-1 stays unresolved and stays a BEFORE-PAPER-DEMO gate. PR-3's recovery function
must **fail closed at the authorization boundary** until OPEN-20 is resolved (section 6, INV-30), so
**PR-3 may be implemented and merged (R1, RESOLVED_BY_RIN)** as PR-4's dependency while the recovery
capability stays **disabled for operational use**; OPEN-20 gates operational enablement, not merging,
and no authorization mechanism may be invented. Every PR is a draft until independently reviewed;
none may merge to `main` without explicit human instruction (AGENTS.md section 10).

## 10. What ADR-035 does NOT decide

- **Track E (RISK-MANUAL-OPEN-1, PR #51) is untouched and BLOCKED.** ADR-035 does not choose A1, A2
  or B, does not specify how a manual OPEN's `RiskDecision` is produced, and does **not** fabricate a
  `ResearchSignal`, `signal_id`, `signal_decision_ref` or `entry_readiness_ref`. The pipeline is
  action-generic: it consumes an already-produced allow `RiskDecision` via authority (ADR-034 s1/s3)
  and creates none. Manual OPEN cannot run end to end until Track E is resolved.
- Demo or real broker transmission, and any governance ADR for either. No `order_send`.
- Numeric spread, slippage, margin, session and freshness policies; any Quant threshold
  (trailing, break-even, profit-lock, partial-close ratios, ADR-033 s15/s22).
- `RiskEngine.evaluate_reduction()` and its Quant limits (ADR-034 s2, unchanged).
- The reconciliation **remediation** procedure (repairing local state from broker state).
- The authorization model for operator-attested records (Security review): for EMERGENCY recovery
  this is **OPEN-20**; for clearing a quarantine it is OPEN-3. ADR-035 defines neither; it only fixes
  that `operator_ref` is provenance, not authorization, and that recovery fails closed at the
  authorization boundary until OPEN-20 is resolved.
- The Manual Execution UI, AUTO, any PROD runtime change, any web file.
- Any edit to ADR-025, ADR-033 or ADR-034.

## 11. Open decisions

Each is OPEN: **no policy is chosen or implied, and no default is selected.** The "Until decided"
column states behavior **forced by already-frozen invariants and fail-closed constraints** (an
unestablished safety fact denies). It is **not** a selected policy and must not be read as the
resolution of the item. Where an approved Rin decision narrows an item's text, that is marked
**RESOLVED_BY_RIN** in section 12; no OPEN item is resolved by this table.

| ID | Question | Owner | Until decided |
|---|---|---|---|
| OPEN-1 | Maximum age of reconciliation evidence, preflight verdict and health/authority inputs at step 6 and in EMERGENCY recovery | Quant + Architect | no approved bound exists, so any operational use that requires a bound fails closed: a mechanism that takes the bound from approved policy/configuration may be implemented and merged without a value (no numeric or temporary bound may be invented or hard-coded; **not** a pre-integration blocker and not a condition for merging PR-3, Rin M-1); no paper-demo (OPEN-1 remains a BEFORE-PAPER-DEMO gate) |
| OPEN-2 | The read-only **BrokerStateQuery** port: position snapshot with completeness, and order/deal lookup by request or idempotency reference; and whether manual operator attestation may resolve `ATTEMPTED_NO_RESULT`/`UNKNOWN` | Architect (+ Security) | no release path out of UNKNOWN; key stays blocked |
| OPEN-3 | Quarantine clearing: who may append `quarantine_resolved`, record format, authorization | Architect + Security | quarantined keys stay quarantined |
| OPEN-4 | Reconciliation scope for position actions: per-position subset vs aggregate; treatment of foreign broker-only positions and unattributed `UNKNOWN` records. **Stated cost of the aggregate-scope constraint (forced by the fail-closed rule; not a selected policy):** a mismatch on another position, or a foreign broker-only position, blocks **every** CLOSE and TIGHTEN, including in an emergency, **and makes EMERGENCY recovery impossible** (section 6 requires the aggregate, ignoring the confirming record, to be SYNCHRONIZED) while any foreign broker-only or other-position mismatch exists | Architect | aggregate only (3.3) |
| OPEN-5 | Position-level in-flight exclusivity between **different** intents on one position (F11): mechanism, and deny vs queue | Architect | **no enforcement exists**: the pipeline offers no protection against two different intents on one position, so any integration must be driven by one serialized caller by external discipline (not enforced by this ADR); real transmission blocked |
| OPEN-6 | Exact shape of the vocabulary extension in 4.6 (`complete`, `close_pending`, `CLOSE_PENDING_CONFIRMED`, `BROKER_FLAT_CONFIRMED`, derived statuses) and whether adapters can attest them | Architect | EMERGENCY recovery unavailable for **every** target, including `MATCH` -> `MANAGING` (section 6 rule 1 needs `complete`) |
| OPEN-7 | Local position state while a transmitted close/reduce is `UNKNOWN`/`ACCEPTED`/`PARTIALLY_FILLED` (and how a `PARTIALLY_FILLED` result updates local quantity); when/how local protection is updated for MODIFY_PROTECTION; what triggers EMERGENCY; the consequence of a late unsafe result. **Until decided, a partial close or a stop tighten leaves the system globally blocked** (3.7, 3.11) | Architect + Quant | local state not mutated before a persisted result (3.11) |
| OPEN-8 | Whether `volume_min`/step apply to a full-position CLOSE of a non-conforming residual, and to a REDUCE whose requested quantity or residual falls below `volume_min` or off the step grid | Architect + Quant | no exemption: such a CLOSE or REDUCE is blocked |
| OPEN-9 | Legacy mode: source/format of the operator-declared execution binding, and whether legacy mode may ever reach transmission | Architect (Rin) | legacy execution denied (`execution_binding_missing`) |
| OPEN-10 | `ProtectionChange` beyond stop-only tightening (target changes, stop removal), and whether a WIDEN should be permitted at all when the kill switch is off | Quant | only stop-only strict tightening is `TIGHTEN`; all else fails closed |
| OPEN-11 | Whether preflight spread/slippage/session/margin policy checks apply to REDUCE/CLOSE/MODIFY_PROTECTION | Quant | risk-reducing policy checks return `preflight_policy_undecided` (deny) |
| OPEN-12 | How a `PROTECTION_MISMATCH` may be repaired when TIGHTEN requires `SYNCHRONIZED` | Architect | blocked |
| OPEN-13 | Adopting broker quantity into a diverged local record (state repair), incl. EMERGENCY recovery with `QUANTITY_MISMATCH` | Architect | recovery refused; no repair |
| OPEN-14 | Confirm W4(ii): `ABORTED_NEVER_ATTEMPTED` as a retry-eligible state (widens the store's release set; needs the `_state`/`_events` changes of W5) | Rin | if declined, abort stays claimed; operator uses a new intent; every "resubmit as g+1 after abort" statement in 3.6/3.7/3.10 and INV-10's never-attempted clause is void |
| OPEN-15 | Retry identity at the adapter: the adapter contract is idempotent per `request.idempotency_key` and the simulator caches by it, so a same-key retry (generation g+1) would return the cached result. Options: generation-qualified adapter idempotency, or a new `request_id`/key per retry. ADR-034 s6 key derivation stays unchanged unless Rin decides otherwise | Rin + Architect | same-key retry not implementable; a new intent is required |
| OPEN-16 | How `ATTEMPTED_NO_RESULT` is represented to `classify_reconciliation` (explicit marker input vs a 4.6 vocabulary addition) | Architect | the gate cannot establish safety while such a key exists, so it treats any `ATTEMPTED_NO_RESULT` key as blocking `UNKNOWN` at step 1 (forced, not a chosen policy), which requires the store's unresolved-key enumeration accessor (W5); a single unresolved key therefore blocks every action globally, including an emergency CLOSE |
| OPEN-17 | Origin of the OPEN/REDUCE quantity and the MODIFY_PROTECTION payload: `TradeIntent` carries neither; candidates are Risk sizing, `RiskReductionDecision.resulting_quantity`/`resulting_protection`, `ExitDecision.reduce_quantity`/`new_stop_price`, or caller-supplied (today's guard). OPEN sizing is a Risk decision; this ADR does not touch Track E or manual-OPEN provenance | Architect + Quant (Risk) | pipeline **denies when the payload is absent**; never defaults or fabricates |
| OPEN-18 | Whether a non-durable `InMemoryExecutionDedupStore` is allowed for any simulated pipeline run | Rin | in-memory refused outside pure unit tests (INV-26) |
| OPEN-19 | Idempotent application of a persisted result to local state: `apply_execution_result` requires `OPEN`/`MANAGING`, so re-applying a FILLED CLOSE raises and re-applying a FILLED REDUCE would double-reduce. Needs an applied-result marker or an atomic "persist position + applied `result_id`" rule; the mechanism is not chosen here | Architect | a result that may already have been applied is not applied again and the position stays blocked (`RESTART_RECOVERY_PENDING`, status `UNKNOWN`) until reconciliation shows the true state; no automatic re-application |
| OPEN-20 | `recover_from_emergency` **authorization model** (Rin D5): who or what may authorize an EMERGENCY recovery once verified evidence exists (section 6). Frozen already: `operator_ref` is provenance/audit identity, **not** authorization; a non-empty `operator_ref` alone never authorizes recovery; no timeout or blind reset; the target stays derived from verified evidence. Not defined here: the authorization mechanism itself, which requires **Security review** | Architect + Security; Rin approves the final freeze | recovery implementation **fails closed at the authorization boundary (deny)** and is **not enabled for operational use** (section 6, INV-30); merging PR-3 does not enable it. **OPEN-20 itself remains OPEN.** Two sub-clauses are **RESOLVED_BY_RIN**: **R1** PR-3 may be implemented and merged while OPEN-20 is unresolved if recovery stays fail-closed at the authorization boundary and cannot be operationally enabled (OPEN-20 blocks operational enablement, not code merge; no mechanism may be invented); **R2** OPEN-20 is a prerequisite of both BEFORE-PAPER-DEMO and BEFORE-REAL-TRANSMISSION operational enablement (adds prerequisites; unlocks neither gate) |

## 12. Decisions requested of Rin personally

Status key. **RESOLVED_BY_RIN**: approved by Rin in the decision delta or the clarifications of
2026-10-04 (R1, R2) and applied in this ADR. **OPEN**: still requested and unresolved. The last column states behavior that is **forced
by already-frozen invariants and fail-closed constraints** until the item is decided; it is **not** a
selected policy and not a default chosen by this ADR (see the terminology note at the top of this
document). Where one row combines an approved part and an unresolved part, each part is labelled
separately.

| # | Decision | Status | Behavior until decided (forced by existing invariants; not a selected policy) |
|---|---|---|---|
| 1 | Approve the pipeline order, including resolution before guard and claim and the new last-look + attempt-marker step (3.1/3.2) | **RESOLVED_BY_RIN (D1: APPROVED)**; order frozen as 1, 2, 2b, 3-9 | decided; only step 7 may become broker-reachable and real transmission stays LOCKED |
| 2 | Retry identity at the adapter (OPEN-15) and the W4(ii) retry widening (OPEN-14) | OPEN | no same-key retry is implementable; a new intent is required |
| 3 | (a) REDUCE joins CLOSE/MODIFY_PROTECTION in requiring `SYNCHRONIZED`. (b) Acceptance of the aggregate-scope cost (OPEN-4: a position-wide or foreign broker-only mismatch blocks every CLOSE/TIGHTEN, including in an emergency, and also blocks EMERGENCY recovery) | (a) **RESOLVED_BY_RIN (D3: APPROVED)**; (b) OPEN | (a) decided: a REDUCE is never allowed merely because it is risk-reducing when reconciliation is `UNSYNCHRONIZED`/`UNKNOWN`; (b) aggregate scope is forced because nothing establishes that a narrower scope is safe |
| 4 | (a) The pipeline, not upstream, re-evaluates authority after resolution from the resolved/derived facts, including the derived `ProtectionChange` (3.4). (b) Payload origin (OPEN-17) | (a) **RESOLVED_BY_RIN (D4: APPROVED)**; (b) OPEN | (a) decided: `UNKNOWN`, unclassified or a missing required fact denies; (b) the pipeline denies when the payload is absent |
| 5 | Whether `preflight_policy_undecided` (deny) blocking **all** risk-reducing transmission, even in the paper simulator, is intended until OPEN-11 is decided | OPEN | blocked |
| 6 | Whether a non-durable in-memory dedup store is allowed for any simulated pipeline run (OPEN-18) | OPEN | refused outside pure unit tests (INV-26) |
| 7 | How the local lifecycle is advanced from a persisted `ExecutionResult` when no `ExitDecision` exists (manual CLOSE, CLOSE ALL, REDUCE): the result-driven helper of 3.11, with a single path also for algorithmic closes | OPEN | no local state mutation; nothing applied beyond FILLED CLOSE/REDUCE per 3.11 until approved |
| 8 | Whether EMERGENCY -> MANAGING (plain `MATCH`) requires a **complete** broker observation (4.6, OPEN-6), and the accepted cost that recovery is blocked while any unrelated or foreign mismatch exists (OPEN-4) | OPEN | a complete observation is required by section 6 rule 1, so recovery is unavailable for every target until OPEN-6 |
| 9 | Whether the OPEN-16 forced behavior (any single unresolved key blocks every action globally) is acceptable given the emergency-CLOSE cost, and the enumeration mechanism it needs | OPEN | blocks globally; the store's enumeration accessor is required |
| 10 | Whether the global block that follows a partial close or a stop tighten until OPEN-7 is decided is acceptable for the paper-demo gate, or whether OPEN-7 is decided earlier | OPEN | globally blocked; OPEN-7 is a paper-demo gate item |
| 11 | Idempotent application of a persisted result (OPEN-19): the mechanism (applied-result marker vs atomic position + result_id persist) | OPEN | no re-application; the position stays blocked until reconciled |
| 12 | OPEN-20: the authorization model for `recover_from_emergency`. The Architect + Security review produces it; Rin approves the final freeze. Sub-clauses: **(R1)** PR-3 may be implemented and merged while OPEN-20 is unresolved, provided `recover_from_emergency` stays fail-closed at its authorization boundary and cannot be operationally enabled (OPEN-20 blocks operational enablement, not code merge; no authorization mechanism may be invented). **(R2)** OPEN-20 is a prerequisite for both BEFORE-PAPER-DEMO and BEFORE-REAL-TRANSMISSION operational enablement (adds prerequisites; unlocks neither gate) | (OPEN-20 itself) OPEN; **(R1) RESOLVED_BY_RIN**; **(R2) RESOLVED_BY_RIN** | recovery fails closed at the authorization boundary and is not enabled for operational use (section 6, INV-30); R1 and R2 are decided |

`RESOLVED_BY_RIN` therefore appears only for rows 1 (D1), 3(a) (D3), 4(a) (D4) and the R1/R2
sub-clauses of row 12; OPEN-1 (M-1) and OPEN-20 itself stay OPEN.

Other Rin decisions of 2026-10-04 applied in this ADR (not rows above): M-1 (OPEN-1 is not a
pre-integration blocker; the freshness mechanism may merge without a bound; sections 6, 8, 9, 11), M-2
(quarantine tooling separated from recovery tooling; section 8), D2 (PR-3 required before
PR-4; sections 8 and 9), D5 (`operator_ref` is not authorization; OPEN-20; sections 6, 10, 11, INV-30),
D6 (section 8/9 contradiction repaired, this table repaired, "until decided"/"default" terminology
corrected), D7 (Track E stays BLOCKED; section 10), D8 (nothing is unlocked: no paper/demo
governance, no real broker adapter, no AUTO, no broker order transmission, no PROD deployment;
section 0).

EXECUTION INTEGRATION SAFETY AMENDMENT PROPOSED — ONLY ITEMS MARKED RESOLVED_BY_RIN ARE DECIDED; NOTHING ELSE IS FROZEN UNTIL RIN APPROVES