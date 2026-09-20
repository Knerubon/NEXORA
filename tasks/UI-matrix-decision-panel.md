# Matrix decision panel

Status: in_progress. Base: origin/main 8dd31a256cc654f79d404718973478d7d43a9a59.
Scope: apps/web/app, apps/web/tests; presentation only.
Inputs: docs/requirements.md, docs/architecture.md, docs/development.md,
root/scoped AGENTS.md, tasks/P8B-signal-intelligence.md, apps/web/app,
apps/web/tests, apps/api/nexora_api/main.py and quotes.py,
packages/nexora/signals/models.py and engine.py, packages/nexora/matrix/models.py,
packages/nexora/structure/models.py, packages/nexora/research/runtime.py.
Additional context: installed Next.js CSS guide for required framework guidance;
API and engine sources establish serialization, readiness and evidence polarity.

## Review before implementation
- Clean working tree on codex/signal-intelligence f9f522b; fetched main is expected
  merge 8dd31a2. Trees identical. New branch codex/matrix-decision-panel from main.
- PR #19 already supplies independent nullable strengths/availability, evaluated
  WAIT, unchanged 0-100 score, patterns, entry/invalidation/targets/R:R and evidence.
- Reuse SignalIntelligence and current decision from state (not historical signals).
- Backend evidence additionally supplies component, polarity, points, source refs;
  positive evidence includes BOTH bullish and bearish contributions, so it must
  not all be called support for the selected decision. Negative evidence is caution.
- State provides matrix_status, quote.status/event_time and research_mode. Use
  backend readiness and explicit timestamp, no frontend freshness threshold.
- No dedicated short-term bias contract: defer, don't relabel regime or score gap.
  Box/reversal per-resolution config details deferred; no inferred mappings.
- Baseline rendered localhost:3000 inspected via browser tree and screenshot:
  chart dominates; floating Matrix plus repeated Matrix/Regime below chart;
  setup always expanded. Actual feed stale, recorded SELL score 100, legacy
  strengths unavailable. Preserve honest missing-data display and recorded labels.
- Existing release refs: 1.0.0 c106824590cfd4724582c46390f6110d25509e30;
  1.0.1 tag object 4341113e9ed0d062992390a0cff6712f6480d4e1. Do not alter.

## Acceptance
Compact responsive chart-first panel, independent donuts, backend readiness/feed,
score/action, concise pattern, evidence explainability and native collapsed setup.
Tests for WAIT/null/cooldown/score, evidence polarity and disclosures; web tests,
lint, typecheck/build; final semantic/scope self-review; draft PR, no merge.
Python tests unnecessary unless backend changed. Independent review pending.

## Resume execution record (2026-09-20)
- Preserved and continued the four interrupted frontend edits and this task file.
  Branch codex/matrix-decision-panel; HEAD and freshly fetched origin/main both
  8dd31a2. No staged changes at resume. Existing local edit removed matrix-float;
  restored its chart slot and upgraded it using shared current decision components.
- Owner scopes only: full Matrix analysis cards, deterministic evidence summary,
  recent signals, independent strengths; compact Pointer Events overlay with
  bounded position, localStorage, reset, keyboard movement and mobile docking.
- Additional directly relevant inputs: reference images from owner conversation;
  installed Next CSS guide; matrix-position helpers and page chart regression test.
- Unavailable: dedicated short-term bias and per-resolution box/reversal config.
  No backend expansion, scoring changes, invented data or new dependencies.
- Checks: `npm --prefix apps/web test` PASS (23 tests);
  `npm --prefix apps/web run lint` PASS after fixing hook dependency warning;
  `npm --prefix apps/web run typecheck` PASS;
  `npm --prefix apps/web run build` PASS; `git diff --check` PASS.
  Existing Node module-type and React SVG title warnings remain; no failures.
- Visual verification: Chrome desktop and 390px mobile; shared backend snapshot
  rendered, unavailable strengths honest, overlay docked on mobile. In-app browser
  attach timed out; Chrome fallback succeeded. No Python/backend tests (no changes).
- Self-review: chart SVG unchanged against baseline for empty and adaptive X/O/S&R
  fixtures; all changed application files are frontend UI/tests. No trading,
  feed, risk, execution, paper, backend or release changes.
- Status: in_review; self-review; independent review pending. Draft PR only.

## Review correction and release authorization (2026-09-20)
- Owner authorized fixing the CI blocker, merging after checks pass, and tag 1.1.0.
  This supersedes the earlier draft-only/no-merge/no-tag instructions for this PR.
- Review found web CI failed because the chart regression reads baseline commit
  8dd31a2 but checkout supplied shallow history. Web CI now fetches full history;
  the baseline comparison and all production code remain unchanged.
- Prior independent inspection: no backend, P&F/Matrix calculation, Signal Engine,
  MT5, Risk, Paper or broker execution changes. Local web tests (23), lint,
  typecheck and build passed; Python CI passed 127 tests, Ruff, mypy and recovery.
- Merge remains gated on fresh CI for this correction. Browser pointer/resize
  integration coverage remains a non-blocking follow-up, not a claimed pass.
