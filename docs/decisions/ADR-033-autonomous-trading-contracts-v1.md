# ADR-033 — Autonomous Trading Contract Freeze V1

Status: PROPOSED — Rin review required; CONTRACT FREEZE V1 NOT DECLARED until Rin approves
Date: 2026-10-02
Owner: Architecture Developer (Claude Code), under Rin (Architect / Tech Lead / Reviewer)
Task: TASK 1B — Autonomous Contract Freeze V1 (follows TASK 0 audit, TASK 1A pre-flight)
Sources: [AGENTS.md](../../AGENTS.md), [ADR-015](ADR-015-risk-engine-policy.md),
[ADR-016](ADR-016-paper-trading-simulator-boundary.md), [ADR-021](ADR-021-entry-readiness-v1.md),
[ADR-023](ADR-023-feature-lifecycle-v1.md), [ADR-024](ADR-024-pnf-pattern-engine-v1.md),
[ADR-025](ADR-025-mt5-instrument-resolution-v1.md)

**Numbering note:** a draft `ADR-032-autonomous-trading-wave0.md` exists only in the separate,
concurrently-owned worktree `D:\NEXORA\NEXORA-AUTONOMOUS-WAVE0` (branch
`codex/autonomous-architecture-v1`), not on `origin/main`. It was read for comparison only, per
instruction; nothing from it was copied blindly. This document is numbered ADR-033 to avoid a
collision if that draft is later merged under its own number. If Rin instead decides to supersede
that draft entirely with this one, the numbering can be reconciled at that time — not decided here.

## 0. Baseline

- `origin/main` = `44037e25b3217ea099a7e4a7e51fdf1b7fab634d` (unchanged since TASK 1A — fetched and
  confirmed at the start of this task).
- Worktree: `D:\NEXORA\NEXORA-AUTO-CONTRACTS`, freshly created from this exact SHA.
- Branch: `claude/autonomous-contract-freeze-v1`.
- `D:\NEXORA\NEXORA-AUTONOMOUS-WAVE0` was **not** touched, reset, cleaned, stashed, committed, or
  deleted. Its staged files (`ADR-032` draft, `packages/nexora/autonomous.py`,
  `tasks/AUTO-wave0.md`, `tests/test_autonomous.py`) remain exactly as TASK 1A found them.

## 1. Scope and discipline

This document freezes **contract shapes and boundaries** for NEXORA Autonomous Trading. It does
not implement live/demo broker execution, Position Supervisor behavior, trailing algorithms, news
or social providers, the AI Trade Manager, Strategy Router behavior, or Control Center backend
integration. AUTO remains unavailable for real execution under current governance (AGENTS.md
section 0/9). No PROD contact. No merge to main. No release/tag.

## 2. Reconciliation with TASK 0 (codebase audit)

No change to TASK 0's findings. Confirmed still true on this fresh checkout: PnfEngine,
StructureEngine, TrendlineEngine, PatternEngine (ADR-024, DISABLED/SHADOW), MarketRegimeEngine,
EntryReadiness, ExperienceEngine, RiskEngine, PaperSimulator, the ADR-025 instrument/broker
resolver, checkpoint/recovery, FeatureLifecycle (ADR-023), environment isolation, and the realtime
gateway all exist and are reusable. Position management, News/Social, AI Trade Manager, Buy/Sell
Force, Trading Session, notifications, and any realized-trade journal remain entirely missing.

## 3. Reconciliation with TASK 1A (pre-flight)

TASK 1A's findings stand, re-verified on this clean baseline:

- **Signal Engine pattern duplication is confirmed and unchanged.** `SignalEngine._patterns()`
  (`packages/nexora/signals/engine.py`) still independently recomputes pattern evidence
  (double top/bottom, head-and-shoulders, triangles, failed breakout/breakdown) directly from
  `structure.pivots`, and `research/pipeline.py` still runs `PatternEngine.process_event()` in
  parallel without ever passing its output into `signals.evaluate()`. This ADR freezes the
  **target ownership boundary only** (section 9) — the reconciliation itself is explicitly out of
  scope for TASK 1B, per instruction C.
- **Test-failure triage re-confirmed on a clean checkout.** Both
  `tests/test_pattern_engine.py::test_pattern_core_has_no_decision_or_runtime_dependencies` and
  `tests/test_validation_core.py::test_fresh_process_reproduces_run_id_and_bundle_hashes` **pass**
  when run against this fresh `origin/main` worktree (see section 20). This proves the first
  failure was caused entirely by the concurrent, uncommitted `autonomous.py` draft in the other
  worktree (which imports `nexora.features`, tripping that file's architectural-isolation guard) —
  **not** a defect on `origin/main`. The second failure remains consistent with order-dependent
  flakiness in the full suite, not a baseline defect.
- TASK 1A's proposed pipeline (`Market Evidence → Strategy/Signal → AI Analysis → Deterministic
  Permission → Risk Guard → Execution Guard → Broker Adapter`) is **superseded** by Rin's frozen
  pipeline in section B of the task prompt, which moves AI Analysis to *after* Entry Permission and
  introduces the explicit `TradeIntent` boundary (section 4 below). TASK 1A's `TradeDecision` as a
  primary execution-domain object is explicitly **not** frozen here, per instruction F — see
  section 10.

## 4. Reconciliation with Position Pre-flight

**No "Position Pre-flight" document, task file, ADR, branch, or worktree could be located anywhere
in this repository.** I searched `tasks/`, `docs/decisions/`, all local branches, and all worktrees
(including detached-HEAD ones) for `Position`, `PositionSupervisor`, `ExitDecision` and found no
prior-review artifact distinct from TASK 0's capability audit. Section J of the Rin-decisions
prompt itself contains substantially more concrete guidance than any document I could find, so the
Position boundary in this ADR (section 15) is derived directly from: (a) TASK 0's confirmed finding
that Position Management is entirely missing in code, and (b) Rin's explicit decisions in section J
of the TASK 1B instructions. **Flag for Rin:** if a Position Pre-flight exists in a session or
system I don't have access to, its findings were not available to this task and should be checked
against section 15 before this ADR is treated as final on that point.

## 5. Reconciliation with News/Social Pre-flight

**Same gap.** No "News/Social Pre-flight" artifact exists anywhere in this repository. TASK 0
confirmed News/Social is entirely missing in code (no provider, calendar, blackout logic, or social
monitoring; `Experience.engine.py` has only an unpopulated `news_context: None` placeholder). The
News/Social boundary in this ADR (section 16) is derived from TASK 0's audit plus Rin's section K
decisions directly. **Flag for Rin:** same caveat as section 4 — confirm against any pre-flight
this task could not locate.

## 6. Reconciliation with Control Center Pre-flight

**Found and reconciled.** `D:\NEXORA\NEXORA-CONTROL-CENTER` (branch `claude/control-center-ui-v1`,
commit `3188ada`, unmerged, ahead of `origin/main`) contains a mock-only, isolated UI design: flat
files `apps/web/app/control-center-*.{tsx,css}`, `control-center-types.ts`, and
`control-center-mock-data.ts`, explicitly documented as "design-only... no backend APIs invented,
no execution wiring, no trading contract changes." AUTO is modeled as **present-but-locked**
(visible, not selectable) rather than merely defaulted off, matching this ADR's governance stance
(section 19).

Its `ControlCenterSnapshot` read-model shape (read directly from `control-center-types.ts` /
`control-center-mock-data.ts`):

```
ControlCenterSnapshot {
  tradingMode:    { mode, autoAvailable, autoTradeEngaged }
  systemHealth:   { status, feed, researchMode, blockReasons[] }
  riskSettings:   { fixedLot, maxExposure, maxDailyLoss, killSwitchArmed }
  positions:      [ { id, symbol, side, openPrice, currentPrice, profitLoss,
                      stopLoss, takeProfit, breakEvenArmed, trailingArmed,
                      partialCloseLevels[] } ]
  marketRegime:   { label, reason, sidewaysDetected }
  newsScanner:    { status, tradeDuringNews, nextBlackoutWindow }
  socialScanner:  { status, lastSignal }
  alertChannels:  { soundEnabled, telegramConnected }
  emergencyStop:  { armed, lastTriggeredAt }
}
```

Per instruction L, this is confirmed **not** a backend contract — it is a UI fixture shape. Section
19 maps each field to the backend contract that will eventually supply it, without building that
wiring now.

## 7. Frozen autonomous pipeline

Rin's conceptual pipeline (instruction B) is adopted as the frozen direction, superseding TASK 1A's
proposal:

```
Market Evidence
    ↓
Strategy / Signal           (SignalEngine today; target: consumes PatternEngine, not _patterns())
    ↓
Entry Permission             (EntryReadiness, as-is — ADR-021 unchanged)
    ↓
AI Analysis / Critique       (NEW — advisory only; may enrich/critique/warn/recommend WAIT)
    ↓
TradeIntent                  (NEW — see section 10)
    ↓
Risk Guard                   (RiskEngine, extended per section 13)
    ↓
Execution Guard              (NEW — SystemHealthGate + dup/spread/slippage/margin, section 11/18)
    ↓
Broker / Paper Adapter       (extends ADR-025, section 17)
```

AI Analysis sits **after** Entry Permission, not before it — it critiques an already-permitted
candidate, it does not gate whether a candidate exists. This is a deliberate correction from TASK
1A's draft (which placed AI immediately after Strategy/Signal) and matches instruction B exactly.

## 8. Frozen authority model

Execution authority is deterministic and is the conjunction of every gate below; AI has **no**
vote in this conjunction:

```
authorized = TradingModeGate(mode, assisted_confirmation)
         AND EntryReadiness == READY
         AND TradeIntent.validate() passes (kind-specific, section 10)
         AND RiskGuard.allow
         AND ExecutionGuard.allow (SystemHealthGate + dup/spread/slippage/margin)
```

**Forbidden by construction, not by convention:** no function in this authority chain may accept
an `AIAnalysis` value as an input parameter. `AIAnalysis` is consumed only by UI/explanation
surfaces and by nothing that produces a boolean authorization. This mirrors the pattern already
proven safe in the concurrent worktree's `evaluate_authority()` (no AI-approval parameter exists in
its signature at all) — reused here as a design principle, not as code.

**Degraded-state invariant (new, from instruction G):** a degraded `SystemHealthGate` state must
never be interpreted as permission to *increase* risk. It always blocks `TradeIntentKind.OPEN`; it
does **not** always block `REDUCE`/`CLOSE`/`MODIFY_PROTECTION` — see section 15 for the split
between `NewTradeAuthority` and `ExistingPositionAuthority`.

## 9. Frozen Pattern ownership boundary

**Target:** `PatternEngine` (ADR-024) is the single source of truth for pattern evidence.
`SignalEngine` must eventually accept a `PatternEngineSnapshot` (or equivalent `PatternResult`
tuple) as an explicit input to `evaluate()` and remove its internal `_patterns()` method entirely —
no parallel recomputation.

**Migration boundary (contract only, not implemented in TASK 1B):**
- `SignalEngine.evaluate()` gains a new required keyword parameter, e.g. `patterns:
  PatternEngineSnapshot`, sourced from the same `pattern_engine.process_event()` call
  `research/pipeline.py` already makes.
- `SignalEngine._patterns()` and its two algorithm-version tags (`p8a-pattern-v1`,
  `p8b-pattern-v2`) are retired; `assessment.patterns` is populated from `PatternResult.direction`
  instead, preserving the existing `confirmation`/`conflict` relation logic in `_assess_components`
  as closely as possible to avoid an unreviewed behavior change in BUY/SELL scoring.
- This changes the Signal Engine's behavior (pattern evidence previously came from pivots directly;
  it will come from `PatternEngine`'s SHADOW/DISABLED-gated output instead) and therefore requires
  Quant sign-off (Q-PE1–Q-PE3, still open) before implementation, not just an engineering change.
- **Do not silently activate PatternEngine** as a side effect of this migration — its
  DISABLED/SHADOW status under ADR-024 is a separate, still-open decision.

Status: **BLOCKED** on Q-PE1–Q-PE3. Contract direction is **FROZEN**; implementation is not
scheduled by this ADR.

## 10. Frozen TradeIntent contract

TASK 1A's `TradeDecision` is **not** frozen as the primary execution-domain object (instruction F).
Instead:

```
TradeIntentKind = OPEN | REDUCE | CLOSE | MODIFY_PROTECTION

TradeIntent {
  kind: TradeIntentKind
  provenance: Provenance            # proposal_id, feed_id, configuration_hash, rules_version, source_refs
  symbol: str
  side: long | short                # meaningful for OPEN; carried through for REDUCE/CLOSE on an existing position
  origin: EntryOrigin | PositionOrigin   # see below — kind-discriminated, not a shared free-form dict
}

EntryOrigin {                       # origin when kind == OPEN
  signal_decision: SignalDecision   # existing type, reused as-is
  entry_readiness: EntryReadinessSnapshot   # existing type, reused as-is
  ai_analysis: AIAnalysis | None    # advisory context only, never authority
}

PositionOrigin {                    # origin when kind in {REDUCE, CLOSE, MODIFY_PROTECTION}
  position_id: str
  exit_decision: ExitDecision       # section 15 — NOT a synthetic ResearchSignal
}
```

**Explicit boundary (instruction F):** `TradeIntent` is a *common envelope*, not a shared
validation path. `OPEN` and risk-reducing kinds (`REDUCE`/`CLOSE`/`MODIFY_PROTECTION`) have
**distinct validation semantics**, documented explicitly rather than assumed:

| | OPEN | REDUCE / CLOSE / MODIFY_PROTECTION |
|---|---|---|
| Requires | `EntryReadiness == READY` | an existing `OPEN`/`MANAGING` `TradeLifecycle` instance for the referenced position |
| Blocked by degraded health | always (new exposure) | only when the fault is `broker: UNHEALTHY` (connectivity) — see section 15 |
| Sizing source | `RiskEngine.evaluate()` on the entry proposal | sizing is a *reduction* of already-approved size; never re-runs new-trade risk approval |
| Can increase exposure | yes, bounded by Risk Guard | **never**, as a *policy* invariant — see enforcement note below |
| Origin type | `EntryOrigin` (wraps `SignalDecision`) | `PositionOrigin` (wraps `ExitDecision`) — **never** a synthetic/fake `ResearchSignal` |

This directly answers instruction F: yes, `OPEN` and risk-reducing intents need distinct validation
semantics, and the discriminated `origin` field is the boundary that prevents `ExitDecision` from
ever being coerced into looking like a `ResearchSignal`.

**Enforcement note (review correction):** today, `TradeIntent`'s type only guarantees
*kind-vs-origin* discrimination — `OPEN` requires `EntryOrigin`, the three risk-reducing kinds
require `PositionOrigin` (enforced in `__post_init__`, validated by
`tests/test_autonomous_contracts.py`). **"Must never increase exposure" is not yet enforceable by
`TradeIntent`'s type alone**, because `TradeIntent` as frozen here carries no size/quantity field to
constrain — there is nothing for the type system to bound. Enforcement of the actual
never-increases-exposure invariant belongs to the future risk-reduction / position validation path
(`RiskEngine.evaluate_reduction()`, section 12), which is where a concrete size comparison against
current exposure can exist. This keeps the invariant's disposition **PROVISIONAL**, consistent with
section 25 — it is a frozen *requirement* on the future implementation, not a property already
guaranteed by the contract shape frozen today.

Status: contract shape **FROZEN**. The never-increases-exposure invariant itself is **PROVISIONAL**
(see enforcement note above). No implementation beyond the minimal pure scaffold in
`packages/nexora/autonomous_contracts.py` — no wiring to `RiskEngine`, no I/O.

## 11. Frozen entry-vs-position safety model

Two separate authority concepts, per instruction G:

```
NewTradeAuthority {
  inputs: TradingConfig, SystemHealthGate snapshot, EntryReadiness, RiskDecision
  rule: UNKNOWN | UNHEALTHY | STALE | UNSYNCHRONIZED (any axis)  =>  deny
  note: this is the ONLY authority that can ever approve TradeIntentKind.OPEN
}

ExistingPositionAuthority {
  inputs: TradingConfig, SystemHealthGate snapshot, current TradeLifecycle state, ExitDecision
  rule:
    - broker connectivity UNHEALTHY  =>  deny ALL actions (including CLOSE) — see fail-closed note below
    - any other health axis UNKNOWN/UNHEALTHY/STALE/UNSYNCHRONIZED =>
          deny actions that increase exposure or widen protection (these are OPEN-shaped by effect)
          allow actions that reduce exposure or tighten protection (REDUCE, CLOSE, TIGHTEN)
  safety invariant: a degraded state must never justify INCREASING risk, under any combination
}
```

**Fail-closed semantics when broker connectivity itself is unhealthy:** no order of any kind —
including a risk-reducing `CLOSE` — can be safely transmitted if there is no confirmed channel to
the broker. This is intentionally stricter than "degraded but exits allowed": a `CLOSE` sent into a
broken connection is not a safety action, it is an unverifiable one. The position is *not* assumed
closed, flattened, or safe by this denial — it is simply unmanaged until connectivity and
reconciliation are restored.

**Reconciliation after reconnect is explicit, not automatic:** re-establishing `broker: HEALTHY`
does not, by itself, re-arm management. A `ReconciliationState` must transition from `UNSYNCHRONIZED
→ reconciled` (comparing journaled last-known position state against a fresh broker position query)
before `ExistingPositionAuthority` allows any new action on that position. This reuses the
"verify-before-trust" pattern already proven in checkpoint/recovery (ADR-022/029 `state_hash()` /
`verify()`) as a design precedent, not as shared code.

Status: **FROZEN** as a boundary/invariant set. The specific reconciliation algorithm is **not**
designed here (depends on Broker Adapter, section 17, which is itself provisional).

## 12. Frozen Risk migration boundary

Current state (confirmed in TASK 1A, re-verified here): `RiskProposal{signal: ResearchSignal,
price}` → `RiskEngine.evaluate()` → `RiskDecision`, consumed only by `packages/nexora/paper/
session.py`. This shape is **insufficient** for `REDUCE`/`CLOSE`/`MODIFY_PROTECTION`, and
instruction F explicitly forbids forcing those through synthetic `ResearchSignal` objects.

**Minimum migration path (frozen as direction, not implemented):**

1. Introduce a new, narrower proposal shape alongside the existing one —
   `RiskReductionProposal{position_id, trade_intent: TradeIntent, current_exposure}` — rather than
   widening `RiskProposal` itself. `RiskProposal` keeps its exact current shape and behavior
   unchanged, so existing paper-session behavior is **not** silently altered.
2. `RiskEngine` gains a second method, e.g. `evaluate_reduction(proposal: RiskReductionProposal) ->
   RiskDecision`, reusing the existing `RiskDecision` output type (no new decision type needed) but
   with a validator that **rejects** any resulting decision whose size would increase exposure —
   enforcing the "never increase risk" invariant from section 11 at the Risk Guard layer too, not
   only at the `TradeIntent` type layer (defense in depth, not redundant).
3. `evaluate()` (existing, OPEN-only) and `evaluate_reduction()` (new) are two distinct entry
   points on the same `RiskEngine` instance — this avoids duplicating `RiskEngine`'s internal
   exposure-tracking state while keeping OPEN and reduction validation semantically separate per
   section 10's table.
4. No change to `PaperSimulator`'s current `apply_decision()` contract is required for this
   boundary to exist — it is additive.

This path preserves existing `RiskEngine` behavior exactly (point 1), supports `OPEN` (unchanged),
supports risk-reducing actions (new method), does not duplicate `RiskEngine` (one class, two entry
points, shared exposure/position state), and does not silently change current paper behavior (the
existing method and its callers are untouched).

Status: **PROVISIONAL** — direction frozen, Quant sign-off needed on how `RiskPolicy` limits
(`max_total_exposure`, `max_drawdown`) apply to a reduction path before `evaluate_reduction()` is
implemented.

## 13. Frozen Execution boundary

`ExecutionGuard` is the last deterministic gate before `BrokerExecutionAdapter`. It consumes an
already-`RiskDecision`-approved `TradeIntent` and checks, in this order: `SystemHealthGate` (section
18, including the `NewTradeAuthority`/`ExistingPositionAuthority` split), duplicate-order detection
(keyed on `TradeIntent.provenance.proposal_id`, reusing the idempotency pattern already used in
`paper/simulator.py`), spread guard, slippage guard, and margin validation (the last two require
`BrokerCapabilities`, section 14, and are therefore `BLOCKED` until that contract has a real
provider). Only after all pass does it emit an `ExecutionIntent` (broker-shaped order:
instrument/feed id, side, size-in-broker-units, stop, targets) for `BrokerExecutionAdapter` to
consume, which returns an `ExecutionResult` (filled/rejected/error) that drives the
`TradeLifecycle` transition.

Status: boundary **FROZEN**; spread/slippage/margin checks **BLOCKED** on `BrokerCapabilities`
having a real (non-mock) provider.

## 14. Frozen BrokerCapabilities contract

Extends ADR-025 conceptually, as a **sibling** contract to `InstrumentDefinition`/`FeedBinding`
(not a merge into them, preserving ADR-025's existing blast radius — matching TASK 1A's §7 and the
concurrent worktree's `CapabilityProfile` draft, which already implements the data-only, fail-closed
shape correctly):

```
BrokerCapabilities {
  binding: FeedBinding              # reused exactly from ADR-025 — no new symbol resolution
  volume_min, volume_max, volume_step: Decimal
  digits, point, tick_size, tick_value: <reused from InstrumentDefinition/PriceGrid where already owned>
  contract_size: <reference to InstrumentDefinition.trade_contract_size — NOT duplicated>
  stops_level, freeze_level: Decimal
  filling_modes: tuple[str, ...]     # adapter-supplied tokens; core defines no broker numeric constants
  execution_mode: str                # adapter-supplied token
  session_policy_ref: str            # opaque reference, not an inline calendar
  spread_policy_ref: str             # opaque reference, not an inline spread limit
  margin_policy_ref: str             # opaque reference, not an inline margin formula
  observed_at: datetime (aware)
}
```

No broker name hard-coding, no `XAUUSD`-specific assumption, no broker-specific constant in trading
core — enforced the same way ADR-025 already enforces it (exact-match binding, no fuzzy resolution,
fail-closed on any observed-vs-declared mismatch).

Status: **FROZEN** as a shape. **BLOCKED** as a working contract until a real `CapabilityProvider`
implementation exists (today, at most, a mock/offline one) — no broker capability discovery is
authorized by this ADR.

## 15. Frozen Position contract (PositionSupervisor / ExitDecision boundary)

Per instruction J, Position Supervisor is **not implemented**. Boundaries only:

```
PositionSupervisor {
  consumes: PnfEngine / StructureEngine / PatternEngine / TrendlineEngine snapshots (read-only)
  must NOT: reimplement any of those engines
  owns: TradeLifecycle.MANAGING state for a given position
  initial SL/TP: consumed from SignalDecision.invalidation_price / .targets at OPEN time,
                 NOT recomputed independently
}

ExitDecision {
  action: HOLD | TIGHTEN | PARTIAL_CLOSE | CLOSE
  evidence: tuple[SignalEvidence, ...]     # same evidence discipline as SignalDecision
  source_refs: tuple[str, ...]
  # deliberately NOT a ResearchSignal — no signal_id/side/decision_time masquerade
}
```

**Explicitly not frozen here (remain Quant decisions, per instruction J):** break-even trigger,
profit-lock threshold, trailing distance, P&F-vs-Structure trailing precedence, partial-close
ratios, time exit policy, re-entry cooldown. Freezing the `ExitDecision` action vocabulary does not
freeze any of the thresholds that decide which action fires.

**Partial-close lifecycle semantics (fast-follow clarification):** a successful `PARTIAL_CLOSE`
does **not**, by itself, close the `TradeLifecycle`. If residual position quantity remains after
the partial close, the lifecycle returns to — or remains in — `MANAGING`; it does not advance to
`EXIT_PENDING`/`CLOSED`. Only a position whose quantity has gone fully to zero may progress
`MANAGING → EXIT_PENDING → CLOSED` (the existing transition table in `autonomous_contracts.py`
already permits this path; it does not need to change). This is a clarification of intent, not a new
transition: `PartialClose` is one of potentially several `MANAGING`-state events a future
`PositionSupervisor` would need to track residual-quantity bookkeeping for, and that bookkeeping is
explicitly **not** designed here — `PositionSupervisor` behavior remains unimplemented per
instruction J.

**Architecture note for future Risk migration:** `packages/nexora/risk/models.py`'s existing
`RiskDecision` requires a `signal_id: str` field (confirmed by inspection — `RiskDecision.signal_id`
is non-optional today). The future risk-reduction contract (section 12's `evaluate_reduction()`)
must **not** paper over this by inventing a synthetic `ResearchSignal` or a fake `signal_id` for
position-management actions — that would silently reintroduce the exact
"risk-reducing-action-pretending-to-be-a-signal" anti-pattern this ADR's `TradeIntent`/`PositionOrigin`
boundary (section 10) was built to prevent. Before `evaluate_reduction()` is implementation-ready,
the future Risk-reduction contract must explicitly resolve how a position-management action
satisfies (or replaces) `RiskDecision.signal_id` — e.g. a new, distinct decision type for
reductions, or a widened `RiskDecision` with an explicit non-signal identity field — rather than
leaving this gap for an implementer to improvise around. This is additive to section 12's existing
PROVISIONAL disposition, not a new blocker.

Status: interfaces **FROZEN**; all policy values **BLOCKED** on Quant; partial-close lifecycle
interaction clarified above, still **BLOCKED** on `PositionSupervisor` implementation.

## 16. Frozen News/Social boundary

Per instruction K, the Provider → Scanner → normalized event/context → consumers pattern is
accepted, but the specific `MarketEvent` envelope shape proposed anywhere (including the mock
`newsScanner`/`socialScanner` shapes in the Control Center fixtures) is **not** frozen. The only
invariant frozen here:

- A social post must never directly cause BUY or SELL (hard invariant, matches instruction K).
- "Social = WARN only" is **not** frozen — policy may eventually WARN *or* BLOCK new entry,
  pending future Quant/Architect decision. Only the *mechanism boundary* (News/Social can influence
  `NewTradeAuthority`, never directly emit a `TradeIntent`) is frozen.
- New-entry news policy and open-position news policy are frozen as **separate** decisions.
  `TRADE_DURING_NEWS=OFF` affects `NewTradeAuthority` only; it must **not** be read as "close
  existing positions" — that would require its own `ExitDecision` policy, not frozen here, pending
  reconciliation with section 15's `ExitDecision`/`TradeIntent` semantics (instruction K, explicit).

Status: mechanism boundary **FROZEN** (influences `NewTradeAuthority`, cannot emit `TradeIntent`
directly). Event envelope shape and existing-position news policy: **BLOCKED**, unresolved by
design, pending Architect/Quant decision.

## 17. Frozen Broker/Paper Adapter boundary

`BrokerExecutionAdapter` extends ADR-025's resolver; it is the only module permitted to call a
broker execution API. It consumes `ExecutionIntent`, returns `ExecutionResult`. The existing
`PaperSimulator` remains the Phase 1 implementation reached by this same boundary in SHADOW/paper
mode — the adapter interface must be able to address both a future real broker and the existing
paper simulator without the `TradeIntent`/`ExecutionIntent` layers knowing which one is behind it.

Status: interface **FROZEN** in shape; **BLOCKED** entirely for any real-broker implementation by
current governance (section 22).

## 18. Frozen SystemHealthGate contract

```
SystemHealthGate {
  inputs: {
    market_data: Health        # quote freshness / feed continuity
    broker: Health              # connection state
    account: Health              # account/margin state
    capabilities: Health          # BrokerCapabilities freshness/validity
    reconciliation: Health        # position-state sync vs. broker, see section 11
    journal: Health                # checkpoint/recovery state validity (reuse ADR-022/029 verify())
  }
  Health = HEALTHY | UNKNOWN | UNHEALTHY | STALE | UNSYNCHRONIZED
  default: UNKNOWN for every axis (fail-closed)

  for NewTradeAuthority:
    any axis in {UNKNOWN, UNHEALTHY, STALE, UNSYNCHRONIZED} => NO_NEW_TRADE

  for ExistingPositionAuthority: see section 11 (separate, narrower rule)
}
```

This widens the concurrent worktree's `SafetyInputs`/`Health` draft (which had `HEALTHY`/`UNKNOWN`/
`UNHEALTHY` only) with `STALE` and `UNSYNCHRONIZED` as named axes-values, per instruction I's
explicit list, and reuses existing staleness/verification machinery (`SignalEngine`'s
`future_inputs` causal guard; ADR-012 data-quality sidecar; ADR-022/029 checkpoint `state_hash()`/
`verify()`) as the implementation precedent for `STALE`/`UNSYNCHRONIZED` respectively — not new
mechanisms.

Status: **FROZEN** as a shape and as the fail-closed default. Wiring to real health sources is
**BLOCKED** (no broker/account connection exists to observe yet).

## 19. Frozen AI authority boundary

`AIAnalysis` (new type) may read `SignalDecision`, `EntryReadinessSnapshot`, `MarketRegimeEngine`
output, `ExperienceEngine` output, and (once they exist) News/Social normalized events. It may
enrich, critique, warn, or recommend WAIT. **It is not accepted as a parameter by any function in
the authority chain in section 8** — not `EntryReadiness`, not `RiskEngine`, not `ExecutionGuard`,
not `SystemHealthGate`, not the Kill Switch. No `AI_APPROVED => EXECUTE` path exists or may be
designed (instruction A, absolute). This is enforced at the type/signature level: those functions
simply have no slot for an `AIAnalysis` argument, so there is nothing to bypass by omission.

Status: **FROZEN**, no exceptions pending.

## 20. Frozen Control Center read-model boundary

Per instruction L, the backend remains the source of truth; the UI must never infer execution
authority locally. Mapping each `ControlCenterSnapshot` field (section 6) to the backend contract
that will eventually supply it, **without building that wiring now**:

| UI field | Backend contract (this ADR) |
|---|---|
| `tradingMode.{mode,autoAvailable,autoTradeEngaged}` | `TradingModeGate` / `TradingConfig` (section 8) |
| `systemHealth.{status,feed,researchMode,blockReasons[]}` | `SystemHealthGate` (section 18) + `EntryReadiness.blockers` (existing) |
| `riskSettings.{fixedLot,maxExposure,maxDailyLoss,killSwitchArmed}` | `RiskPolicy` (existing, ADR-015) + kill-switch flag from `SystemHealthGate.inputs` |
| `positions[]` | `TradeLifecycle` instances in `OPEN`/`MANAGING` + `PositionSupervisor` state (section 15, not yet implemented) |
| `marketRegime.{label,reason,sidewaysDetected}` | `MarketRegimeEngine` / `RegimeSnapshot` (existing, unchanged) |
| `newsScanner` / `socialScanner` | section 16 — **BLOCKED**, no backend exists yet; UI's `NOT_IMPLEMENTED` status is honest and should stay until section 16 is unblocked |
| `alertChannels` | not addressed by this ADR — notifications remain entirely missing per TASK 0 |
| `emergencyStop.{armed,lastTriggeredAt}` | Kill Switch state within `SystemHealthGate`/`NewTradeAuthority` |

Status: mapping **FROZEN** as a target; no field in this table is wired to a real backend by this
ADR. The Control Center worktree's current mock-only, AUTO-locked UI is compatible with this
mapping and needs no rework to stay compatible — confirmed by inspection, not by instruction to
that worktree's owner.

## 21. Governance blockers

Unchanged from TASK 1A §9: AGENTS.md §0 (Phase 1 prohibits live/demo broker orders), §9 (trading
semantics escalation, pattern evidence ≠ permission), ADR-016 (paper boundary, explicitly
paper-only), ADR-021 (keeps AI outside deterministic entry permission). This ADR does not lift any
of them. A future governance ADR would need to: (a) define narrow, named conditions for lifting §0
for a controlled scope, (b) update AGENTS.md §0 through the repo's own process, (c) trigger the
Security review AGENTS.md §12 already requires. **Not drafted here.**

## 22. Open Quant decisions

Unchanged list from TASK 1A, plus one addition from this task:
- Q-PE1–Q-PE3 (Pattern Engine activation / ADR-024) — blocks section 9.
- Q-V2–Q-V5 (replay validation defaults / ADR-030).
- Q-Q1 (MT5 multi-broker / ADR-025).
- Q-M3/Q-M4/Q-M8 (M30 bias thresholds / ADR-026).
- **New:** break-even/profit-lock/trailing-distance/P&F-vs-structure-precedence/partial-close-ratio/
  time-exit/re-entry-cooldown values (section 15) — all Position policy, none frozen here.
- **New:** how `RiskPolicy.max_total_exposure`/`max_drawdown` apply to `evaluate_reduction()`
  (section 12).
- **New:** News/Social WARN-vs-BLOCK policy and existing-position news policy (section 16).

## 23. Compatibility / migration plan

1. This ADR introduces no runtime wiring, so nothing currently deployed changes behavior.
2. The minimal pure scaffold (`packages/nexora/autonomous_contracts.py`) adds a new,
   unimported-by-anything module; it cannot affect `code_fingerprint()`/checkpoint compatibility
   because nothing in `research/pipeline.py` or `checkpoint_state.py` references it.
3. Future migration order, once Quant/Architect unblock each piece: (a) Pattern ownership
   reconciliation (section 9) first, since Signal Engine's scoring output is an input to almost
   everything else; (b) `TradeIntent`/Risk migration (sections 10/12) next, as a pure-contract
   change with no broker dependency; (c) `SystemHealthGate`/`ExecutionGuard` wiring only once a real
   or mock `BrokerCapabilities` provider exists; (d) `BrokerExecutionAdapter` last, gated on the
   governance change in section 21.

## 24. Parallel implementation plan after freeze

Once Rin ratifies this ADR: Position contract scaffolding (section 15 interfaces) and News/Social
event-envelope design (section 16) can proceed in parallel — no shared-file conflict, matching TASK
0/1A's parallelization findings. Pattern-ownership reconciliation (section 9) should be scheduled
first and alone, since Strategy Router, AI Analysis, and Position all depend on knowing which
pattern source is authoritative. Control Center backend wiring (section 20) should not start until
at least `SystemHealthGate` and `TradingModeGate` have real (even if minimal/offline) providers,
or the UI will have nothing honest to render beyond its current mock state.

## 25. Contract disposition summary

| Contract | Status |
|---|---|
| TradingMode / TradingModeGate | **FROZEN** |
| TradeLifecycle / TradeState | **FROZEN** (table adopted verbatim from TASK 1A/instruction E) |
| TradeIntent / TradeIntentKind | **FROZEN** (shape only); never-increases-exposure invariant is **PROVISIONAL** — not enforceable by the type alone, belongs to the future risk-reduction path (section 10/12) |
| ExitDecision | **FROZEN** (action vocabulary only; policy BLOCKED) |
| RiskDecision ↔ TradeIntent relationship | **PROVISIONAL** (migration path frozen, Quant sign-off pending) |
| ExecutionIntent / ExecutionResult | **FROZEN** (shape only) |
| BrokerExecutionAdapter | **FROZEN** (interface only); **BLOCKED** (governance) |
| BrokerCapabilities | **FROZEN** (shape only); **BLOCKED** (no real provider) |
| SystemHealthGate | **FROZEN** (shape + fail-closed default); **BLOCKED** (no real health sources) |
| NewTradeAuthority | **FROZEN** |
| ExistingPositionAuthority | **FROZEN** (boundary + invariant); reconciliation algorithm **BLOCKED** |
| AIAnalysis authority boundary | **FROZEN**, no exceptions |
| PatternEvidence ownership boundary | **FROZEN** (direction); migration **BLOCKED** on Q-PE1–3 |
| Normalized News/Social event boundary | **PROVISIONAL** (mechanism frozen, envelope shape open) |
| Control Center read-model boundary | **FROZEN** (mapping only, no wiring) |
| PositionSupervisor / ExitDecision interfaces | **FROZEN** (interfaces); Quant values **BLOCKED** |

## 26. Test / failure triage (re-run on this baseline)

```
PYTHONPATH=<worktree>/packages:<worktree>/apps/api \
  python -m pytest -q tests/test_pattern_engine.py::test_pattern_core_has_no_decision_or_runtime_dependencies \
                     tests/test_validation_core.py::test_fresh_process_reproduces_run_id_and_bundle_hashes
```
Result: **2 passed**. Confirms both TASK 1A findings: the pattern-engine isolation failure was
caused solely by the concurrent worktree's uncommitted draft (absent here), and the validation-core
failure is consistent with suite-order-dependent flakiness, not a baseline defect. No unrelated
tests were fixed or touched.

TASK 1B CONTRACT FREEZE READY FOR RIN REVIEW
