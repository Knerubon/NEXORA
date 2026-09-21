---
task: ENV1
status: in_review
depends_on: []
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/development.md", "docs/environment-isolation.md"]
translation_needed: false
---

# Environment Isolation V1

## Assignment and baseline

User-authorized DEV/PROD isolation; no main changes, merge, tags, PR21 or engine semantics.
Base commit: `f5f388f` (merged PR22 realtime fix). Main `130f260` already includes PR21;
this branch intentionally starts before PR21 as requested. This dependency/baseline was
reported before work. No branch mixing or PR21 edits were performed.
Worktree: `D:/NEXORA/NEXORA-DEV`; branch `codex/environment-isolation-v1`.

Inputs: source-of-truth documents above; root/scoped AGENTS; API, web configuration/UI/tests;
packages/nexora/storage.py (read-only boundary inspection); tests, scripts, pyproject.toml,
.gitignore; installed Next.js environment/distDir/Turbopack-root guides. Additional files
created for environment profiles, examples and documentation are within this assignment.
Dependencies inspected in actual source: runtime journal, feed configuration, frontend
build-time URL, startup lifecycle, existing CORS/origin checks and research import CLI.

## Implementation / acceptance

- One profile JSON supplies DEV 3000/8000 and PROD 3100/8100; loopback only.
- Own environment file, code worktree, runtime root, storage/state/checkpoints/log/cache.
- Canonical paths, hardlink rejection, code/root ownership markers, populated-root rejection.
- PostgreSQL read-only identity check before journal initialization; separate DB/roles required.
- Launcher records PID/creation time/cwd/command and stops only that verified process tree.
  Lock serializes launcher operations; occupied ports fail without killing any listener.
- DEV uses Next dev; PROD build/start with environment-bound build ID and no hot reload.
- Visible DEV/PROD badge and profile-derived HTTP/WebSocket endpoints; cross-origin rejection.
- Research CLI uses guarded storage and config paths. Tests use disposable roots.
- No core engine, strategy, realtime quote, risk, paper or Experience logic changes.
- Main, source-of-truth documents and existing runtime files/processes left unchanged.

## Execution record

Validation from DEV worktree unless stated otherwise:

- `.venv/Scripts/python -m pytest -q`: PASS, 155 passed, 2 skipped; skips are isolated
  PostgreSQL DSN unavailable and Windows symlink privilege unavailable. Existing two
  Starlette/httpx deprecation warnings. No live feed/production DB used by tests.
- `.venv/Scripts/python -m pytest -q tests/test_environment.py`: PASS, 20 passed, 1 skipped.
  Includes separate profiles/paths, reverse/nested ownership, existing unowned data,
  hardlinks, invalid listeners, environment-file selection, HTTP origin separation,
  mocked PostgreSQL identity, actual owned-process stop and concurrent HTTP API processes
  with separate SQLite journals on ephemeral test ports.
- `.venv/Scripts/ruff check .`: PASS.
- `.venv/Scripts/mypy`: PASS (92 files).
- `npm --prefix apps/web test`: PASS (27 tests); existing Node module-type and SVG title warnings.
- `npm --prefix apps/web run lint`: PASS.
- `npm --prefix apps/web run typecheck`: PASS.
- `npm --prefix apps/web run build`: PASS, DEV-labelled optimized artifact; no deployment.
- `D:/NEXORA/NEXORA/.tools/Scripts/uv lock --check` and `uv build`: PASS; wheel contains profile JSON.
- `.\scripts\nexora.ps1 -Environment development -Action describe`: PASS, DEV ownership/root.
- `.\scripts\nexora.ps1 -Environment development -Action start`: EXPECTED REFUSAL
  (WinError 10048), existing legacy ports occupied; no process registry or child created.
- Isolated source/dependency copy in `%TEMP%/nexora-env1-prod-gwcz2m3g`:
  `.\scripts\nexora.ps1 -Environment production -Action build`, `start`, `stop`: PASS.
  `/health` on 8100 returned production; `/config` accepted origin 3100; web 3100 HTTP 200
  with rendered PROD badge and production-prefixed BUILD_ID. Fresh empty journal, no
  pipeline/market-data configuration. No live trading or real Experience validation claimed.
- During that smoke, all four loopback listeners 3000/8000/3100/8100 coexisted. Stopping
  the new PROD test removed only 3100/8100; original PIDs 26752/4808 on 3000/8000 stayed.
  Full NEW DEV+NEW PROD launcher pair on default ports remains not_run because legacy
  listeners occupy DEV ports. Concurrent isolated APIs and default-port profiles are tested.
- `git diff --check`: PASS; requirements/architecture and packages/nexora diff against
  base is empty; stable main working tree remains clean.

Self-review found and corrected missing-Origin mutation rejection during origin separation;
existing regression now passes. Shared-profile build required explicit Turbopack worktree
root and now passes. No independent reviewer approval is claimed.

## Handoff

status: in_review
from: developer (self-review)
to: Rin
commit: see branch HEAD / draft PR
changed_files: API environment/launcher/boundary; web environment/profile badge/config;
PowerShell launcher and research CLI guard; config templates; environment tests and
existing test isolation/health contract; development/isolation docs; dependencies/lock.
decisions: centralized profile + owner markers + fail-closed legacy/PG adoption; no migration.
risks: markers are not an OS ACL boundary; separate accounts/ACLs need operator setup;
PostgreSQL target-host role enforcement and full default-port NEW DEV/PROD pair pending;
startup recovery remains synchronous and is isolated rather than optimized; checkpoint
paths reserved, no checkpoint implementation added; PR21 integration requires Rin review.
blockers: no local code/test failure; deployment/migration and independent review pending.
next_action: Rin reviews draft diff/baseline and migration plan before any stable deployment.
No merge, tags, automatic migration or production source update.

- Implementation commit: `927e647`.
- Draft review: [PR #23](https://github.com/Knerubon/NEXORA/pull/23).
  Independent Rin review pending; no merge, tags or deployment.
