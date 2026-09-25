# DRAW1 — Manual Drawing V1 on the P&F Chart

status: in_review
translation_needed: false
role: DEV-CHART (self-review; independent review pending)
base_commit: 816c8d701d9678e2cc78fe46857eb28cf0b19c3f (origin/main, Merge PR #39)
branch: claude/chart-manual-drawing-v1
worktree: D:\NEXORA\NEXORA-MANUAL-DRAWING

## Assignment / context

User request: "Manual Drawing V1 บน P&F Chart + toggle". Lets the viewer draw their own
horizontal lines and trend lines on the live P&F chart, with a toggle to show/hide them.
Sources: [requirements](../docs/requirements.md), [architecture](../docs/architecture.md),
[root workflow](../AGENTS.md) sections 2, 7 and 9, [web AGENTS.md](../apps/web/AGENTS.md).
Builds on the Chart Visual Intelligence V1 overlays (`chart-overlays.tsx`, `overlay-model.ts`).

## Scope decisions

- **User annotation, never evidence.** Drawings are not a pattern, trendline, S/R level or
  signal. They are not sent to the backend, not in the research journal, and not read by
  Entry Readiness, SignalEngine, backtests or the Pattern Engine. They have no BUY/SELL/WAIT
  meaning (AGENTS.md section 9). The popup states this in the UI.
- **No shared contract change.** No API, schema, WebSocket or ADR contract is touched, so no
  Architect contract freeze is required. `TrendlineEngine` (ADR-020) and the pattern overlays
  are unchanged.
- **Storage:** browser `localStorage`, key `nexora:manual-drawings:v1:<symbol>`, per symbol and
  per browser. Memory is primary; if storage is unavailable or throws, drawings work for the
  page session and the toggle shows "not saved". Stored data is validated on read (bad JSON,
  unknown version, invalid or duplicate items are dropped); max 50 drawings per symbol.
- **Anchoring:** data space (`column_id`, price), not pixels. Clicks snap to the P&F cell under
  the pointer (same centre convention as the X/O glyphs and the trendline overlay:
  `cy(p) = y(p) - 13`). Empty slots right of the latest column map to the next column ids
  (P&F `column_id` increases by exactly 1 per column).
- **Tools:** Horizontal line (1 click), Trend line (2 clicks, dashed preview), select a drawing
  to open a popup with Delete, Clear all (confirm). Esc cancels an in-progress tool.
- **Toggle:** the `Drawings (n)` button shows/hides the layer (persisted per symbol). Hidden
  drawings disable the tools. Evidence layer toggles (Trendline / S/R / Patterns) are
  unchanged and independent.
- **Chart regression invariant kept:** with no drawings and no active tool the drawing layer
  renders nothing, so the P&F SVG is byte-identical to before (existing
  `page-chart-regression` tests still pass unchanged).

## Deliverables

- `apps/web/app/manual-drawing-model.ts` — types, validation, edits, snapping, per-symbol store.
- `apps/web/app/manual-drawing.tsx` — `useManualDrawings`, `DrawingControls`, `DrawingLayer`,
  `DrawingPopup`.
- `apps/web/app/page.tsx` — wires the drawing controls, layer and popup into `StructureChart`;
  only one popup (evidence or drawing) open at a time.
- `apps/web/app/chart-overlays.tsx` — exports the existing `place()` popup positioner (reused).
- `apps/web/app/globals.css` — drawing control/hint/delete styles.
- `apps/web/tests/manual-drawing.test.mjs`, `apps/web/tests/overlay-harness.mjs` (injects the
  new chart dependencies).

## Known limitations (V1)

- Trend lines are segments (no ray extension); no drag-to-edit (delete and redraw).
- Drawings do not sync across browsers/devices (browser-only by design).
- Drawings are per symbol, not per box-size/config: after a box-size change they keep their
  column/price anchors.
- Drawings on columns older than the 60-column visible window are clipped, like the overlays.

## Execution record

- 2026-09-25 DEV-CHART implementation on base `816c8d7`.
- `npm --prefix apps/web test` — pass, 82/82 (68 existing + 14 new).
- `npm --prefix apps/web run lint` — pass (0 warnings).
- `npm --prefix apps/web run typecheck` — pass.
- `NEXORA_ENV=development npm --prefix apps/web run build` — pass.
- Browser verification (headless Edge over CDP, `next dev` on 127.0.0.1:3300 against a
  synthetic mock DEV API on 127.0.0.1:8000 serving `tests/overlay-fixture.mjs`; PROD ports
  3100/8100 untouched): 20/20 checks pass — place H-line and trend line, endpoints within
  1.5 px of the glyph centres, alignment kept at 160% zoom, Esc cancels, persistence across
  reload, toggle hide/show persisted, select → popup → delete persisted, evidence popups still
  open, no network request carries drawings.
- Review: self-review; independent review pending.
