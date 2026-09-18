---
task: FIX2
status: in_review
depends_on: ["tasks/FIX1-system-readiness.md"]
agents: ["agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/security/AGENT.md"]
skills: ["skills/market-data/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/research-runtime.md", "docs/decisions/ADR-019-explicit-feed-time-correction.md"]
translation_needed: false
---

# FIX2 — Explicit MT5 feed timestamp correction

User authorized correcting the observed feed timestamp defect and configuring the local chart. FIX1 merged in PR #13; production certification remains pending. Scope: apps/api/nexora_api/{quotes,research}.py, quote display in apps/web/app/page.tsx, tests, runtime configuration and documentation. No domain formula changes or broker orders. Local preview uses explicitly labelled research-example sizing, no paper execution.

## Acceptance

- Preserve raw source time; configurable feed-specific correction defaults to zero, never inferred automatically.
- Validate configured offset; stale/future checks still apply after correction. Do not replace event time with arrival time.
- Persist raw timestamp/offset provenance in normalized event identity and source labels.
- Offline tests cover UTC default, +3-hour correction, stale/future data, invalid config and replay provenance.
- Verify actual quotes and chart locally; independent review pending, draft PR only.

## Evidence

2026-09-18: three advancing ticks on the user-selected running terminal were 10799.20–10799.54 seconds ahead of local UTC. This supports a temporary explicit 10800-second correction for this feed, not a general broker timezone/DST claim. Terminal/account settings unchanged.

Local verification: 86 passed, 1 PostgreSQL test skipped (service unavailable), two existing dependency deprecation warnings. Ruff and mypy (82 files) pass; web lint/typecheck/build pass; diff check passes. Actual feed status live, recorded events increasing, no processing error, P&F column visible in browser. Paper disabled. Preview uses bid with fixed 0.5/1/2 price-unit boxes, reversal 2, from labelled example assumptions; no strategy validation claim. Self-review only; independent review pending.

## User-requested chart presentation follow-up

User requested Point & Figure X/O cells matching the supplied visual reference. Scope adds apps/web/app/page.tsx and globals.css; use skills/frontend/SKILL.md. Render confirmed transition box prices (including per-transition effective box sizes), no browser trading-rule calculation. White grid, green X/red O, actual confirmed support/resistance bands and floating Fast/Medium/Slow matrix. No inferred BOX 10/20/30 or H1/H4 indicators. Retain real feed status, explicit time correction and local-only paper boundary. Render window capped to 60 columns/2500 cells; history is not fabricated.

Chart follow-up verification: web lint/typecheck/build PASS; browser shows green X and red O per confirmed price, blue confirmed support band, actual Bid/Ask and resolution states. Narrow layout keeps chart scrolling inside its panel; Matrix moves below chart. No fabricated historical columns.
