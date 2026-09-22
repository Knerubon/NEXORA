# Environment Isolation V1

Research/observation and local paper only; PROD is a stable screen, not authorization
for live or demo orders. Same market input; separate code, configuration, processes,
brain, storage and logs. No strategy, P&F, Matrix, Signal or Experience formula changes.

## Layout and baseline

- `D:\NEXORA\NEXORA`: existing stable worktree; do not edit it during development.
- `D:\NEXORA\NEXORA-DEV`: development feature worktree.
- Rebased on current main `130f260` (including PR21 and PR22), as requested by Rin.
  Experience is included unchanged. No domain/time semantics are altered by isolation.
  Main itself is not modified; final Rin approval, merge and deployment remain pending.

Exact worktree setup (only if the destination/branch do not already exist):

```powershell
git -C D:\NEXORA\NEXORA fetch --no-tags origin
git -C D:\NEXORA\NEXORA worktree add D:\NEXORA\NEXORA-DEV codex/environment-isolation-v1
Set-Location D:\NEXORA\NEXORA-DEV
uv sync --locked --extra api --extra postgres
npm --prefix apps/web ci
Copy-Item config/development.env.example .env.development
```

Install `--extra mt5` separately when using the existing MT5 adapter. Each worktree
owns its virtualenv and node_modules; do not junction writable build/dependency folders.
All feature edits, tests, builds and Codex work run in DEV. Update stable code only
through an approved release while that environment is stopped. No automated checkout,
merge, tag, deployment or movement of the existing repository is provided.

## Central configuration

`apps/api/nexora_api/environments.json` defines the loopback host, fixed ports and labels.
Python settings, Next configuration and the browser badge consume this same profile.
Select `NEXORA_ENV` before importing the API (default development); only the matching
root `.env.development` or `.env.production` is loaded. Shell settings take precedence.
The legacy root `.env` is intentionally not loaded; no secrets are copied automatically.
Never carry exported PROD settings into a DEV shell. Templates are in `config/`.

| Resource | DEV | PROD |
| --- | --- | --- |
| Web | http://127.0.0.1:3000 | http://127.0.0.1:3100 |
| API | http://127.0.0.1:8000 | http://127.0.0.1:8100 |
| WebSocket | ws://127.0.0.1:8000/ws/events and /ws/quotes | ws://127.0.0.1:8100/ws/events and /ws/quotes |
| Root | DEV worktree/.runtime/development | stable worktree/.runtime/production |
| SQLite | root/storage/research.sqlite | root/storage/research.sqlite |
| State/PIDs | root/state | root/state |
| Checkpoints (reserved; engine currently replays) | root/checkpoints | root/checkpoints |
| Logs | root/logs/api.log and web.log | root/logs/api.log and web.log |
| Application cache (reserved) | root/cache | root/cache |
| Next build/cache | DEV worktree/apps/web/.next | stable worktree/apps/web/.next |

`NEXORA_RUNTIME_ROOT` can select an absolute isolated directory. Optional
`NEXORA_JOURNAL_PATH`, `NEXORA_STATE_PATH`, `NEXORA_CHECKPOINT_PATH`, `NEXORA_LOG_PATH`,
`NEXORA_CACHE_PATH` must resolve inside it. Config JSON/backtest config paths must be
inside the owning worktree or its runtime root. `NEXORA_MT5_PATH`, symbol and explicit
time offset keep their existing meaning. No values are invented or retuned.

## Start, stop and build

From the selected worktree, in a fresh PowerShell:

```powershell
# DEV worktree
.\scripts\nexora.ps1 -Environment development -Action start
.\scripts\nexora.ps1 -Environment development -Action stop

# Stable worktree, only AFTER approved integration/setup
.\scripts\nexora.ps1 -Environment production -Action build
.\scripts\nexora.ps1 -Environment production -Action start
.\scripts\nexora.ps1 -Environment production -Action stop
```

Restart = stop then start for the same environment. PROD always uses `next start`;
`next dev` refuses production. Build IDs encode the environment; starting a DEV build
as PROD fails. Rebuild when changing public settings. The launcher starts hidden
Windows processes and records PID, creation time, cwd and command. Stop validates
those fields before terminating only that process tree. A reused PID is rejected.
Existing occupied ports are rejected, never killed. A launcher lock prevents concurrent
start/stop/build operations; after a launcher crash, inspect its recorded PID before
manually removing a stale lock. Do not delete ownership markers to bypass a failure.

`start` confirms process creation, not completion of engine recovery. Check `/health`,
`/state` and the environment-specific logs. Recovery still blocks API readiness in V1;
this task isolates it from the other environment, and does not implement checkpoints.
Do not run bare uvicorn/npm commands as a substitute for the supported launcher.

## Guards and limits

- Canonical resolved paths reject outside-root writes, including junction/symlink escape.
- Worktree and runtime markers bind environment, absolute code path and runtime root.
  Opposite environments cannot claim each other's root, nested root or worktree.
- Unowned populated roots and hardlinked database files fail before database opening.
- HTTP/WebSocket requests from the other environment's web origin are rejected.
- Research import CLI uses the same journal guard. Tests use temporary roots and no
  workstation feed configuration. Arbitrary scripts/direct DB clients must not be given
  PROD access; markers are accidental-misconfiguration guards, not an OS security boundary.
- For enforcement against development tools with the same OS permissions, use separate
  Windows accounts and ACLs: DEV identity has no write access to stable code/runtime,
  PROD identity owns only its release/runtime. Apply and verify ACLs administratively;
  this change does not alter user permissions. Separate machines give stronger fault isolation.
- Shared MT5 read-only input is allowed; terminal availability/host resources remain
  shared failure domains. No guarantee that the external terminal isolates concurrent clients.
- No shared writable databases, journal, engine state, Experience memory, checkpoint,
  caches or logs. Experience uses its owning runtime journal (including raw observations,
  frozen snapshots, lifecycle and outcome streams); its implementation matches main.

## Time semantics preserved

DEV and PROD must preserve identical existing MT5 normalization and offset semantics.
Copy the verified `NEXORA_MT5_PATH`, symbol and `NEXORA_MT5_TIME_OFFSET_SECONDS` exactly;
never silently reset an explicit offset to zero when moving from legacy `.env`.
The current correction subtracts the configured offset from raw event time, retaining
raw time and offset provenance; the observed 10800-second workaround is feed-specific,
not a timezone default. Do not auto-detect offsets, change clocks, rewrite timestamps,
or change trading/event/Experience time rules in PR23. See
[ADR-019](decisions/ADR-019-explicit-feed-time-correction.md) and the
[proposed Time Semantics task](../tasks/TS1-time-semantics-proposal.md).

## Existing data and PostgreSQL migration

No existing database is moved, cleared or adopted by startup. Keep the running legacy
screen unchanged until an approved maintenance window. For a fresh DEV, keep its new
empty journal. For PROD migration: stop only the old runtime, take a verified SQLite
backup (SQLite backup API, including WAL state), initialize an empty PROD root using
`-Action describe`, restore the verified backup into that root's storage while stopped,
then start and verify config, event counts and readiness. Never copy a live .sqlite file
alone or change historical/config semantics. Do not point DEV at the old PROD journal.
This migration is documented, not executed by this task. The required operator gates,
verification evidence, soak and rollback procedure are in the
[migration/rollback checklist](environment-migration-checklist.md). All checkboxes are
unexecuted; this PR does not authorize a production cutover.

PostgreSQL requires separate databases and least-privilege roles. Before enabling a
local DSN, an administrator provisions this identity table in each database, using
exactly one row `development` or `production` as appropriate:

```sql
CREATE TABLE public.nexora_environment_identity (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  environment text NOT NULL CHECK (environment IN ('development', 'production'))
);
-- Execute the appropriate single INSERT in each database, never both.
INSERT INTO public.nexora_environment_identity (environment) VALUES ('development');
```

Application roles receive SELECT only on this identity table; do not grant ownership,
UPDATE/DELETE/INSERT or access to the opposite database. Keep credentials out of Git.
Startup reads identity in a read-only transaction before journal initialization and fails
closed on missing/mismatched identity. This does not provision DBs, migrate schemas,
or claim verified target-host ACL/role separation. Existing isolated PostgreSQL tests
continue to use their explicitly disposable test database.

## Review

See [ENV1 execution record](../tasks/ENV1-environment-isolation.md) for exact validation
and limitations. Self-review; independent Rin review pending. No merge or tags.
