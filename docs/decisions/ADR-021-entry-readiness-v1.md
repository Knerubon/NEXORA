# ADR-021 — Entry Readiness V1 (Deterministic Trendline-Aware Permission Filter)

Status: accepted
Date: 2026-09-23
Related: [ADR-020](./ADR-020-pnf-trendline-v1.md) (Trendline Engine — the sole additional evidence source this ADR consumes), [ADR-011](./ADR-011-signal-evidence-policy.md) (Signal evidence/dedup policy — the field conventions this ADR reuses), [ADR-009](./ADR-009-market-structure-lifecycle.md), [ADR-008](./ADR-008-multi-resolution-matrix.md)
Sources: `packages/nexora/signals/{models,engine,decision_context}.py`, `packages/nexora/trendline/{models,engine}.py`, `packages/nexora/research/pipeline.py`, `packages/nexora/experience/engine.py`

## Context

NEXORA's `SignalEngine` already produces a deterministic `SignalDecision.action ∈ {BUY, SELL, WAIT}`. Entry Readiness V1 answers a narrower, downstream question only: *given an already-existing BUY/SELL Signal decision, does the current deterministic Trendline state contradict it, leave it pending, or leave it uncontradicted?* It consumes exactly one additional evidence source — the Trendline Engine merged in Phase 1 (ADR-020) — and nothing else.

This ADR incorporates Rin's final architecture/quant review of the initial design draft and freezes every previously open question.

## Decision 1 — Core principle: Direction ≠ Signal ≠ Entry Readiness

Three distinct, already-or-newly-deterministic concepts, never collapsed into one:

- **Direction** — a Matrix-derived lean (`decision_context.Bias`, existing code, untouched by this ADR).
- **Signal** — `SignalDecision.action ∈ {BUY, SELL, WAIT}`, the sole upstream trading-permission authority (`packages/nexora/signals/engine.py`).
- **Entry Readiness** — a **permission filter**, not a permission source. It can only preserve or narrow whatever `SignalDecision.action` already granted. It is not a second Signal Engine, not an AI opinion, not a probability, confidence score, or prediction, and it never generates a BUY/SELL action of its own.

## Decision 2 — Hard safety invariant

**`WAIT → READY` is impossible.** If `SignalDecision.action == "WAIT"`, `EntryReadinessState` is always `NOT_READY`, unconditionally, regardless of any Trendline state. No combination of Trendline evidence may upgrade WAIT into any form of entry permission. Trendline can only preserve or reduce readiness for an already-existing BUY/SELL decision — it can never manufacture one.

## Decision 3 — State enum and exact V1 triggers

```python
EntryReadinessState = Literal["READY", "DEVELOPING", "NOT_READY", "BLOCKED"]
```

- **`NOT_READY`** — `action == "WAIT"`. This is its only V1 trigger. WAIT already encodes multiple upstream causes (cooldown, insufficient inputs, future-input rejection, mixed-matrix/range/high-volatility forced WAIT, missing trade setup, sub-threshold score — `packages/nexora/signals/engine.py`); Entry Readiness does not inspect or reproduce any of them, it only reads `action`. `BLOCKED` is never used merely because Signal is WAIT — BLOCKED means a deterministic contradiction of an *existing* BUY/SELL decision, which cannot exist when there is no BUY/SELL decision.
- **`READY`** — `action ∈ {BUY, SELL}`, zero blockers. Does not require a same-kind Trendline to exist or be actively supportive; absence of a Trendline is not a gate (Decision 4). **Invariant: `READY` always carries zero `EntryBlocker` entries and zero `PendingConfirmation` entries** — the decision table (Decision 5) never pairs a `READY` row with the one Trendline state (`retesting`) that produces a pending confirmation.
- **`DEVELOPING`** — `action ∈ {BUY, SELL}`, zero blockers, and the aligned-kind Trendline (Decision 4) is currently `state == "retesting"` — a retest has entered but has not yet resolved to `retest_held`/`retest_failed`. This is the only field in the entire current codebase that represents a genuinely undetermined, not-yet-resolved deterministic fact (`TrendlineLine.state`, `packages/nexora/trendline/models.py`); no other "required confirmation" exists anywhere in NEXORA today, and none is invented here. **Invariant: `DEVELOPING` always carries zero `EntryBlocker` entries and exactly one `PendingConfirmation` entry** (code `aligned_trendline_retest_pending`, Decision 7) — it is the only state the decision table pairs with `retesting`.
- **`BLOCKED`** — `action ∈ {BUY, SELL}` and the aligned-kind Trendline is `broken` or `retest_held`. Always carries at least one structured `EntryBlocker` (Decision 6).

## Decision 4 — Aligned-line-only evaluation (frozen)

`SignalDecision.action` fixes exactly one "aligned" Trendline kind and ignores the other entirely for state purposes:

```
BUY  -> aligned kind = bullish_support,  opposite kind = bearish_resistance
SELL -> aligned kind = bearish_resistance, opposite kind = bullish_support
```

Only `TrendlineSnapshot.active_bullish` / `active_bearish` — whichever one is the aligned kind — is read. The opposite-kind line is **not read at all** by V1's `evaluate_entry_readiness`. This is frozen, not a default that may silently expand:

- Opposite-kind `retesting` does **not** cause `DEVELOPING`.
- Opposite-kind `retest_failed` does **not** cause `BLOCKED`.
- No opposite-kind state of any kind affects `EntryReadinessState` in V1.
- No opposite-kind evidence is surfaced in the output at all (Decision 8 removes the field that would have carried it).

Rationale: Signal's own scoring already weighs support/resistance evidence (`SignalEvidence(component="support_resistance", ...)`, `packages/nexora/signals/models.py`) before ever emitting BUY/SELL — re-reading the opposite-kind line here would duplicate Signal's own judgment rather than filter it, which Decision 1 forbids. A single-line evaluation surface is also the minimum necessary to satisfy the "explainable, independently testable, deterministic" bar for every blocker without introducing cross-line interaction rules that are not yet justified by any concrete failure this system has observed.

**Aligned `retest_failed` is frozen as `READY` because its blocker is cleared, not because it is positive confirmation.** Once the aligned line resolves out of `broken`/`retest_held`, no deterministic contradiction remains, so the filter falls through to its `READY` default (Decision 5). `retest_failed` is not elevated to supportive evidence of any kind — there is no field in the V1 output that could carry such a distinction (Decision 8).

`replaced` is not a reachable value of `active_bullish`/`active_bearish` — only `history` entries ever carry `state == "replaced"` (`packages/nexora/trendline/engine.py`, `_try_form_or_replace`). No table row is needed for it.

## Decision 5 — Frozen V1 decision table

| Signal | Aligned Trendline state | `EntryReadinessState` |
|---|---|---|
| WAIT | any (including none) | `NOT_READY` |
| BUY / SELL | *(none)* | `READY` |
| BUY / SELL | `active` | `READY` |
| BUY / SELL | `broken` | `BLOCKED` |
| BUY / SELL | `retesting` | `DEVELOPING` |
| BUY / SELL | `retest_held` | `BLOCKED` |
| BUY / SELL | `retest_failed` | `READY` |

This table is exhaustive and final for V1. No Cartesian expansion over opposite-kind state is added, per Decision 4.

## Decision 6 — Blocker taxonomy (frozen)

```python
EntryBlockerCode = Literal[
    "aligned_trendline_broken",
    "aligned_trendline_retest_held",
]

@dataclass(frozen=True, slots=True)
class EntryBlocker:
    code: EntryBlockerCode
    side: SignalAction                # "BUY" | "SELL"
    trendline_kind: TrendlineKind     # "bullish_support" | "bearish_resistance"
    line_id: str                      # TrendlineLine.line_id — exact, sufficient evidence reference
    reason: str
```

Exactly these two codes. No other blocker code exists in V1.

`source_refs` is removed: `line_id` alone is already a deterministic, sha256-based, unique reference to the exact `TrendlineLine` (ADR-020), and `TrendlineLine` has no additional transition-identity string beyond `line_id`/`break_transition_id` that a generic `source_refs` tuple would meaningfully add over what `code` + `line_id` + `reason` already provide. `side` and `trendline_kind` are retained even though V1's single-blocker cardinality (Decision 4) makes them derivable from the parent `EntryReadinessSnapshot.signal_action`: a blocker record must remain self-explanatory if read independent of its parent snapshot (logs, Experience context, a future multi-blocker extension).

## Decision 7 — Pending-confirmation taxonomy (frozen)

```python
PendingConfirmationCode = Literal["aligned_trendline_retest_pending"]

@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    code: PendingConfirmationCode
    side: SignalAction
    trendline_kind: TrendlineKind
    line_id: str
    reason: str
```

Exactly this one code. No other pending-confirmation code exists in V1.

## Decision 8 — Data model (frozen; `supportive_evidence` removed)

```python
@dataclass(frozen=True, slots=True)
class EntryReadinessSnapshot:
    schema_version: Literal[1]
    symbol: str
    state: EntryReadinessState
    signal_action: SignalAction
    blockers: tuple[EntryBlocker, ...]
    pending_confirmations: tuple[PendingConfirmation, ...]
    config_version: str
```

A top-level `source_refs` field is not carried: for `BLOCKED`/`DEVELOPING` it would only duplicate what `blockers[*].line_id`/`pending_confirmations[*].line_id` already state; for `READY`/`NOT_READY` there is no deterministic value to put there. Evidence traceability lives entirely on the blocker/pending-confirmation records themselves (Decision 6, Decision 7), never at the snapshot's top level.

`supportive_evidence` — present in the initial draft — is removed entirely. It had no deterministic role in computing `state` (state is fully determined by Decision 5's table) and, once opposite-line evidence is removed (Decision 4) and aligned `retest_failed` is frozen as blocker-clearing rather than positive confirmation (Decision 4), there is no remaining V1 fact that field would carry. A field with no required deterministic role is not carried "for information" — this matches Decision 1's framing of Entry Readiness as a filter, not a narrator.

`blockers` and `pending_confirmations` remain tuples (matching existing repo convention, e.g. `SignalDecision.positive_evidence`) even though V1's aligned-only rule (Decision 4) never produces more than one entry in either — this keeps the shape stable if a later ADR revision adds further aligned-only evidence sources without a breaking type change.

Explicitly and permanently excluded, per the original design brief: win probability, AI confidence, predicted return, target price, lot size, order instruction, and any readiness-specific score (the existing `SignalDecision.score`/`buy_strength`/`sell_strength` are Signal's concern and are not read, duplicated, or re-derived here).

## Decision 9 — Pure-function architecture

```python
def evaluate_entry_readiness(
    *, decision: SignalDecision, trendline: TrendlineSnapshot, config_version: str,
) -> EntryReadinessSnapshot
```

No class, no internal state, no history buffer, no `__init__`. `TrendlineSnapshot.active_bullish`/`active_bearish` already carry all state (Decision 3, 4) needed to evaluate readiness for the current transition; there is nothing left to accumulate independently. This mirrors the existing, already-accepted `derive_decision_context` pattern (`packages/nexora/signals/decision_context.py`) exactly. The function's signature has no parameter through which Experience, AI, or any historical/journal data could enter — Decisions 12 and 13 hold structurally, not by convention alone.

## Decision 10 — Pipeline insertion point

Inside `ResearchPipeline._process()` (`packages/nexora/research/pipeline.py`), immediately after `signals = self.signals.evaluate(...)` and before `self._output = {...}` is assigned:

```python
signals = self.signals.evaluate(structure=structure, regime=regime, matrix=matrix)
entry_readiness = evaluate_entry_readiness(
    decision=signals.decision, trendline=trendline, config_version=self.config.version,
)
self._output = {
    ...,
    "signals": signals,
    "entry_readiness": entry_readiness,
    ...,
}
```

`trendline` here is the same `TrendlineSnapshot` produced earlier in the same `_process()` call, for the same event — Signal and Trendline are guaranteed to correspond to the same causal processed state by construction, satisfying "no future transition may influence current readiness" without any additional bookkeeping.

## Decision 11 — No-lookahead / prefix invariance

Entry Readiness inherits prefix invariance structurally: it is a pure function of two already-prefix-invariant snapshots (Trendline's invariance proven in ADR-020/Phase 1; Signal rejects future-timestamped inputs at `packages/nexora/signals/engine.py`). There is no mutable state in which future information could leak backward. Required test: capture `entry_readiness` at processed step N, continue processing further (future, at capture time) transitions, and assert re-derivation from the *same* `(decision, trendline)` pair recorded at step N reproduces a bit-identical `EntryReadinessSnapshot` — the same pattern already used for Trendline's own prefix-invariance tests.

## Decision 12 — Experience Engine boundary

`packages/nexora/experience/engine.py`'s `freeze()` gains exactly one additive line in its `context` dict, alongside the existing `"trendline"` key:

```python
"entry_readiness": output.get("entry_readiness"),
```

Not added to `fingerprint()`. `plan()`, `measure()`, `advance()`, `eligible()`, `initial_lifecycle()` are untouched. No Experience statistic of any kind feeds back into `evaluate_entry_readiness` — its signature (Decision 9) has no parameter through which that could happen.

## Decision 13 — AI boundary

No AI Analyst exists in NEXORA today. `evaluate_entry_readiness` has zero AI dependency, structurally (Decision 9's signature). A future AI Analyst may read and explain an `EntryReadinessSnapshot` in prose; it may never write to, gate, or otherwise modify it.

## Decision 14 — Out of scope for V1

This ADR is implemented in the same PR (PR #29). Implementation in this PR must not touch: `packages/nexora/signals/` (beyond the one-line pipeline read of `signals.decision`), `packages/nexora/trendline/`, `packages/nexora/pnf/`, `packages/nexora/matrix/`, `packages/nexora/market_regime/`, `packages/nexora/risk/`, `apps/web/`, `apps/api/nexora_api/main.py` (REMOTE1/Tailscale trust code and existing endpoints), REMOTE1 infrastructure, the running PROD instance, or any release tag. No Entry Readiness frontend overlay, no AI Analyst/LLM provider, no auto-trading, no order placement, no position sizing, and no new Signal strategy are implemented under this ADR.

## Consequences

- Entry Readiness never creates trading permission; it can only preserve or narrow an existing `SignalDecision.action ∈ {BUY, SELL}`.
- The V1 evaluation surface is deliberately minimal: exactly one Trendline line (the aligned kind) and exactly one field on it (`state`) determine `EntryReadinessState`. Opposite-line evidence and any supportive/informational field are excluded, not deferred with a placeholder.
- Two blocker codes and one pending-confirmation code fully describe V1's behavior; both are machine-queryable and independently testable, with zero free-text-only identity.
- Because the function is pure and reads only already-computed, already-causal snapshots, Entry Readiness costs O(1) per transition and requires no new stateful engine, no history replay, and no additional persistence.

## Validation expected

Full decision-table coverage (Decision 5) for both BUY and SELL · WAIT → `NOT_READY` regardless of aligned Trendline state, parametrized over all six states · purity (identical inputs ⇒ identical output) · prefix invariance (Decision 11) · every `BLOCKED` result carries ≥1 `EntryBlocker` · every `READY` result carries zero blockers and zero pending confirmations · every `DEVELOPING` result carries exactly one `PendingConfirmation` and zero blockers · absence of an aligned Trendline line does not block (`active_bullish`/`active_bearish is None` ⇒ `READY` for BUY/SELL) · opposite-line state has zero effect on `state` (explicit test constructing a conflicting opposite-line state and asserting no change) · `evaluate_entry_readiness` never mutates or replaces the `SignalDecision` object it is given · Experience `fingerprint()` hash is unchanged when only `entry_readiness` varies.
