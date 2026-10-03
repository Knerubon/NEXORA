# Proposal — Manual OPEN Risk Provenance V1

Status: DRAFT PROPOSAL — Architect/Quant decision required; not an ADR, not frozen, not implemented
Author: Claude E (ARCHITECT/INTEGRATOR design role, per AGENTS.md section 1), acting in a
design-only capacity for this proposal — no code authored, no self-approval claimed
Date: 2026-10-03
Related: [ADR-015](../decisions/ADR-015-risk-engine-policy.md),
[ADR-033](../decisions/ADR-033-autonomous-trading-contracts-v1.md) sections 10/12/15,
[ADR-034](../decisions/ADR-034-execution-contracts-v1.md) sections 1/2/9/11,
[P11 task](../../tasks/P11-risk-engine.md), [MANUAL-EXEC-1](../../tasks/MANUAL-EXEC-1-manual-execution-ui.md)
Task: [RISK-MANUAL-OPEN-1](../../tasks/RISK-MANUAL-OPEN-1.md)

## 0. Scope and discipline

This document proposes options to close the **open question ADR-034 section 1 explicitly left
unresolved**: how a manual OPEN's `RiskDecision` is produced when the order has no
`ResearchSignal` behind it. It freezes nothing. It does not implement a path. It does not pick a
policy. It is scoped strictly to the two owned paths of this task:
`tasks/RISK-MANUAL-OPEN-1.md` and this file. No production code is touched by this task.

Per the task brief: the smallest compatible change must let a Manual OPEN obtain a valid
`RiskDecision` **without** a fabricated `signal_id`, a fabricated `ResearchSignal`, or a fabricated
`signal_decision_ref`/`entry_readiness_ref`, and **without** bypassing `RiskEngine`/`RiskGuard`.
`RiskProposal` requiring a `ResearchSignal`, `RiskDecision.signal_id` being a required `str`, and
`NewTradeAuthority.evaluate_manual()` requiring an already-produced `RiskDecision` are all treated
as given and preserved, unless an explicit future ADR authorizes a migration away from them.

## 1. Confirmed current state (inspection evidence)

- `packages/nexora/risk/models.py`:
  - `RiskProposal.signal: ResearchSignal` — required, not `Optional`. No alternate constructor.
  - `RiskDecision.signal_id: str` — required, not `Optional`, no default.
  - `signal_fingerprint(signal: ResearchSignal) -> str` hashes `asdict(signal)` — it is a
    `ResearchSignal`-shaped function, not a generic proposal hash.
- `packages/nexora/risk/engine.py` (`RiskEngine._validate`/`_evaluate`):
  - Reads `proposal.signal.decision_time`, `.confirmation_time`, `.occurrence_time`, `.status`,
    `.side`, `.symbol`, `.source_refs` directly. Timezone-awareness, ordering
    (`occurrence_time <= confirmation_time <= decision_time`), and `status == "active"` are all
    `ResearchSignal`-shaped checks with no manual equivalent today.
  - Staleness (`stale_or_future_input`) compares `signal.decision_time` against
    `account.observed_at`/`price.observed_at` using `policy.max_input_age_seconds`.
  - `RiskDecision.effective_time = proposal.signal.decision_time`;
    `expires_at = signal.decision_time + policy.approval_ttl_seconds`. Both derive from the signal,
    not from a decision-call wall-clock.
  - Idempotency/replay cache keys on `proposal.proposal_id` (caller-supplied), comparing full
    dataclass equality (`self._proposals[proposal.proposal_id] != proposal`) to detect
    identity conflicts — this discipline is proposal-shape-agnostic and already reusable.
  - `RiskEngine._account_id` pins the instance to the first account seen; a second account raises
    `account_mismatch`. One engine instance == one account scope, already true today, not a new
    manual-specific constraint.
- `packages/nexora/risk/replay.py`: `replay_signals_with_risk()` only replays `ResearchSignal`s
  against P10 backtest output. There is no "manual order dataset" to replay; manual orders are
  live operator actions, not backtest fixtures.
- `packages/nexora/autonomous/authority.py` `NewTradeAuthority.evaluate_manual()` (ADR-034 section
  3/9, already implemented and frozen): takes `config`, `health`, `risk_decision: RiskDecision` —
  no `entry_readiness` parameter, by design (`ManualOrigin` never claims one). It reads only
  `risk_decision.action`. It does **not** read `.signal_id` — the authority layer has no opinion on
  how `signal_id` was populated; the gap is entirely upstream of it, inside
  `RiskProposal`/`RiskEngine.evaluate()`.
- `packages/nexora/autonomous/risk_migration.py`: `RiskReductionProposal`/`RiskReductionDecision`
  (ADR-033 section 12, ADR-034 section 2) already establish and freeze the precedent this proposal
  reuses: a **second, narrower, sibling proposal/decision pair**, added **alongside** the existing
  `RiskProposal`/`RiskDecision`/`RiskEngine.evaluate()` without changing any of their current shape
  or behavior. `RiskReductionDecision` is a **new type with no `signal_id` field at all** — not a
  widened `RiskDecision`. This is the direct, already-accepted answer to "how do we avoid faking
  `signal_id` for a non-signal action," applied there to REDUCE/CLOSE/MODIFY_PROTECTION. This
  proposal asks whether the same pattern, or a narrower variant of it, should resolve the
  symmetric OPEN-side gap.
- `packages/nexora/execution/idempotency.py`: `ManualOrigin.manual_request_id` is already named,
  in ADR-034 section 1, as "stable identity: dedup/idempotency/journal/replay anchor" and is reused
  verbatim as `origin_ref` in `build_execution_request()`. It is the natural identity anchor for a
  manual Risk proposal too, if one is introduced — not a new identity concept.
- `packages/nexora/position/models.py`: `PositionRecord.__post_init__` requires non-empty
  `source_signal_decision_ref` and `source_entry_readiness_ref`; `open_position_from_signal()` is
  the only constructor and hard-requires a `SignalDecision` with `invalidation_price`/`targets`.
  **This is a second, separate, downstream blocker**: even a successful manual-OPEN `RiskDecision`
  + `AuthorityDecision.allowed` cannot yet produce a conforming `PositionRecord`. This gap already
  appears on the Wave 1 coordination board as Track F's blocker ("F cannot fabricate signal/
  readiness references to represent a manual position... needs architecture decision with E").
  It is **out of this task's owned-path scope** (`position/models.py` is not a file this task may
  edit) but is flagged here explicitly as a dependency this proposal does not resolve.

## 2. Problem restated precisely

`NewTradeAuthority.evaluate_manual()` is already correctly designed: it asks only for *an*
`allow` `RiskDecision`, with no opinion on provenance. The actual obstacle is one layer below —
`RiskEngine` has exactly one way to produce a `RiskDecision`: `evaluate(RiskProposal)`, and
`RiskProposal.signal` is a required `ResearchSignal`. A manual order has no `ResearchSignal`. Three
non-options are explicitly excluded by the task brief and by symmetry with ADR-033 section 10/
ADR-034 section 1's existing "never a synthetic ResearchSignal" rule:

- Fabricating a fake `ResearchSignal` to satisfy `RiskProposal.signal`.
- Fabricating a fake `signal_id` string to satisfy `RiskDecision.signal_id` once a decision exists
  via some other path.
- Bypassing `RiskEngine.evaluate()`/`RiskGuard` entirely for manual orders (would violate AGENTS.md
  section 9's "pattern/signal evidence is never automatic permission" principle applied to risk
  authorization, and the task brief's explicit "do not bypass Risk Guard").

## 3. Options

### Option A — `ManualRiskProposal` + `RiskEngine.evaluate_manual_open()`, output stays `RiskDecision` unchanged in shape

A new, narrower proposal type, sibling to `RiskProposal` (same pattern as
`RiskReductionProposal` being a sibling to `RiskProposal`, ADR-033 section 12 point 1):

```text
ManualRiskProposal {
  proposal_id: str                  # Risk-layer identity; recommend namespacing, see 4.2
  manual_request_id: str            # ManualOrigin.manual_request_id, carried through verbatim
  operator_ref: str                 # ManualOrigin.operator_ref, carried through verbatim
  symbol: str
  side: Literal["long", "short"]
  requested_at: datetime (aware)    # takes over the role signal.decision_time plays today
  stop_distance: Decimal
  requested_size: Decimal
  quality_status: str
  account: AccountSnapshot          # reused exactly, unchanged
  price: PriceSnapshot              # reused exactly, unchanged
  source_refs: tuple[str, ...] = () # operator-supplied audit refs; never a fabricated signal ref
}
```

New method, additive, not a modification of `evaluate()`:

```text
RiskEngine.evaluate_manual_open(proposal: ManualRiskProposal) -> RiskDecision
```

It would reimplement the same fail-closed checks `_evaluate()`/`_validate()` already apply
(currency match, quality status, price freshness, stop distance, sizing/quantization, exposure,
daily loss, drawdown, kill switch, idempotent proposal-id caching, account-scope pinning), using
`requested_at`/`symbol`/`side`/`account`/`price` in place of the `ResearchSignal` fields, with no
signal-ordering checks (`occurrence_time <= confirmation_time <= decision_time`) because a manual
order has no such phases.

**The remaining question this option does not answer by itself:** `RiskDecision.signal_id` is
still a required `str`. Sub-options for filling it, none implemented here:

- **A1 — leave `RiskDecision` shape untouched; populate `signal_id` with the literal empty string
  `""`.** This reuses the codebase's own existing "absent" idiom (`RiskDecision.account_id: str =
  ""`, `.symbol: str = ""`, `.side: str = ""` are already optional-via-empty-string defaults on
  this exact type). An empty string is not a plausible-looking fabricated identity — it is
  honestly "no signal" — but it does NOT change `RiskDecision`'s shape at all, so it is the
  smallest possible diff. **Risk:** any future consumer that assumes `signal_id` is always
  resolvable to a real `ResearchSignal` row would need to explicitly check for emptiness; no such
  consumer exists today (confirmed — nothing joins `RiskDecision.signal_id` back to a signal store;
  `risk/repository.py` only journals it opaquely as JSON).
- **A2 — add one additive field to `RiskDecision`, e.g. `origin_kind: Literal["signal", "manual"]
  = "signal"`,** so a consumer can test `origin_kind == "manual"` instead of inferring "no signal"
  from `signal_id == ""`. Strictly additive with a safe default — every existing call site,
  journal row, and replay fixture is unaffected (the default preserves current behavior exactly).
  This is still a change to an ADR-015-frozen contract's shape and would need an explicit ADR
  amendment before implementation (AGENTS.md section 3), even though it is non-breaking.
- **A3 — reuse `signal_id` to literally carry `manual_request_id`'s value** (no shape change, no
  empty string either). Zero-diff on `RiskDecision`. **Risk, flagged explicitly rather than
  decided:** a field named `signal_id` holding a `manual_request_id` value could mislead a future
  reader/consumer into believing it identifies a `ResearchSignal`, even though the value itself is
  real (not invented) — this is a naming/governance risk, not a fabrication of data, but it
  reintroduces exactly the ambiguity ADR-033 section 10/ADR-034 section 1 were written to prevent
  for the symmetric `PositionOrigin`/`ExitDecision` case. Not recommended without an explicit
  Quant/Architect sign-off that this reuse is acceptable.

**Compatibility:** `RiskProposal`, `RiskEngine.evaluate()`, and `NewTradeAuthority.evaluate_manual()`
are **entirely unchanged** under A1/A3 (zero diff to a frozen/accepted contract). Under A2,
`RiskDecision` gains one additive field (non-breaking, but still needs ADR sign-off per AGENTS.md
section 3). `authority.py` needs **no change** in any sub-option, because `evaluate_manual()`
already only reads `.action` — the one thing every sub-option preserves identically.

### Option B — sibling decision type `ManualOpenRiskDecision` (no `signal_id` field at all), mirroring `RiskReductionDecision` exactly

Instead of forcing the manual path through `RiskDecision`'s existing shape, define a second,
narrower decision type with no `signal_id` field — the same resolution ADR-034 section 2 already
applied to the symmetric REDUCE/CLOSE/MODIFY_PROTECTION gap:

```text
ManualOpenRiskDecision {
  decision_id, proposal_id, manual_request_id: str
  action: DecisionAction               # reused "allow" | "reject"
  reason, reason_codes, approved_size, reserved_risk, effective_time,
  policy_version, source_refs, account_id, symbol, side, expires_at   # same fields as RiskDecision,
                                                                       # minus signal_id/signal_hash
}
```

**Compatibility cost, flagged as the deciding factor against this option as "smallest
compatible":** `NewTradeAuthority.evaluate_manual()` is **already implemented and frozen**
(ADR-034 section 3/9) with the signature `risk_decision: RiskDecision`. Introducing
`ManualOpenRiskDecision` as the manual path's actual output would require widening that frozen
signature (e.g. `risk_decision: RiskDecision | ManualOpenRiskDecision`) or adding a second
authority method — either way, a change to an already-FROZEN contract (ADR-034 section 3), which
AGENTS.md section 3 requires routing through Architect review and an ADR update before any
dependent implementation proceeds. This is not forbidden, but it is a larger compatibility
footprint than Option A, and it conflicts with the task brief's explicit instruction to preserve
"authority evaluate_manual requires RiskDecision" unless an explicit future ADR authorizes the
migration. Recorded for completeness; not recommended as the smallest-compatible path.

### Option C — do not create a new proposal/decision path; widen `RiskProposal.signal` to accept a manual substitute object satisfying a `ResearchSignal`-shaped protocol

Rejected outright, not developed further: this is structurally the same as fabricating a
`ResearchSignal` (an object that walks and quacks like one to satisfy `RiskEngine`'s field
accesses), which the task brief and ADR-033 section 10/ADR-034 section 1 both forbid by the same
reasoning applied to `PositionOrigin`. Listed only so the option space is shown as exhausted, not
silently skipped.

## 4. Cross-cutting implications (apply to Option A regardless of A1/A2/A3)

### 4.1 TTL / freshness

`RiskPolicy.approval_ttl_seconds`/`max_input_age_seconds` are reused as-is under Option A —
`requested_at` takes over the role `signal.decision_time` plays today for both staleness
comparison against `account.observed_at`/`price.observed_at` and for computing
`expires_at = requested_at + approval_ttl_seconds`. **Open Quant question:** should a manual
order's approval TTL reuse the exact same `approval_ttl_seconds` value as a signal-derived entry,
or does a human operator looking at a panel before clicking BUY warrant a distinct (likely longer,
or explicitly re-validated-on-click) TTL policy field? Not decided here — flagged for Quant.

### 4.2 Idempotency / proposal identity

`RiskEngine._evaluate()`'s existing idempotency discipline (cache on `proposal.proposal_id`,
reject on identity conflict if a non-identical proposal reuses the same id) is proposal-shape
-agnostic and reusable without change. The open question is only what string to use as
`ManualRiskProposal.proposal_id`. `ManualOrigin.manual_request_id` is already the frozen
dedup/idempotency/journal/replay anchor at the `TradeIntent`/`ExecutionRequest` layer (ADR-034
sections 1/6). Recommend namespacing it at the Risk layer (e.g. `f"manual-open:
{manual_request_id}"`), the same pattern `replay.py` already uses
(`f"replay-{index}:{signal.signal_id}"`) and `close_all.py` uses
(`prefix:position_id`) — this avoids any possible id-space collision inside one `RiskEngine`
instance's `_decisions`/`_proposals` dict between a signal-derived `proposal_id` and a
manual-derived one. **Open question, not decided here:** should the Risk-layer `proposal_id` be
required to equal `manual_request_id` exactly (simpler, more literal reuse of ADR-034's anchor) or
is a derived/namespaced key preferred (safer against collision, consistent with existing
namespacing precedent elsewhere in the codebase)? Flagged for Architect.

### 4.3 Account / reservation scope

No new concern beyond what already exists: one `RiskEngine` instance is pinned to one
`account_id` after its first proposal (`account_mismatch` otherwise). Manual orders presumably
share the same research/paper account and therefore the same engine instance as signal-derived
proposals — reserved exposure, daily loss, and drawdown accumulate across both paths in one
shared `RiskState`, which is almost certainly the *correct* safety behavior (a manual order must
count against the same aggregate budget as everything else), but is stated here as an explicit
assumption, not verified against any requirement document, since no manual-specific account
model exists anywhere in the repo. **Open question:** is a single shared `RiskEngine`
instance/account-scope across signal and manual paths the intended model, or does Manual Order
Test Harness traffic need a separately scoped engine/account to avoid interfering with
backtest/paper budget accounting? Flagged for Architect/Quant.

### 4.4 Replay

`replay.py`'s `replay_signals_with_risk()` replays a fixed `ResearchSignal` dataset from P10 —
there is no equivalent "manual order dataset" to replay against, because manual orders are live
operator actions, not backtest fixtures. This proposal does **not** claim Option A needs its own
replay harness to reach parity with P11's existing replay acceptance criteria; manual-order audit
trail is a journal/idempotency concern (ADR-034 section 6), not a backtest-replay concern.
Flagged so this is an explicit scope statement, not a silently dropped requirement.

### 4.5 PositionRecord provenance (downstream, out of this task's scope, flagged as a dependency)

Per section 1, even a fully-approved manual-OPEN `RiskDecision` + `AuthorityDecision.allowed` does
not yet have anywhere to go: `PositionRecord`/`open_position_from_signal()`
(`packages/nexora/position/models.py`) hard-require a `SignalDecision`-sourced
`source_signal_decision_ref`/`source_entry_readiness_ref`. This proposal does not resolve that gap
— it is Track F's blocker per the Wave 1 coordination board, and `position/models.py` is outside
this task's two owned paths. It is recorded here only so a future Architect decision on Option
A/B does not accidentally assume the position-side gap is already closed.

## 5. QUANT DECISION REQUIRED

The following must be decided by Quant/Architect before any implementation proceeds. None are
decided or defaulted by this proposal (AGENTS.md section 9 — speculative defaults must not become
specification):

1. **Which option (A1 / A2 / A3 / B) resolves the `RiskDecision.signal_id` gap for manual OPEN?**
   Engineering recommendation (non-binding): A1 (empty-string `signal_id`, zero shape change) for
   the smallest diff, or A2 (additive `origin_kind` field) if an explicit, type-level
   signal-vs-manual discriminator is wanted badly enough to justify one additive field needing an
   ADR amendment. B is not recommended — it reopens a contract ADR-034 section 3 already froze.
2. **Does a manual order's Risk approval TTL reuse `RiskPolicy.approval_ttl_seconds` as-is, or
   does Manual OPEN need its own TTL policy field** (section 4.1)?
3. **What exact fail-closed checks apply to a manual proposal that have no `ResearchSignal`
   equivalent** — e.g., is there a minimum/maximum age between `ManualOrigin.requested_at` and the
   actual Risk-evaluation call (today's signal path implicitly trusts `decision_time` as "now
   enough"; a manual operator could sit on an un-submitted panel for an arbitrary time)? Not
   decided here.
4. **Is the Risk-layer `proposal_id` required to equal `ManualOrigin.manual_request_id` exactly,
   or should it be namespaced** (section 4.2)?
5. **Is a single shared `RiskEngine` instance/account scope across signal-derived and
   manual-derived proposals the intended model** (section 4.3), or does Manual Order Test Harness
   traffic need isolation from backtest/paper aggregate budget accounting?
6. **Does resolving this proposal's Option A/B require an ADR amendment to ADR-015 (risk engine
   policy) and/or ADR-034 (section 1), or can it proceed as an additive extension under existing
   ADR-015 acceptance** — given AGENTS.md section 3's rule that frozen contracts/formulas change
   only via ADR update? Recommended reading: at minimum A2/B require an ADR touch (shape change to
   an ADR-015-governed type, or to an ADR-034 Section 3/9-frozen signature); A1/A3 arguably do not
   change any existing type shape and may be implementable as a strict ADR-015 extension — but this
   is an Architect call, not asserted here as settled.

## 6. What this proposal explicitly does not do

- Does not implement `ManualRiskProposal`, `evaluate_manual_open()`, or any new decision type.
- Does not modify `packages/nexora/risk/*`, `packages/nexora/autonomous/*`,
  `packages/nexora/position/*`, or any other production code.
- Does not amend ADR-015 or ADR-034, and does not declare either accepted/frozen for this topic.
- Does not pick A1 vs A2 vs A3 vs B — that is the Quant/Architect decision in section 5.
- Does not resolve the `PositionRecord` provenance gap (section 4.5) — flagged as a dependency for
  Track F and Architect, not addressed here.
