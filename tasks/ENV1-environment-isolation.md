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

User-authorized DEV/PROD isolation; no main changes, merge, tags or domain semantics.
Current base: `130f260` (current main, including PR21/PR22), rebased at Rin's explicit
request. Original base was `f5f388f`; pre-rebase validation below is historical evidence.
Integration preserves Experience implementation unchanged; final Rin approval is pending.
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

## Original execution record (before rebase)

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
paths reserved, no checkpoint implementation added; rebased integration awaits final Rin review.
blockers: no local code/test failure; deployment/migration and independent review pending.
next_action: Rin reviews draft diff/baseline and migration plan before any stable deployment.
No merge, tags, automatic migration or production source update.

- Implementation commit: `927e647`.
- Draft review: [PR #23](https://github.com/Knerubon/NEXORA/pull/23).
  Independent Rin review pending; no merge, tags or deployment.

## Rin integration revision (2026-09-21)

Status: in_review. User now explicitly authorizes rebase onto current main (supersedes
original pre-PR21 baseline constraint). Current main: 130f260. Rebase completed without
conflicts. Additional context: packages/nexora/research/runtime.py and experience/*,
apps/api/nexora_api/quotes.py, existing Experience tests, docs/decisions/ADR-019*,
needed to verify unchanged integration/time semantics and document migration gates.
Deliverables: rerun all checks, current-main domain diff, explicit migration/rollback
checklist and proposed-only Time Semantics task. No deploy, migration, tags or merge.


### Post-integration validation and handoff

Rin accepted the isolation structure in principle; this revision addresses the requested
integration/documentation checks, not final approval. `git rebase origin/main` replayed
both branch commits without conflicts. Rebased implementation: `2fd5512`; no application
logic edits were needed to integrate PR21. Latest documentation commit records this review.

Exact checks after rebase (DEV worktree):
- `.venv/Scripts/python -m pytest -q tests/test_environment.py`: PASS, 20 passed, 1 skipped
  (Windows symlink privilege unavailable); includes separate paths/ownership and concurrent
  isolated API processes with independent storage on ephemeral test ports.
- `.venv/Scripts/python -m pytest -q`: PASS, 202 passed, 3 skipped (two isolated PostgreSQL
  integrations lack a local DSN; one symlink privilege skip); two existing deprecation warnings.
  Includes unchanged Experience causal/horizon/recovery regression tests from main.
- `.venv/Scripts/ruff check .`: PASS.
- `.venv/Scripts/mypy`: PASS, 100 source files.
- `npm --prefix apps/web test`: PASS, 27 tests, existing module-type/SVG-title warnings only.
- `npm --prefix apps/web run lint`: PASS.
- `npm --prefix apps/web run typecheck`: PASS.
- `npm --prefix apps/web run build`: PASS (optimized build labelled DEV).
- `.\scripts\nexora.ps1 -Environment production -Action build`: PASS in the stopped,
  isolated `%TEMP%/nexora-env1-prod-gwcz2m3g` source/dependency copy refreshed with rebased
  code. BUILD ONLY: no PROD runtime start, deployment or stable data/config copied.
- `git diff --exit-code origin/main -- packages/nexora apps/api/nexora_api/quotes.py
  docs/requirements.md docs/architecture.md`: PASS, empty diff. Core P&F/Matrix/Signal/Risk,
  paper/backtest, Experience, runtime, persistence and feed/time-normalization code match main.
- Reviewed complete `git diff origin/main`: API changes are environment config/storage/origin
  guards and identity reporting; UI changes are badge/endpoint configuration; replay CLI only
  guards config/storage paths. No event-time, quote, engine, scoring, outcome or order semantics.
- Resolved both profiles without starting either: DEV web/API 3000/8000, PROD 3100/8100;
  distinct storage, state, checkpoints, logs, cache and PID registries under respective roots.
  Existing tests verify opposite-owner rejection and that stopping one leaves the other alive.
- `git diff --check`: PASS. New document links/context paths checked. Stable main clean.

Added [migration/rollback checklist](../docs/environment-migration-checklist.md): verified
backup and disposable restore, config/MT5/time preservation, complete per-stream counts and
hash manifests (including Experience), common-watermark domain comparisons, PROD readiness,
quote/WebSocket checks, explicit soak gates, retained legacy rollback and post-cutover gap
handling. Every migration checkbox remains unexecuted.

Added [TS1 proposal](TS1-time-semantics-proposal.md): separate approval/contract gate for UTC,
aware event/receipt times, broker normalization, Bangkok display, market/session zones,
DST-safe New York/London and replay/Experience invariance. No TS1 implementation in PR23.

Final review limits: target-host PostgreSQL/ACL checks, full new DEV/PROD default-port
cutover, production data migration, realtime/WS market verification and soak are NOT RUN
in this revision and require later explicit authorization. Earlier empty-runtime smoke is
historical evidence, not proof of an actual migration. Checkpoint paths remain reserved.
No PROD deployment, existing data migration, main merge or tag operations performed.
Next action: Rin final review of PR23 and this evidence; maintain draft/in_review status.

## Independent re-verification for PR #23 final head (2026-09-22)

Status: in_review, unchanged. This section is an independent re-check of the above
evidence from a separate session, using the same DEV worktree and commit `366ba58`
(content-identical rebase of `927e647`/`ccf3e5e` onto current main `130f260`), plus one
fix found only through this re-check.

- Re-ran locally and reproduced the prior session's recorded numbers exactly:
  `pytest -q tests/test_environment.py` (20 passed, 1 skipped), `pytest -q` full suite
  (202 passed, 3 skipped), `ruff check .` (pass), `npm --prefix apps/web test` (27 passed),
  `npm --prefix apps/web run lint` (pass), `npm --prefix apps/web run typecheck` (pass),
  `npm --prefix apps/web run build` (pass), `git diff --exit-code origin/main -- packages/nexora
  apps/api/nexora_api/quotes.py docs/requirements.md docs/architecture.md` (empty diff).
- Checked GitHub Actions for the existing pushed head (`ccf3e5e`/`927e647`) instead of
  trusting the prior local-only record: CI's `python` job failed at the `mypy` step —
  `apps/api/nexora_api/launch.py:165: error: Module has no attribute "CREATE_NO_WINDOW"
  [attr-defined]`. This did not reproduce in the prior session's local Windows mypy run
  (Windows stdlib stubs expose `CREATE_NO_WINDOW`); CI runs on `ubuntu-latest`, where mypy
  resolves the Linux-only `subprocess` stub and the attribute does not exist for that
  platform, regardless of the runtime `os.name == "nt"` guard.
- Fix: `apps/api/nexora_api/launch.py` now uses
  `getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0` instead of the
  direct attribute reference, preserving identical runtime behavior (Windows-only flag,
  `0` elsewhere) while resolving under both platform stubs. Re-ran after the fix: `ruff
  check .` (pass), `mypy` (`Success: no issues found in 100 source files`), full `pytest -q`
  (202 passed, 3 skipped, unchanged), `python scripts/recovery_drill.py` (`PASS: SQLite
  backup and fresh-process paper/risk recovery ...`; `NOT VERIFIED: PostgreSQL backup/restore,
  host crash, RPO/RTO, remote access` — expected, matches CI's own step), `git diff --check`
  (pass). No other diffs required; this was the only CI-reproducing issue found.
- Isolation test (Step 8 of the current assignment): default DEV ports 3000/8000 confirmed
  occupied by the existing legacy listeners (PIDs observed via `netstat`) at the time of this
  check, so no live default-port launcher pair was started, per instruction not to force that
  test. `tests/test_environment.py::test_two_live_api_processes_keep_separate_storage` already
  provides equivalent real-process evidence on ephemeral test ports: two concurrent live
  `uvicorn` processes (`development`/`production`), each reporting its own environment identity
  from `/health` and each writing to its own `<root>/storage/research.sqlite`, verified by this
  re-run. `test_stop_validates_identity_and_leaves_other_environment_alive` independently
  verifies identity-mismatch rejection and that stopping one owned process leaves an unrelated
  process alive. No existing listener was started, stopped, or otherwise touched.
- Not independently re-verified in this pass (unchanged limitation from the prior session):
  target-host PostgreSQL role/identity provisioning, a full default-port NEW DEV + NEW PROD
  launcher pair, realtime MT5/WebSocket verification, and soak. These remain pending explicit
  authorization and, where relevant, a market window.
- Outcome: with the `launch.py` fix applied, this branch is ready to be pushed as the new
  PR #23 head; CI must still be checked on that actual new head (not assumed from this local
  run) before this task can report a passing CI result.
