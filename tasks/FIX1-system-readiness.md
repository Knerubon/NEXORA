---
task: FIX1
status: in_review
depends_on: []
agents: ["agents/rin/AGENT.md", "agents/architect/AGENT.md", "agents/quant/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md", "agents/security/AGENT.md"]
skills: ["skills/testing/SKILL.md", "skills/backtesting/SKILL.md", "skills/paper-trading/SKILL.md", "skills/postgres/SKILL.md", "skills/security/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/development.md", "docs/decisions/ADR-018-readiness-corrections.md", "docs/research-runtime.md"]
translation_needed: false
---

# FIX1 — Correct system readiness and complete research integration

User-authorized remediation of the 2026-09-18 audit at e16ef34. This is a corrective task, not approval of incomplete P4–P13 delivery. Preserve historical task evidence and record new evidence here.

## Context and scope

Read existing packages/nexora/{risk,paper,backtest,matrix,structure,market_regime,signals,market_data}, apps/api/nexora_api, apps/web/app, tests and scripts: these implement the audited boundaries. Review ADR-008–017 for existing decisions; ADR-018 explicitly records corrections. Root/scoped AGENTS apply.

## Acceptance

- [x] Risk release/retry/day-boundary and account/price validation regressions fixed.
- [x] Paper decisions bound to signal/account/side/symbol/time; recovery does not duplicate fills.
- [x] Backtest consumes verified market events, distinct configured strategies and actual execution prices; no seeded production results.
- [x] Dataset content verification, run identity and entry-delay metric covered by negative/golden tests.
- [x] Shared research pipeline exposes actual engine snapshots/history and honest readiness.
- [x] Durable journal/restart, storage failure and backup/restore validation; PostgreSQL capability and unavailable external evidence distinguished.
- [x] Quality completeness, bounded retention and regime exit hysteresis corrected.
- [x] Dashboard displays actual structure/evidence/results; reconnect resynchronizes authoritative snapshots.
- [x] Existing relevant tests, new regression/integration tests, lint/type/build and diff checks pass.
- [x] Self-review and draft PR; independent review/security sign-off still required. No merge.

## Safety

Research/backtest/local paper only. No live/demo broker orders, credentials in tracked files, public database/MT5, or OX parameter claims. Remote deployment remains disabled until authenticated encrypted infrastructure is actually verified; do not claim that local guards implement remote authentication.

## Execution record

- Implementation: implemented; in_review, not production certified
- Base: e16ef3411adba27eb4b9b917d71bfb467094985f
- Environment: Windows/Python 3.13; lockfile-managed environment. PostgreSQL tools and Docker unavailable on PATH.
- Checks: `.venv/Scripts/python -m pytest -q`: 80 passed, 1 skipped (isolated PostgreSQL unavailable locally), 2 dependency deprecation warnings.
- `.venv/Scripts/ruff check .`: PASS; `.venv/Scripts/mypy`: PASS (81 files); changed Python formatting: PASS.
- `.venv/Scripts/python scripts/recovery_drill.py`: PASS, SQLite backup restored in a fresh process with matching paper/risk hash; PostgreSQL restore/host crash/RPO/RTO not verified.
- `.venv/Scripts/python scripts/smoke_api.py`: PASS; `.tools/Scripts/uv lock --check` and `.tools/Scripts/uv build`: PASS.
- `npm --prefix apps/web run lint`, `typecheck`, `build`: PASS. Build warns about an unrelated parent-directory lockfile outside this repository.
- Browser QA on isolated synthetic data: chart/levels/evidence/results render, paper pause/resume persists, configured research run appears; mobile 390px viewport has no page overflow (table scrolls locally).
- Task graph/metadata/link audit: PASS, 16 manifests, 259 metadata paths, 150 local Markdown links. P1–P4, requirements/architecture and ADR-001–006 blobs preserved against e16ef34.
- `git diff --check`: PASS. PostgreSQL integration is wired to an ephemeral CI service; its result must be recorded separately.
- Review: self-review complete; independent review/security sign-off pending. Draft [PR #13](https://github.com/Knerubon/NEXORA/pull/13); not merged.
- Next action: independent review/security sign-off; close the explicit release gates in docs/research-runtime.md before claiming production readiness.

- CI initial implementation 6fa30f8: [run 35308427199](https://github.com/Knerubon/NEXORA/actions/runs/35308427199) PASS, including real isolated PostgreSQL 18 transaction/durability/conflict integration and web checks. This is not target-host backup/restore evidence.
- Follow-up self-review: release an approved reservation when paused paper execution produces no fill; restart regression passes.
