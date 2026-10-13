# Proposal — Manual OPEN Risk Provenance V1

Status: DRAFT PROPOSAL, revised after Orchestrator integration review (changes requested at
previously reviewed HEAD `6f8436af7c02e569098bef35fbe7ccd7131ab553`) — Architect/Quant decision
required; not an ADR, not frozen, not implemented. This revision corrects two MAJOR findings and
three MINOR findings from that review; no policy approval exists for this revision either.
Wave 2 revision (2026-10-13): further corrected per the independent Agent D review at HEAD
`fdc832e7741a90b71e5e76afd3eed9932687318f` (F1 MAJOR, F2-F4 MINOR, F5 INFO; see section 7) and
refreshed against `origin/main` `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`. Still no policy
approval and no option selected.
Author: Claude E (ARCHITECT/INTEGRATOR design role, per AGENTS.md section 1), acting in a
design-only capacity for this proposal — no code authored, no self-approval claimed
Date: 2026-10-03 (Wave 2 revision 2026-10-13)
Related: [ADR-015](../decisions/ADR-015-risk-engine-policy.md),
[ADR-033](../decisions/ADR-033-autonomous-trading-contracts-v1.md) sections 10/12/15,
[ADR-034](../decisions/ADR-034-execution-contracts-v1.md) sections 1/2/3/9/11,
[ADR-035](../decisions/ADR-035-execution-integration-safety-amendment.md) sections 0/10/11 (OPEN-17),
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

**Correction note (this revision):** the prior revision (HEAD `6f8436a`) understated how much
every option actually changes. No option below leaves the system's current, fully-wired
signal-to-paper-execution semantics completely unchanged. Every option is an explicit
contract-affecting migration at one layer or another; this revision stops claiming otherwise and
instead compares the migrations honestly.

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
  - `reserved_risk = proposal.stop_distance * size` (line 90) — a bare price-distance-times-size
    product. It implicitly assumes `stop_distance` is already expressed in the same monetary/price
    unit the rest of `RiskPolicy` (`max_risk_per_trade`, `max_total_exposure`, `max_daily_loss`,
    `max_drawdown`) is denominated in. No contract-size or unit-conversion step exists in this
    engine at all today. **This formula must not be copied into a manual-order path without an
    explicit Quant decision on monetary unit/contract-size/conversion for manual `stop_distance`
    input** (see section 5, question 6) — a manual-order UI could plausibly let an operator enter
    a stop distance in a different unit than whatever upstream guarantee keeps signal-derived
    `stop_distance` consistent today, and nothing in `RiskEngine` would catch a unit mismatch.
  - **Reject vs. raise — the two code paths are structurally different and must not be
    conflated.** `_validate()` *returns* a plain string reason code (e.g. `"account_mismatch"`,
    `"missing_identity"`, `"invalid_signal"`, `"symbol_mismatch"`, `"stale_or_future_input"`);
    `_evaluate()` turns any non-`None` return into a normal, cached, `action="reject"`
    `RiskDecision` via `self._reject(...)` (confirmed: `account_mismatch` at line 184 is a
    `_validate` return, consumed at the `if error is not None: return self._reject(...)` branch —
    it is **never** raised as an exception). Separately, `RiskEngine` *raises* `RiskInputError` for
    a distinct set of conditions that are treated as programming/input-contract violations rather
    than policy outcomes: `proposal_identity_conflict` (line 55), `invalid_requested_size` (line
    79), `invalid_realized_pnl` (line 143), `release_identity_conflict` (line 146), and
    `timezone_required` (line 180, inside `_validate` itself, before any reject-string path is
    reached). A manual-path sibling (`evaluate_manual_open()`, section 3) would need to preserve
    this exact same reject-vs-raise split for its own equivalent checks — e.g. a missing/invalid
    `manual_request_id` identity is a raise-shaped condition, while a currency/quality/price
    failure is a reject-shaped `RiskDecision`, mirroring today's exact boundary rather than
    inventing a new one.
  - Idempotency/replay cache keys on `proposal.proposal_id` (caller-supplied), comparing full
    dataclass equality (`self._proposals[proposal.proposal_id] != proposal`) to detect
    identity conflicts. **Corrected (Wave 2, F3):** only decisions that pass `_validate()` are
    recorded. A `_validate()` reject (`missing_identity`, `account_mismatch`, `invalid_signal`,
    `symbol_mismatch`, `invalid_account_or_size`, `invalid_exposure`, `invalid_price`,
    `stale_or_future_input`, `out_of_order_day`) returns `self._reject(...)` **without**
    `_cache_decision()` and before `self._proposals[...]` is written (`risk/engine.py:57-61`), so
    the same `proposal_id` can later be resubmitted with different contents and is evaluated as
    new, with no `proposal_identity_conflict`. Rejects produced after validation (kill switch,
    currency, quality, price, stop distance, sizing, exposure, loss, drawdown) are cached. The
    discipline is reusable in shape, but this uncached-reject behaviour is a policy property a
    manual path would inherit, not a settled guarantee (section 4.2, question 4).
  - `RiskEngine._account_id` pins the instance to the first account seen; a second account raises
    `account_mismatch`... **correction:** re-checked directly — `account_mismatch` is a
    `_validate()` *return* value, not a raise (see reject-vs-raise note above); it produces a
    rejected `RiskDecision`, the proposal is never silently accepted under a different account.
- `packages/nexora/risk/replay.py`: `replay_signals_with_risk()` only replays `ResearchSignal`s
  against P10 backtest output. There is no "manual order dataset" to replay; manual orders are
  live operator actions, not backtest fixtures.
- `packages/nexora/autonomous/authority.py` `NewTradeAuthority.evaluate_manual()` (ADR-034 section
  3/9, already implemented and frozen): takes `config`, `health`, `risk_decision: RiskDecision` —
  no `entry_readiness` parameter, by design (`ManualOrigin` never claims one). It reads only
  `risk_decision.action`. It does **not** read `.signal_id` — the authority layer itself has no
  opinion on how `signal_id` was populated. **Corrected (Wave 2, F1):** it also reads no other
  `RiskDecision` field, so nothing on the manual path binds the decision to the order it
  approves (section 4.7). Under Option A its *signature* can stay unchanged, but that does not
  mean the manual path needs no authority/guard/pipeline change: a binding check is still
  required somewhere (section 4.7). The broader prior claim that "authority needs zero change
  under any option" remains withdrawn (section 2).
- **`packages/nexora/paper/simulator.py::PaperSimulator.apply_decision()` — a consumer this
  revision did not previously surface with enough weight.** Its signature requires `decision:
  RiskDecision` **and a separate, mandatory `signal: ResearchSignal` parameter** (line ~75-80). It
  computes a `binding_error` (lines 90-100) that **actively cross-checks**
  `decision.signal_id != signal.signal_id`, `decision.signal_hash != signal_fingerprint(signal)`,
  `decision.symbol != signal.symbol`, `decision.side != signal.side`, and
  `decision.account_id != self.account_id`, raising `PaperInputError("invalid_approval_binding_or_time")`
  if any mismatch is found on an `allow` decision. **This directly contradicts the previous
  revision's claim that "nothing joins `RiskDecision.signal_id` back to a signal store" — that
  claim is withdrawn.** `PaperSimulator` is exactly such a consumer, and it requires a genuine
  `ResearchSignal` object as a second argument regardless of what `.signal_id` contains. A manual
  `RiskDecision` cannot reach today's `PaperSimulator.apply_decision()` at all — there is no
  `ResearchSignal` to pass as the second argument, by construction. This is a **third blocker**,
  structurally similar to the `PositionRecord` gap (section 4.6), and is likewise out of this
  task's Risk-layer-only scope, but it means **no option in section 3 is sufficient by itself** to
  let a manual OPEN actually execute in paper mode — each would additionally need its own
  `PaperSimulator` manual-binding path (not designed here).
- `packages/nexora/autonomous/risk_migration.py`: `RiskReductionProposal`/`RiskReductionDecision`
  (ADR-033 section 12, ADR-034 section 2) already establish and freeze the precedent Option B
  (section 3) would reuse: a **second, narrower, sibling proposal/decision pair**, added
  **alongside** the existing `RiskProposal`/`RiskDecision`/`RiskEngine.evaluate()` without changing
  any of their current shape or behavior. `RiskReductionDecision` is a **new type with no
  `signal_id` field at all** — not a widened `RiskDecision`.
- `packages/nexora/autonomous_contracts.py` (`ManualOrigin`, defined at line ~105; consumed by
  `packages/nexora/execution/idempotency.py`): `ManualOrigin.manual_request_id` is already named,
  in ADR-034 section 1, as "stable identity: dedup/idempotency/journal/replay anchor" and is reused
  verbatim as `origin_ref` in `build_execution_request()`. It is the natural identity anchor for a
  manual Risk proposal too, if one is introduced — not a new identity concept. **It is not,
  however, a `ResearchSignal.signal_id` and must never be placed in a field whose contract meaning
  is "identifies a real `ResearchSignal`"** — see section 3, Option A3 (now rejected, not an
  available option).
- `packages/nexora/position/models.py`: `PositionRecord.__post_init__` requires non-empty
  `source_signal_decision_ref` and `source_entry_readiness_ref`; `open_position_from_signal()` is
  the only constructor and hard-requires a `SignalDecision` with `invalidation_price`/`targets`.
  **This is a separate, unresolved, downstream blocker** (section 4.6) — out of this task's
  owned-path scope, already tracked on the Wave 1 board as Track F's blocker, and this revision
  does not resolve it either.

## 2. Problem restated precisely (corrected)

`NewTradeAuthority.evaluate_manual()` is already implemented correctly for its own narrow job: it
asks only for *an* `allow` `RiskDecision`, with no opinion on provenance, and its signature needs
no change under Option A. **That is a true, narrow claim about one function's signature.** It is
not a claim that the manual path is safe as wired: `evaluate_manual()` checks only
`risk_decision.action == "allow"`, so any `allow` decision satisfies it (section 4.7, Wave 2 F1). The previous revision
over-extended it into "the gap is entirely upstream... authority needs zero change under any
option" — that broader claim is false and is withdrawn here, per the integration review (MAJOR 2).
It is false specifically for **Option B** (section 3): if the manual path's output is a *new*
sibling decision type rather than `RiskDecision` itself, `evaluate_manual()`'s already-frozen
signature (`risk_decision: RiskDecision`, ADR-034 section 3/9) would itself need to change —
either by widening its parameter type or by adding a second authority method — and that is an
edit to a FROZEN contract requiring Architect review and an ADR update (AGENTS.md section 3), not
a cost-free migration. Section 3 now compares Option A and Option B as two genuinely available,
honestly-costed migrations, not as "Option A is free, Option B is forbidden."

Three non-options remain explicitly excluded by the task brief and by symmetry with ADR-033
section 10/ADR-034 section 1's existing "never a synthetic ResearchSignal" rule — and, per the
integration review, a fourth is added to this excluded list (Option A3, see section 3):

- Fabricating a fake `ResearchSignal` to satisfy `RiskProposal.signal`.
- Fabricating a fake `signal_id` string to satisfy `RiskDecision.signal_id` once a decision exists
  via some other path.
- Bypassing `RiskEngine.evaluate()`/`RiskGuard` entirely for manual orders.
- Reusing `ManualOrigin.manual_request_id`'s value, verbatim or otherwise, as `RiskDecision.
  signal_id` (Option A3) — see section 3 for why this is now classified as excluded, not merely
  "needs sign-off."

## 3. Options

**Honest framing (corrected):** no option below is a zero-impact, purely-additive change relative
to the system's current, fully-wired semantics. `RiskDecision.signal_id` is not just a typed `str`
field — `PaperSimulator.apply_decision()` (section 1) actively verifies it against a real
`ResearchSignal.signal_id`. That means the field's *current, enforced contract meaning* is "the
identity of a real `ResearchSignal` this decision was approved against, checked at execution
time," not merely "a non-empty string." Every option below either changes that meaning (requiring
ADR-governed migration and a consumer check of everything that reads `.signal_id`, not just a
"does the dataclass still type-check" review), or avoids changing it by introducing a new type
(requiring a change to `evaluate_manual()`'s frozen signature instead). There is no third way that
touches nothing.

### Option A — `ManualRiskProposal` + `RiskEngine.evaluate_manual_open()`, output stays `RiskDecision`

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
daily loss, drawdown, kill switch, idempotent proposal-id caching, account-scope pinning, and the
exact reject-vs-raise split documented in section 1), using `requested_at`/`symbol`/`side`/
`account`/`price` in place of the `ResearchSignal` fields, with no signal-ordering checks because a
manual order has no such phases. The monetary-unit caveat on `reserved_risk = stop_distance * size`
(section 1) applies identically here and is **not** resolved by this option — it is a Quant
decision regardless of which `signal_id` sub-option is chosen.

**The remaining question this option does not answer by itself:** `RiskDecision.signal_id` is
still a required `str`, and — per the corrected framing above — its current enforced meaning is
"a real `ResearchSignal.signal_id`, checked by `PaperSimulator`." Sub-options:

- **A1 — populate `signal_id` with the literal empty string `""`.**
  **Corrected classification (was "zero-diff," per MAJOR 1 this is false):** this is **not** a
  zero-contract-change option. `""` is type-legal (the dataclass has no non-empty validation on
  this field), but it changes the field's *required semantic meaning* from "always identifies a
  real signal, verified at execution time" to "may be absent, meaning no signal backs this
  decision." That is a genuine semantic contract change to an ADR-015-governed type, even though
  no line of `RiskDecision`'s dataclass definition needs to change. It requires: (a) an explicit
  ADR-015/ADR-034 amendment or recorded architecture-review extension (AGENTS.md section 3 — a
  semantic contract change is a contract change regardless of whether the Python type signature
  moves), and (b) an explicit audit of every `.signal_id` consumer against the new "may be empty"
  meaning — most pressingly `PaperSimulator.apply_decision()` (section 1), which would need its
  own manual-aware binding path before it could accept such a decision at all, since it has no
  branch today for "this decision legitimately has no backing signal." A1 is listed as the
  **smallest line-level diff** among the signal_id sub-options, not as a free one.
- **A2 — add one additive field to `RiskDecision`, e.g. `origin_kind: Literal["signal", "manual"]
  = "signal"`.** Structurally additive with a safe default, so every *existing* Python call site
  that only passes/reads the fields it already knows about keeps working. **Corrected (per MAJOR
  1): this revision does not claim "every existing journal row/replay fixture is unaffected"
  without migration evidence, and withdraws that claim.** Concretely: `risk/repository.py::
  append_decision()` serializes via `asdict(decision)`; adding a field changes the serialized JSON
  shape of every *newly written* row (one new key appears), and any already-written historical
  journal row lacks that key entirely — a future reader of old rows would need an explicit
  default/migration rule for the missing key, not an assumption that `asdict()` output is
  shape-stable across the change. Whether any **golden/fixture test** asserts an exact serialized
  `RiskDecision` shape was checked only in `tests/test_risk.py` (no such golden-shape assertion
  found there — it round-trips through `replay_decisions()`, not a hard-coded JSON fixture); a
  repo-wide check for golden/checkpoint-hash fixtures elsewhere (e.g. validation/checkpoint bundle
  hash tests) was **not performed** in this docs-only task and is left as an explicit open
  verification item, not asserted either way. This is still a change to an ADR-015-governed
  contract's shape and needs an explicit ADR amendment before implementation (AGENTS.md section
  3), exactly as A1 does — it is not a lower-governance-cost alternative to A1, only a
  different-shaped one (type-level discriminator vs. convention-based emptiness).
- **A3 — reuse `signal_id` to carry `manual_request_id`'s value. REJECTED — not an available
  option, per the integration review (MAJOR 1); relabeled from the prior revision's "needs Quant
  sign-off."** The prior revision's framing ("the value itself is real, not invented... a
  naming/governance risk, not a fabrication of data") is wrong and is withdrawn. Per section 1's
  corrected evidence, `signal_id`'s enforced contract meaning is "the identity of a genuine
  `ResearchSignal`, checked by `PaperSimulator.apply_decision()` against that signal's own
  `.signal_id`." A `manual_request_id` is not a signal id — it does not identify any
  `ResearchSignal`, confirmed, satisfied, or otherwise. Placing it in a field whose contract is
  "this is a real signal's id" **is** fabricating a signal identity in every sense the task brief
  and ADR-033 section 10/ADR-034 section 1 prohibit, regardless of whether the string value happens
  to be a genuine identifier of *something else*. A real ID of the wrong kind of thing, placed in a
  slot that asserts it is the right kind of thing, is exactly what "fabricated signal_id" means
  here. **This option is removed from the comparison table below** — it is not carried forward as
  a candidate requiring further sign-off.

**Compatibility, corrected:** `RiskProposal`, `RiskEngine.evaluate()`, and
`NewTradeAuthority.evaluate_manual()` keep their exact current Python signatures under both A1 and
A2 — no existing function signature changes. That is **narrower** than "zero compatibility
impact": A1 changes a frozen type's *enforced semantic meaning* without changing its *signature*;
A2 changes the type's *shape* (one additive field) without changing its *signature*. Both still
require ADR-governed sign-off (AGENTS.md section 3) before implementation; neither is a loophole
that avoids that requirement. Unchanged signatures also do not remove the decision-to-intent
binding work in section 4.7: under A1 and A2 a manual `RiskDecision` has no field carrying
`manual_request_id` at all (A1 additionally leaves `signal_id == ""`), so the binding check must
compare symbol/side/account/size/expiry and cannot tie the decision to one specific manual
request unless a field is added.

### Option B — sibling decision type `ManualOpenRiskDecision` (no `signal_id` field at all), mirroring `RiskReductionDecision`

```text
ManualOpenRiskDecision {
  decision_id, proposal_id, manual_request_id: str
  action: DecisionAction               # reused "allow" | "reject"
  reason, reason_codes, approved_size, reserved_risk, effective_time,
  policy_version, source_refs, account_id, symbol, side, expires_at   # same fields as RiskDecision,
                                                                       # minus signal_id/signal_hash
}
```

**Honest comparison, corrected (per MAJOR 2 — this option is compared on its merits, not
dismissed merely because a migration touches a frozen signature):**

| | Option A (A1 or A2) | Option B |
|---|---|---|
| `RiskDecision` shape/semantics | Changed (A1: meaning; A2: shape) | Unchanged — new sibling type carries no `signal_id` at all, structurally impossible to fabricate |
| `NewTradeAuthority.evaluate_manual()` signature | Signature unchanged, but a decision-to-intent binding check is still required (section 4.7) | Must change — widen to accept `RiskDecision \| ManualOpenRiskDecision`, or add a second authority method; either requires amending a contract ADR-034 section 3/9 already froze |
| `authorize_trade_intent()` dispatch | Unchanged | Must change — needs to route a `ManualOpenRiskDecision` to the (possibly new) authority entry point |
| `PaperSimulator` consumer gap (section 1) | Still unresolved either way — out of scope for this proposal regardless of option | Still unresolved either way |
| Governance path | ADR-015/ADR-034 amendment for the Risk-layer type | ADR-034 amendment for the Authority-layer frozen signature, in addition to a Risk-layer ADR for the new sibling type |
| Decision ↔ manual request binding (section 4.7) | No field identifies the manual request; binding limited to symbol/side/account/size/expiry unless a field is added | `manual_request_id` is in the type; a binding check is still required |
| Type-safety against fabrication | Depends on audit discipline (A1: empty-string convention; A2: discriminator field) | Structurally guaranteed — no `signal_id` field exists to misuse, same guarantee `RiskReductionDecision` already has |

Both options require an explicit ADR-governed decision before implementation; neither is
"smaller" in every dimension. Option B has a strictly larger number of frozen-contract touch
points (Risk layer *and* Authority layer) but offers a stronger structural (type-level, not
convention-level) guarantee against ever re-fabricating a signal identity. Option A confines the
change to one layer (Risk) but relies on discipline (A1) or an additional field consumers must
learn to check (A2) rather than the type system itself. **This is an engineering tradeoff
observation, not a trading-policy recommendation** — which axis to prioritize (fewer frozen-contract
touch points vs. stronger structural guarantee) is an Architect call, not resolved here.

### Option C — widen `RiskProposal.signal` to accept a manual substitute object satisfying a `ResearchSignal`-shaped protocol

Rejected outright, not developed further: structurally identical to fabricating a `ResearchSignal`
to satisfy `RiskEngine`'s field accesses, which the task brief and ADR-033 section 10/ADR-034
section 1 both forbid by the same reasoning applied to `PositionOrigin`.

## 4. Cross-cutting implications (apply regardless of which Option-A sub-choice or Option B is eventually selected)

### 4.1 TTL / freshness

`RiskPolicy.approval_ttl_seconds`/`max_input_age_seconds` would be reused as-is under either
option — `requested_at` (Option A) or its equivalent (Option B) takes over the role
`signal.decision_time` plays today for staleness comparison and `expires_at` computation. **Open
Quant question:** should a manual order's approval TTL reuse the exact same
`approval_ttl_seconds` value as a signal-derived entry, or does a human operator looking at a
panel before clicking BUY warrant a distinct TTL policy field? Not decided here.

### 4.2 Idempotency / proposal identity — namespacing is not a complete guarantee by itself

`RiskEngine._evaluate()`'s existing idempotency discipline (cache on `proposal.proposal_id`,
raise `proposal_identity_conflict` if a non-identical proposal reuses the id of a cached decision)
is proposal-shape-agnostic in form. **Corrected (Wave 2, F3):** it is not complete as a
guarantee. `_validate()` rejects are neither cached nor recorded (section 1), so after, for
example, a `stale_or_future_input` reject, a manual retry under the same
`manual-open:{manual_request_id}` id with changed contents is evaluated as a new proposal.
Whether that is the intended operator-retry behaviour for Manual OPEN, or whether manual
validation rejects must be cached so a request id cannot be re-evaluated with different
contents, is a policy choice added to question 4, not assumed here. `ManualOrigin.manual_request_id` is the natural identity
anchor for a manual proposal's `proposal_id`. **Corrected (per MINOR finding):** the prior
revision's recommendation to namespace it (e.g. `f"manual-open:{manual_request_id}"`, mirroring
`replay.py`'s `f"replay-{index}:{signal.signal_id}"`) only prevents id-space collisions **if every
proposal-id-producing path in a shared `RiskEngine` instance applies namespacing consistently** —
the signal path, the manual path, and any future path alike. Namespacing one path is not a
guarantee against collision with another path that does not namespace, or that namespaces
differently. This is stated here as a design requirement to verify at implementation time, not as
an already-proven property of "namespacing" as a general technique. **Open question, not decided
here:** should the Risk-layer `proposal_id` format be centrally specified (e.g. a single prefix
registry/convention document) so every current and future proposal-identity source is guaranteed
disjoint, rather than relying on each call site independently choosing a non-colliding prefix?

### 4.3 Account / reservation scope — shared-budget safety implication, not resolved here

`RiskEngine._account_id` pins one engine instance to one account after its first proposal.
**Explicit safety implication, not previously stated as plainly (per MINOR finding):** if a
manual-order path and the signal-derived path were ever given **separate** `RiskEngine` instances
for what is actually the same underlying account — for example, out of a desire to isolate Manual
Order Test Harness traffic from backtest/paper accounting — each instance would independently
track its own `_state.reserved_exposure`/`daily_pnl`/`equity_peak` against the **same**
`RiskPolicy.max_total_exposure`/`max_daily_loss`/`max_drawdown` limits, with neither instance aware
of the other's reservations. That would let the manual path and the signal path **each**
independently consume up to the **full** policy budget, for a combined real exposure up to double
the intended limit — a genuine safety hazard, not a theoretical one. This proposal does **not**
resolve whether manual and signal proposals must share one `RiskEngine` instance (closing this gap
by construction) or may use separate instances with some other, not-yet-designed
cross-instance reservation reconciliation. **Open question for Architect/Quant**, stated now with
its exact failure mode rather than left implicit.

### 4.4 Replay

`replay.py`'s `replay_signals_with_risk()` replays a fixed `ResearchSignal` dataset from P10 —
there is no equivalent "manual order dataset" to replay against, because manual orders are live
operator actions, not backtest fixtures. Manual-order audit trail is a journal/idempotency concern
(ADR-034 section 6), not a backtest-replay concern. Stated as an explicit scope boundary, not a
dropped requirement.

### 4.5 Stop-size / monetary unit / contract size — Quant decision, not an engineering default

As noted in section 1, `reserved_risk = proposal.stop_distance * size` carries an implicit,
undocumented unit assumption today (whatever keeps signal-derived `stop_distance` consistent with
`RiskPolicy`'s currency-denominated limits). This formula, and the sizing/quantization logic built
on it (`max_size_by_trade = policy.max_risk_per_trade / proposal.stop_distance`), **must not be
copied verbatim into a manual-order path** without an explicit Quant decision on: what unit a
manual operator's `stop_distance` input is in, whether any contract-size/instrument-specific
conversion is required before the formula applies, and how that reconciles with the eventual
`BrokerCapabilities`/broker-unit-conversion layer (ADR-033 section 14, still blocked on a real
provider). This is listed as its own open question (section 5) rather than assumed identical to
the signal path.

### 4.6 PaperSimulator consumer gap (new in this revision, section 1) and PositionRecord provenance (downstream, out of this task's scope)

Two separate, unresolved downstream blockers exist beyond this task's Risk-layer-only scope:

- **`PaperSimulator.apply_decision()`** requires a real `ResearchSignal` as a second, mandatory
  argument and actively cross-checks `.signal_id`/`.signal_hash`/`.symbol`/`.side` against it
  (section 1). No option in section 3 makes a manual `RiskDecision` consumable by today's
  `PaperSimulator` — a manual-aware binding path would be a separate, additional design question,
  not addressed here.
- **`PositionRecord`/`open_position_from_signal()`** (`packages/nexora/position/models.py`)
  hard-require a `SignalDecision`-sourced `source_signal_decision_ref`/
  `source_entry_readiness_ref`. Already tracked as Track F's blocker on the Wave 1 coordination
  board; `position/models.py` is outside this task's two owned paths.

Both are recorded here only so a future Architect decision on sections 3's options does not
accidentally assume either downstream gap is already closed — approving a Risk-layer option does
**not**, by itself, make a manual OPEN executable end-to-end.

### 4.7 Decision-to-intent binding on the manual path (Wave 2, F1 MAJOR)

Verified on `origin/main` `36c90c5`:

- `NewTradeAuthority.evaluate_manual()` (`packages/nexora/autonomous/authority.py`, the
  `if risk_decision.action != "allow"` check) reads only `risk_decision.action`.
- `authorize_trade_intent()` forwards whatever `RiskDecision` the caller passes to
  `evaluate_manual()` for a `ManualOrigin` OPEN intent.
- `ExecutionPipeline._step2b_authority` (`packages/nexora/execution/pipeline.py`) passes
  `inputs.risk_decision` straight through; `_step3_guard` calls `evaluate_execution_guard()`
  without the decision. No pipeline, guard or authority code compares a `RiskDecision`'s
  `symbol`, `side`, `account_id`, `approved_size`, `expires_at` or proposal identity with the
  `TradeIntent`, `ManualOrigin` or the resolved order quantity.
- The only place this binding exists today is `PaperSimulator.apply_decision()`
  (`packages/nexora/paper/simulator.py:90-102`), on the signal path, which a manual order cannot
  use (section 4.6).

**Failure mode:** any `allow` `RiskDecision` (one issued for another symbol or side, one already
expired, or one approved for a smaller size than the order) satisfies Manual OPEN authority. Risk
approval would then be necessary but not specific to the order it approves, which is a
verdict-reuse path around Risk Guard. Today this is latent, not live: Manual OPEN cannot reach
transmission (ADR-035 section 10 keeps Track E blocked; `ExecutionPreflight` is DENY-only;
production composition keeps `transmission=None`).

**Requirement every option must meet before implementation (not a selection between options):**
the design must state where, and against which fields, a manual `RiskDecision` is bound to the
order: at minimum `symbol`, `side`, `account_id`, requested/resolved quantity versus
`approved_size`, and `expires_at` versus the evaluation time, plus a manual-request identity
where the decision type can carry one. The binding point (authority, execution guard or
pipeline) and whether the same binding is also added to the signal-derived OPEN path, which has
the same authority-level gap outside `PaperSimulator`, are Architect decisions recorded as
question 8. This overlaps ADR-035 OPEN-17 (origin of the OPEN quantity, section 11), which must
be decided consistently with it.

## 5. QUANT DECISION REQUIRED

The following must be decided by Quant/Architect before any implementation proceeds. None are
decided or defaulted by this proposal (AGENTS.md section 9):

1. **Which Risk-layer migration resolves the `RiskDecision.signal_id` gap for manual OPEN: A1
   (empty-string convention, smallest line-level diff, requires `PaperSimulator` consumer
   rework before use), A2 (additive `origin_kind` discriminator, requires serialization/migration
   verification), or Option B (new sibling decision type, requires amending the already-frozen
   `evaluate_manual()` signature too)?** No engineering recommendation is offered on this specific
   choice in this revision — each sub-option's actual cost depends on governance priorities
   (fewest frozen-contract touch points vs. strongest structural guarantee) that are not this
   role's to weigh. Option A3 is **not** a candidate — see section 3, rejected outright.
2. **Does a manual order's Risk approval TTL reuse `RiskPolicy.approval_ttl_seconds` as-is, or
   does Manual OPEN need its own TTL policy field** (section 4.1)?
3. **What exact fail-closed checks apply to a manual proposal that have no `ResearchSignal`
   equivalent** — e.g., a minimum/maximum age between `ManualOrigin.requested_at` and the actual
   Risk-evaluation call?
4. **What exact `proposal_id` format/namespace convention applies across every current and future
   Risk-layer proposal-identity source**, so namespacing actually guarantees disjointness rather
   than relying on each call site independently avoiding collision (section 4.2)? And must a
   manual proposal's `_validate()`-equivalent rejects be cached, so a `manual_request_id` cannot
   be re-evaluated with different contents after a validation reject, or is an uncached retry
   the intended behaviour (section 4.2, Wave 2 F3)?
5. **Must manual-derived and signal-derived Risk proposals share one `RiskEngine` instance (and
   therefore one reservation/exposure/daily-loss/drawdown budget), or is a separate-instance model
   with explicit cross-instance reconciliation required** (section 4.3's double-budget-consumption
   hazard)?
6. **What monetary unit, contract-size, and conversion policy applies to a manual order's
   `stop_distance`/size before `reserved_risk = stop_distance * size` (or its manual-path
   equivalent) is computed** (section 4.5)? This must be consistent with ADR-035 OPEN-17 (origin
   of the OPEN quantity), which is still open on `origin/main` `36c90c5`.
7. **Does the chosen Risk-layer option require an ADR amendment to ADR-015 and/or ADR-034, or can
   it proceed as a recorded architecture-review extension under existing ADR-015 acceptance** —
   given AGENTS.md section 3's rule that frozen contracts/formulas change only via ADR update?
   Recommended reading, non-binding: every sub-option in section 3 (A1, A2, B) changes either the
   enforced meaning or the shape or a frozen signature of an ADR-governed contract, so this
   revision's working assumption is that **all** of them need at least a recorded architecture
   review, and probably an ADR amendment — not that any of them is exempt. This is still an
   Architect call. Governance state on `origin/main` `36c90c5`: ADR-034 is **accepted / FROZEN**
   (contract shapes and boundaries), so an amendment to its section 1/3/9 contracts is an ADR
   change, not a review note; ADR-035 is PROPOSED and records Track E (this proposal) as BLOCKED
   and independent (ADR-035 sections 0 and 10).
8. **Where is a manual `RiskDecision` bound to the order it approves (authority, execution guard
   or pipeline), against which fields, and does the same binding apply to the signal-derived
   OPEN path** (section 4.7, Wave 2 F1)? No option in section 3 may be implemented until this is
   answered.

## 6. What this proposal explicitly does not do

- Does not implement `ManualRiskProposal`, `evaluate_manual_open()`, or any new decision type.
- Does not modify `packages/nexora/risk/*`, `packages/nexora/autonomous/*`,
  `packages/nexora/position/*`, `packages/nexora/paper/*`, or any other production code.
- Does not amend ADR-015 or ADR-034. ADR-034 is already accepted/FROZEN on main (PR #50); this
  proposal adds no acceptance, freeze or amendment for this topic.
- Does not pick A1 vs A2 vs B — that is the Quant/Architect decision in section 5. Does not treat
  A3 as a live option requiring sign-off — it is excluded outright (section 3).
- Does not claim any option is "zero compatibility impact" — every option is an explicit,
  ADR-governed migration at some layer (section 3's honest framing).
- Does not resolve the `PaperSimulator` consumer gap or the `PositionRecord` provenance gap
  (section 4.6) — both flagged as dependencies for future work, not addressed here.
- Does not choose where the decision-to-intent binding of section 4.7 lives; it records the
  requirement and the open question (question 8).

## 7. Revision record — Wave 2 (independent review findings at `fdc832e`)

| Finding | Severity | Resolution in this revision |
|---|---|---|
| F1: manual `RiskDecision` not bound to the manual intent; "evaluate_manual() unchanged" overstated | MAJOR | Section 4.7 added with evidence and failure mode; section 1/2 wording and the Option A row of the comparison table corrected; question 8 added. No option selected |
| F2: stale against current main (ADR-034 accepted, ADR-035 added, OPEN-17) | MINOR | Branch updated with `origin/main` `36c90c5`; ADR-035 referenced; ADR-034 status stated as accepted/FROZEN (question 7); OPEN-17 linked from question 6 and section 4.7; task file context refreshed |
| F3: uncached `_validate()` rejects not stated | MINOR | Stated in section 1 and section 4.2; added to question 4 |
| F4: wrong cross-reference (question 7 for the unit question) | MINOR | Corrected to question 6 |
| F5: `ManualOrigin` location | INFO | Corrected to `packages/nexora/autonomous_contracts.py` |
| F6: A2 repo-wide fixture check still open | INFO | Unchanged; remains an open verification item for whoever evaluates A2 |
