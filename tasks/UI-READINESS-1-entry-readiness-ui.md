# UI-READINESS-1 — Entry Readiness UI + Display Toggle

status: in_review
translation_needed: false
base_commit: 816c8d701d9678e2cc78fe46857eb28cf0b19c3f (origin/main, Merge PR #39)
branch: claude/entry-readiness-ui-v1
worktree: D:\NEXORA\NEXORA-ENTRY-READINESS-UI
role: DEV-B (frontend presentation), self-review; independent review pending (Rin)

## Assignment / context

Presentation-only exposure of the existing backend Entry Readiness result in the trading UI,
plus a session-only display toggle. Sources: [requirements](../docs/requirements.md),
[architecture](../docs/architecture.md), [root workflow](../AGENTS.md),
[web AGENTS](../apps/web/AGENTS.md),
[ADR-021](../docs/decisions/ADR-021-entry-readiness-v1.md) (accepted, frozen contract),
[UI-DECISION-1](./UI-DECISION-1-signal-summary.md) (toggle/presentation precedent).

No backend, Python, API, Entry Readiness, Trendline, P&F/Pattern, Signal, Risk or Experience
change. If one becomes necessary: stop with `UI-READINESS-1 SCOPE EXPANSION REQUIRED`.

## Inspection evidence (Phase 1, base 816c8d7)

- Backend source: `packages/nexora/entry_readiness/derive.py::evaluate_entry_readiness`, pure
  function of `(SignalDecision, TrendlineSnapshot, config_version)`.
- Wiring: `packages/nexora/research/pipeline.py` sets `self._output["entry_readiness"]`;
  `snapshot()` returns `canonical_serialize(self._output)` (dataclass → object, tuple → array).
- API path: `ResearchRuntime.snapshot()["output"]` → `apps/api/nexora_api/main.py::state_payload`
  `research.output.entry_readiness`, delivered by `GET /state` and the WS `state_snapshot`
  payload (`/ws/events`). No API change needed.
- Absent/null is a real case: recovered/older journal rows may carry no or `null`
  `entry_readiness` (`tests/test_experience_replay_equivalence.py`).
- Frontend before this task: no reference to `entry_readiness` in `apps/web`.

### Frozen backend schema (ADR-021 Decision 8, `schema_version: 1`)

```text
entry_readiness: {
  schema_version: 1
  symbol: string
  state: "READY" | "DEVELOPING" | "NOT_READY" | "BLOCKED"
  signal_action: "BUY" | "SELL" | "WAIT"
  blockers: [{ code, side, trendline_kind, line_id, reason }]
  pending_confirmations: [{ code, side, trendline_kind, line_id, reason }]
  config_version: string
}
```

Codes (ADR-021 Decisions 6–7): blockers `aligned_trendline_broken`,
`aligned_trendline_retest_held`; pending `aligned_trendline_retest_pending`. `reason` is
backend English text.

## Frozen presentation decisions

- Location: new isolated component `apps/web/app/entry-readiness.tsx`, mounted in
  `SignalIntelligence` `strength-panel` directly after the existing Decision / Signal Score
  line and its note. Not in the floating Matrix card (avoids enlarging the overlay on the
  P&F chart). `page.tsx` only adds the `Output.entry_readiness` field and passes it through.
- Rendering (valid payload): `State {state}` verbatim canonical value (`NOT_READY` stays
  `NOT_READY`) · `Signal {signal_action}` verbatim; each blocker as `Blocker` + backend
  `reason` + `code` (with `line_id` in `title`); each pending confirmation as `Pending` +
  backend `reason` + `code`; `config {config_version}`. Colour class derived 1:1 from the
  backend state string only.
- Validation is shape-only: `schema_version === 1`, state/action in the frozen enums, string
  fields, arrays of objects with string fields. It never repairs, defaults, reorders or
  re-derives a field, and never checks ADR-021 invariants against Signal/Trendline.
- Unavailable:
  - absent / `null` → `State Unavailable · Not reported by backend`.
  - malformed / unsupported (unknown state, other `schema_version`, wrong types) →
    `State Unavailable · Unsupported backend payload — not interpreted`. No blocker/pending
    list is shown and no state is inferred.
- Stale: reuse the existing Signal Intelligence convention
  (`researchMode !== "live_observation" || connectionError` → last-received data remains
  visible and is labelled). The block keeps the backend state and adds a `Last received`
  label; it never becomes a fresh conclusion.
- Toggle: button in the Entry Readiness header, `aria-pressed`, visible text `ON`/`OFF`,
  constant accessible name `Entry Readiness display`. Default ON. OFF hides only the Entry
  Readiness body; header + toggle remain so it can be re-enabled. State is React `useState`
  only (session only): no localStorage, sessionStorage, cookie, API, DB or config.
- Independence: toggle does not touch Decision, Bias, Signal Score, Matrix, Trendline/S/R/
  Pattern chart layers, Manual Drawing or backend data; chart layer toggles do not touch it.

## Acceptance tests (apps/web/tests/entry-readiness.test.mjs)

1–4 valid state / as-is / blockers / pending · 5 absent → Unavailable · 6 malformed → no
inference · 7–8 toggle ON/OFF · 9 OFF does not mutate data · 10–11 OFF keeps Decision/Bias/
Signal Score · 12 trendline/P&F independent · 13 rest of Signal Intelligence identical to base ·
14 stale label · 15–16 desktop/mobile CSS · 17 no readiness derivation in source · 18 no
persistence/API for toggle.

## Cross-lane

DRAW-1 (`claude/chart-manual-drawing-v1`, `D:\NEXORA\NEXORA-MANUAL-DRAWING`): no commits over
816c8d7; uncommitted work appeared during this task in `page.tsx`, `globals.css`,
`chart-overlays.tsx`, `tests/overlay-harness.mjs` (+ new `manual-drawing*` files).

- `apps/web/app/page.tsx`: DRAW-1 hunks are imports + inside `StructureChart` (base lines
  10, 94–135); UI-READINESS-1 hunks are the `Output` type (line 25) and the `Home`
  `<SignalIntelligence>` call (line 321). Disjoint; non-material.
- `apps/web/app/globals.css`: DRAW-1 appends at EOF; this block is placed after the
  `.wait-context` rules (line 147) to avoid an EOF append conflict. Distinct selectors.
- No shared component, state or contract. Entry Readiness does not read chart layers or
  drawings; `entry-readiness.test.mjs` asserts `StructureChart` never references it.

## Execution record

Self-review; independent review pending (Rin). Base 816c8d7 (origin/main unchanged at final
check). Implementation commit 568fda9. Not pushed, no PR, no merge.

- Files: `apps/web/app/entry-readiness.tsx` (new), `apps/web/app/signal-intelligence.tsx`,
  `apps/web/app/page.tsx`, `apps/web/app/globals.css`, `apps/web/tests/entry-readiness.test.mjs`
  (new), `apps/web/tests/entry-readiness-harness.mjs` (new),
  `apps/web/tests/signal-intelligence.test.mjs` and `decision-signal-summary.test.mjs`
  (resolver shim for `./entry-readiness`; the UI-DECISION-1 full-panel guard now strips only
  the Entry Readiness block before comparing with its baseline).
- `node --experimental-strip-types --test tests/entry-readiness.test.mjs` (in `apps/web`):
  pass 14/14.
- Signal Intelligence + UI-DECISION-1 tests: pass 26/26. Page/chart/overlay/structure/matrix
  position tests: pass 27/27.
- `npm test`: pass 82/82 · `npm run lint`: pass · `npm run typecheck`: pass ·
  `npm run build`: pass · `git diff --check`: clean.
- Python/backend: not_run. No Python, API or backend file changed.
- Not verified in a live browser against a running backend: layout covered by CSS and markup
  assertions only.
