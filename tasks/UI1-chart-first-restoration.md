# UI1 — Restore chart-first observation dashboard

Status: in_review
Branch: codex/restore-chart-first-ui
Base: c106824590cfd4724582c46390f6110d25509e30

## Scope and inputs

Presentation only: apps/web/app/page.tsx and apps/web/app/globals.css.
Requirements: docs/requirements.md (FR-07, FR-08).
Architecture: docs/architecture.md (frontend and engine separation).
Additional context: docs/development.md for validation commands; apps/web/AGENTS.md
and bundled Next.js CSS/client component guides for frontend conventions;
e2b0bed / PR #14 and fa562c9 / PR #15 for existing chart and centering behavior.
Visual source: user's NEXORA screenshot retrieved from the referenced conversation.

## Dependency and backup evidence

Initial working tree clean on codex/restore-local-mt5-runtime at a450123.
Fetched origin and created a separate UI branch from origin/main c106824,
which includes the local MT5/feed and signal work through PR #16.
Actual existing backup tag is `1.0.0` (without v), local and remote both
c106824590cfd4724582c46390f6110d25509e30. No `v1.0.0` exists on either side.
No tags created, moved, overwritten or deleted.

## Restored presentation

Reuse existing inline chart from #14 and preserve its current glyph centering,
recorded boxes, S/R and current quote bindings. Compact title/subtitle and quote
lines; symbol/box toolbar; bounded 60–200% zoom (default 120%); latest-price
scrolling; focus mode with Escape; repeated price labels and floating Matrix
symbol corrected. Existing research panels remain below the chart. Mobile
wraps toolbar and places Matrix below the scrollable chart.
No API, backend, configuration, calculation or trading logic changes.

## Execution record

- `npm run lint` (apps/web): PASS.
- `npm run typecheck` (apps/web): PASS.
- `npm test` (apps/web): PASS, 9 tests; existing module-type warning only.
- `npm run build` (apps/web): PASS, static route generated.
- `git diff --check`: PASS.
- Browser runtime http://127.0.0.1:3000 with actual API on 8000: desktop
  screenshot compared at 1112x782 and mobile inspected at 390x844.
- Zoom 120 -> 140 -> 120; Latest price scrolls to latest quote area;
  Focus chart fills viewport; Escape exits: PASS.
- DOM inspection: all 108 displayed glyph centers remain on cell centers.
- Mobile document width equals scroll width (375 CSS px excluding scrollbar);
  Matrix static below chart; toolbar fits: PASS.

## Limits / handoff

Self-review; independent review pending. Open draft PR, do not merge.
Live API currently reports stale / recorded_or_unavailable, box 1, O/O/O,
with -10800s correction and current raw timestamp preserved. Reference shows
live, box 0.5, X/X/X and historical prices: these data differences are retained
rather than changing engine/configuration or fabricating live status.
Existing grid spacing and whole-box price anchoring preserved; screenshot
therefore is not pixel-identical. Existing tests cover geometry/rendering of
the separate legacy chart; runtime DOM check also covers the active page chart.
Next action: independent review of UI diff and live-market visual verification.
