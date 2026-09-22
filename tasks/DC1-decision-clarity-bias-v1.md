# Decision Clarity + Bias V1

status: in_review
translation_needed: false
base_commit: ee1d88d00f5ef71efa11e8459b9b68b18c9db25b (origin/main, includes PR #23 Environment Isolation V1)

## Assignment / context

User-authorized UI clarity task: make `Decision WAIT · Bias: Unavailable` understandable
without inventing trading semantics in the frontend. Sources:
[requirements](../docs/requirements.md), [architecture](../docs/architecture.md),
[root workflow](../AGENTS.md), [roadmap](../docs/roadmap.md).

Additional context authorized before inspection:
- `packages/nexora/signals/{models,engine}.py`, `packages/nexora/matrix/models.py`,
  `packages/nexora/market_regime/models.py`: existing decision, matrix and regime contracts.
- `apps/api/nexora_api/main.py`: `/state` response assembly and WebSocket broadcast.
- `apps/web/app/{signal-intelligence.tsx,page.tsx,matrix-float.tsx}`: existing panel rendering.
- `tests/test_signals.py`, `apps/web/tests/signal-intelligence.test.mjs`: existing fixtures
  and assertions, reused directly where possible.

## Problem

`apps/web/app/signal-intelligence.tsx` hard-coded `Bias: Unavailable` and
`Short-term Bias / Unavailable / No short-term bias supplied.` regardless of backend state,
because no dedicated bias contract existed. This hid useful evidence already present in the
Matrix (e.g. FAST X, MEDIUM X, SLOW O) and in the Signal Engine's existing WAIT evidence.

## Scope / boundaries

Added a new, additive, read-only backend contract (`DecisionContext`) and rendered it in
the frontend. Did **not** touch `packages/nexora/signals/engine.py` or `models.py`, P&F,
Adaptive Box, Structure, Matrix, Market Regime, Risk, Paper, Backtest, Experience, MT5,
realtime feed, Environment Isolation, or the separate gateway work. Confirmed by diff:
only `apps/api/nexora_api/main.py`, `apps/web/app/{signal-intelligence.tsx,page.tsx,globals.css}`,
`packages/nexora/signals/__init__.py`, and new/updated tests changed.

## Deterministic bias semantics

`packages/nexora/signals/decision_context.py`, `derive_decision_context(decision, matrix)`:
purely derived, never persisted, never journaled, computed fresh from an existing
`SignalDecision` and `MatrixSnapshot` at `/state` response time. Not part of the Signal
Engine's decision rules, not stored in the journal, not read back by replay.

Bias is derived from each Matrix resolution's raw `direction` (`X`/`O`/`none`), not the
coarser `MatrixSnapshot.alignment` enum, because that enum collapses "no evidence" and
"some resolutions still warming up while others already agree" into the same
`"unavailable"` value — using it directly would have made LEAN cases wrongly report
UNAVAILABLE.

Given `total` = configured resolutions, `bullish`/`bearish` = count with direction `X`/`O`:

| Condition | Bias | Alignment (aligned/total) |
|---|---|---|
| `total == 0` or `bullish + bearish == 0` | `UNAVAILABLE` | `0/total` |
| `bullish == total` | `BULLISH` | `bullish/total` |
| `bearish == total` | `BEARISH` | `bearish/total` |
| `bullish > bearish` | `BULLISH_LEAN` | `bullish/total` |
| `bearish > bullish` | `BEARISH_LEAN` | `bearish/total` |
| `bullish == bearish` (both > 0) | `MIXED` | `bullish/total` |

`UNAVAILABLE` is reserved for zero known directions (no matrix, or every resolution still
warming up). Disagreement — even a tie — is `MIXED`, never `UNAVAILABLE`. A 2-of-3 lean is
`*_LEAN`, never suppressed to `UNAVAILABLE` merely because the matrix isn't fully warmed up.

## State semantics implemented vs deferred

Only states provable from a single snapshot are implemented:

- `CONFIRMED`: bias is `BULLISH` and decision action is `BUY`, or bias is `BEARISH` and
  action is `SELL` — full matrix alignment and the engine's own decision agree.
- `DEVELOPING`: bias has a directional lean (`BULLISH`/`BULLISH_LEAN`/`BEARISH`/`BEARISH_LEAN`)
  but the decision has not (yet) confirmed that direction as BUY/SELL.
- `UNAVAILABLE`: bias is `MIXED` or `UNAVAILABLE` — no coherent direction to characterize.

`ACTIVE`, `WEAKENING`, `INVALIDATED` are **not implemented**. They require tracking a
specific trade's lifecycle across snapshots (has a confirmed setup started, is it losing
strength, was it invalidated), which does not exist yet — no per-signal temporal state is
tracked outside the Signal Engine's own history. Fabricating these labels from a single
snapshot would not be truthful. Deferred to a future **Signal Stability V1** task.

## WAIT semantics ("Why WAIT?" / "Waiting for")

Only populated when `decision.action == "WAIT"`; empty for BUY/SELL.

- `reasons`: `decision.negative_evidence[*].reason` verbatim (e.g. "Matrix disagreement
  detected.", "Signal cooldown window is active.", "Confirmed structure, matrix, or regime
  inputs are unavailable.", "Range regime requires breakout confirmation."). If empty (a WAIT
  reached purely via the score/gap threshold, which the engine does not itself record as
  negative evidence) falls back to one of two honest, existing-evidence-based statements:
  "Evidence gap between BUY and SELL points has not reached the actionable threshold."
  (when there is positive evidence) or "No supporting evidence has been produced yet."
  (when there is none). No new trading thresholds were introduced for this fallback text.
- `waiting_for`: `decision.future_conditions` verbatim — this field already existed for
  exactly this purpose and is reused rather than duplicated.

## Unavailable semantics

Tested explicitly (`tests/test_decision_context.py`): zero known directions → `UNAVAILABLE`;
a tie between known bullish/bearish directions → `MIXED`, not `UNAVAILABLE`; a 2-of-3 lean →
`*_LEAN`, not `UNAVAILABLE`. `UNAVAILABLE` is never used as a substitute for disagreement.

## Experience / replay compatibility

`SignalDecision`, `SignalSnapshot`, `SignalEngine`, and the journal/Experience schema are
byte-for-byte unchanged — `DecisionContext` is computed fresh at the API layer from the
already-reconstructed `matrix`/`decision` on every `/state` call and WebSocket broadcast,
and is never written to the journal or read back by `ResearchRuntime._rebuild()`/replay.
There is nothing for old journal records to be incompatible with, and no new
Experience → decision feedback path was added.

## Tests

Backend (`tests/test_decision_context.py`, 12 tests): all-unavailable; zero-known-directions
unavailable (not mixed); 2-bullish/1-bearish → BULLISH_LEAN with WAIT preserved (task's own
example, run through the real `SignalEngine` for the WAIT-preserved half and through the
pure function directly for the exact bias/alignment split); the mirrored BEARISH_LEAN case;
full bullish alignment → BULLISH/CONFIRMED via a real BUY decision; full bearish alignment →
BEARISH/CONFIRMED via a real SELL decision; a genuine 1X/1O tie → MIXED, not UNAVAILABLE;
cooldown WAIT surfaces the cooldown reason; insufficient-input WAIT (missing structure
pivots) while the matrix itself is fully aligned — shows the honest matrix-derived bias even
though the overall decision is blocked by something else; range-regime WAIT surfaces the
breakout-confirmation reason; determinism across a canonical-serialize/decode round trip
(same as production); additive-only serialized shape. `tests/test_dashboard_api.py`: `/state`
carries a fully-`UNAVAILABLE` `decision_context` when there is no research state yet.

Frontend (`apps/web/tests/signal-intelligence.test.mjs`): bias renders honestly `Unavailable`
when no `decisionContext` prop is supplied even though the raw `matrix` prop shows a clear
`aligned_bearish` reading (proves the frontend does not derive bias itself); bias/state/
alignment render verbatim from a supplied `decisionContext`; "Why WAIT?"/"Waiting for" render
only for WAIT and only when supplied, using backend text verbatim; the floating `MatrixSummary`
widget shows the same honest-default-then-real-value behavior. All pre-existing assertions
that depended on the previous literal hard-coded text were updated to match the new,
still-honest, default rather than deleted.

## Known limitations

- `ACTIVE`/`WEAKENING`/`INVALIDATED` states are deferred to Signal Stability V1 (see above).
- Bias reflects Matrix resolution directions only; it does not incorporate Structure,
  Regime, or Signal evidence polarity, even though those are also valid per Section 5 of
  the assignment. This keeps the contract small, deterministic, and directly testable
  against every example in the assignment; broadening the bias inputs is a candidate for a
  follow-up if the Matrix-only view proves insufficient in practice.
- The FAST/MEDIUM/SLOW resolution rows are unchanged (no arrow glyphs added) to keep this
  diff minimal; only the new Bias/State/Alignment/Why-WAIT block was added.

## Execution record

Commands run in `D:\NEXORA\NEXORA-CLARITY` (worktree, branch `claude/decision-clarity-v1`):
- `.venv\Scripts\python -m pytest -q tests/test_decision_context.py`: 12 passed.
- `.venv\Scripts\python -m pytest -q`: 214 passed, 3 skipped (unchanged skip reasons: no
  local PostgreSQL DSN, Windows symlink privilege unavailable).
- `.venv\Scripts\ruff check .`: pass. `.venv\Scripts\mypy`: pass, 102 source files.
- `npm --prefix apps/web test`: 29 passed. `npm run lint`: pass. `npm run typecheck`: pass.
  `npm run build`: pass.
- `git diff --check`: pass.
- Full diff inspected file-by-file against the regression guard list (P&F, Adaptive Box,
  Structure, Matrix, Regime, Signal scoring/thresholds, Risk, Paper, Backtest, Experience,
  MT5, realtime feed, Environment Isolation, Gateway): no changes outside
  `apps/api/nexora_api/main.py`, `apps/web/app/{signal-intelligence.tsx,page.tsx,globals.css}`,
  `packages/nexora/signals/__init__.py`, new `packages/nexora/signals/decision_context.py`,
  and tests.

Self-review; independent Rin review pending. No merge, deploy, or data migration performed.
No restart of the currently running NEXORA screen/API was needed or performed — all work was
done in the isolated worktree.
