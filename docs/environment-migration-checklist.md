# Stable runtime to isolated PROD: migration and rollback checklist

Status: proposed operator procedure; NOT EXECUTED. Requires Rin final approval and an
explicit maintenance window. PR23 only prepares this checklist; no PROD deploy, data
migration, tags, live/demo orders or time-semantic changes are authorized. Runtime design:
[environment isolation](environment-isolation.md). Scope: preserve existing backend and
all research/Experience history. Switching SQLite to PostgreSQL is a separate migration.

## 1. Freeze the plan and capture the baseline

- [ ] Name operator, reviewer, maintenance window, rollback owner and maximum outage.
  Record approved release commit and known-good legacy commit/build/dependency versions.
  Do not update the stable worktree or uninstall its dependencies before rollback is ready.
- [ ] Inventory the actual legacy commands, PID/start time/cwd, ports, storage backend,
  database path/name, WAL usage and all mutable paths. Do not infer that port 8000 means
  DEV: the legacy stable screen currently uses 3000/8000. Never use a broad process kill.
- [ ] Confirm isolated PROD will use web 3100/API 8100 and its own root, SQLite/database,
  state, checkpoint, log, cache, build and process ownership. Confirm DEV uses 3000/8000
  only AFTER those legacy listeners are retired. Check filesystem/database permissions;
  DEV must not have write permission to stable runtime/code. Owner markers alone are not ACLs.
- [ ] Preserve exact research and backtest JSON contents plus canonical config hash and
  engine/schema versions, strategy parameters, units, price source and symbol. Save
  checksums of non-secret configuration files. Path relocation must not change their
  semantic contents or embedded dataset references.
- [ ] Inventory effective settings from BOTH legacy `.env` and the process environment
  (shell overrides win). Transfer necessary settings to private `.env.production`/secret
  store, never to Git, logs or this checklist. Verify no inherited DEV/PROD overrides.
  Keep an access-controlled backup of secret configuration; publish only redacted evidence.
- [ ] Preserve MT5 terminal executable/path, selected broker/account/source and symbol
  mapping, precision, quote price source, explicit offset and adapter/version settings.
  `NEXORA_MT5_TIME_OFFSET_SECONDS` must remain EXACTLY the verified old value; do not
  assume the template's zero or adopt 10800 from another feed. Do not change terminal,
  machine clock, login or order permissions. Shared input must not imply shared state.
- [ ] Capture baseline `/health`, `/config`, `/state`, `/quality`, `/operations/readiness`,
  `/experiences/summary`, representative Experience detail/outcomes and quote/WebSocket
  samples. Save timestamp, latest event identity, normalized/raw event times and receipt
  time, offset, active runtime config hash/stream, P&F columns, Matrix directions/status,
  Signal decision/reasons/evidence, and existing warning/error states in a private artifact.
  Baseline health alone is not proof of feed/research readiness.

## 2. Quiesce, back up and prove restoration

- [ ] Stop only the inventoried legacy application writers using their verified process
  ownership. Confirm no competing journal/Experience/paper writer remains. Leave MT5
  configuration unchanged. Mark this cutover watermark and the resulting observation gap.
- [ ] SQLite: create a consistent snapshot using SQLite backup API, not a raw copy of an
  open `.sqlite` file. Include committed WAL contents. Never delete WAL/SHM to force a
  copy. Preserve the stopped original database untouched for rollback. Use a new backup
  path; record SHA-256, file size, timestamp and tool/version. Keep an off-host/access-
  controlled copy as required by the operator's backup policy.
- [ ] PostgreSQL (if actually used): take a consistent `pg_dump` with matching tools and
  securely preserve required roles/grants separately; verify with `pg_restore --list`
  and a real restore to a disposable, isolated database. No credentials in commands/logs.
  App roles must not be able to modify environment identity or access the other DB.
- [ ] Restore the backup to a disposable verification destination (never legacy or DEV's
  active database). SQLite `PRAGMA integrity_check` must return `ok`; PostgreSQL restore
  must complete without errors. Record verified backup hash before and after transport.
- [ ] Compare source-at-watermark, backup restore and intended target BEFORE any target
  ingestion: total journal count, per-stream counts, ordered sequence/event-key/content-
  hash manifest, minimum/maximum sequences, active runtime event count and config hash.
  Include ALL config/backtest/paper/risk/Experience streams, not just current symbol.
  Verify canonical payload hashes through existing journal verification on the offline
  restore; a DB integrity check alone does not verify journal content.

Read-only inventory query, executed on each consistent stopped/restored snapshot:

```sql
SELECT stream, COUNT(*) AS row_count, MIN(sequence) AS first_sequence,
       MAX(sequence) AS last_sequence
FROM research_journal GROUP BY stream ORDER BY stream;

SELECT sequence, stream, event_key, content_hash
FROM research_journal ORDER BY sequence;
```

- [ ] Store an ordered manifest/digest using identical serialization for each comparison.
  Check Experience snapshot IDs, observations, lifecycle, completed outcomes and pending
  horizons; no missing streams or changes to frozen/history records. Raw DB file hashes
  can differ after restoration; journal identity/content must agree.
- [ ] On disposable data only, rehearse restore/recovery with the approved code and unchanged
  config; compare results at the SAME recorded watermark. Experience recovery can append
  missing idempotent projections: retain original rows unchanged and account for every
  additional row separately. Do not call unexplained count differences a successful migration.
  Avoid comparing a moving live feed against a frozen snapshot.
- [ ] If any check fails, STOP. Keep legacy unchanged and follow rollback. Do not repair by
  dropping history, reinterpreting timestamps, editing content hashes or resetting state.

## 3. Prepare isolated PROD while stopped

- [ ] Under the approved release procedure, prepare the stable code/dependencies and keep
  the rollback release/build available. Review the diff/version against the approved commit.
  Do not use DEV node_modules, `.next`, virtualenv, data or writable state through junctions.
- [ ] Initialize an EMPTY PROD-owned root with the supported `describe` action and verify
  owner markers bind production, correct code and absolute root. Do not copy DEV markers
  or blindly overwrite existing owned/populated directories. Retain old root separately.
- [ ] Restore the VERIFIED backup to PROD-owned storage. Preserve its backend; for PostgreSQL
  use a separate production DB/role and administrator-provisioned read-only environment
  identity. Confirm no path/DSN resolves to DEV or the retained legacy rollback store.
- [ ] Recheck complete counts/manifests/config hash from section 2 at the restoration
  watermark. Move only required existing state/checkpoint files with documented versions
  and ownership; current reserved checkpoint paths do not imply checkpoint support.
- [ ] Build PROD with `scripts/nexora.ps1 -Environment production -Action build` from its
  own worktree. Verify production-prefixed BUILD_ID, API URL 8100, WebSocket URL 8100,
  web port 3100, visible PROD badge and `next start` command (no development/hot reload).
  Preserve exact MT5/time settings. This is a later operator action, not executed by PR23.

## 4. Cutover and functional verification

- [ ] Start only isolated PROD using the approved launcher. Confirm `/health` on 8100
  reports production and logs show the correct storage/root and environment. Wait for
  recovery to complete; allow for known synchronous replay cost. If it exceeds the
  pre-agreed outage budget, roll back rather than repeatedly restarting or clearing data.
- [ ] Confirm web 3100 is HTTP 200 with PROD badge; API/config identifies production;
  all writable files and process records remain in PROD-owned paths. No DEV file writes.
- [ ] During an active market window, compare several successive `/quotes` samples with
  the SAME MT5 source: symbol, bid/ask, precision, advancing raw/corrected timestamps,
  unchanged offset, receipt timestamps and freshness. Check stale/future handling retains
  its existing meaning. Market closure is not a pass for realtime validation; reschedule it.
- [ ] Connect from origin 3100 to BOTH `ws://127.0.0.1:8100/ws/quotes` and `/ws/events`.
  Observe successive real updates; verify disconnect/reconnect and authoritative REST
  resynchronization. Confirm DEV origin 3000 is rejected, and no stale state rolls back a
  newer quote. Record messages/timings with private source identifiers redacted as needed.
- [ ] Verify P&F columns/box/reversal configuration, Matrix resolutions/directions/status,
  Signal decision/score/reasons/evidence and source/config versions against the recorded
  common-watermark replay baseline. After new ingestion, compare equivalent events rather
  than expecting live values to remain equal to the old snapshot. WAIT is a valid outcome.
- [ ] Verify Experience endpoints and preserved IDs/outcomes; pending horizons continue
  without duplicates, historical rewrites or cross-environment rows. Risk/paper state
  remains unchanged at the baseline; no broker order path is introduced or exercised.
- [ ] Verify recovery/data-quality warnings, journal growth and process ownership. Use a
  disposable DEV instance for restart/stop isolation checks only when its ports are free;
  never stop legacy/stable listeners by port number to force a test.

## 5. Soak and acceptance

- [ ] Operator/reviewer agree the observation window before cutover: proposed minimum
  2 continuous hours in an active market (covering at least two 60-minute Experience
  horizons), plus the next session boundary when relevant. This is an operational gate,
  not a trading/time formula. Record any market gaps explicitly.
- [ ] At start, every 15 minutes and end, record readiness, feed/WS continuity, reconnects,
  quote/research lag, oldest pending Experience horizon, errors, process CPU/memory,
  storage growth and per-stream counts. Compare with legacy baseline and pre-agreed
  operational limits; do not invent performance SLAs after seeing the result.
- [ ] No unexplained hash/identity conflicts, duplicate outcomes, timestamp/offset drift,
  cross-environment writes, growing unbounded processing lag or unexplained count loss.
  Verify independent DEV stop/restart leaves PROD PIDs, feed and journal intact when safe.
- [ ] Any failed mandatory gate triggers rollback; unresolved gaps or insufficient active-
  market observation keep acceptance pending. Reviewer signs recorded evidence before
  retiring the legacy runtime. Retain rollback artifacts for the agreed retention period.

## 6. Rollback (prepare BEFORE cutover)

- [ ] Trigger on backup/count/config mismatch, failed recovery/startup, incorrect time/MT5
  behavior, feed/WS failure, domain mismatch, ownership violation or failed soak limit.
- [ ] Stop ONLY isolated PROD through its verified PID registry. Confirm its writers exited.
  Preserve failed PROD storage/WAL, logs and post-cutover event watermark in a separate
  incident snapshot; do not overwrite the original known-good backup or legacy store.
- [ ] Restore the known-good code/dependencies/build and exact legacy environment/MT5
  settings through the approved release mechanism, without changing tags or git history.
  Prefer the retained untouched legacy store; if unavailable, restore the verified backup
  to a clean legacy-owned location and recheck counts/content before startup.
- [ ] Ensure legacy ports 3000/8000 are free. If a DEV instance now owns them, stop only
  that verified DEV instance and record why. Never kill by process name or arbitrary port.
- [ ] Start the original legacy commands with their original settings. Verify health,
  quote/WS flow, P&F/Matrix/Signal and Experience state against the known-good watermark.
  Record rollback completion time, lost observation interval and last confirmed event.
- [ ] Post-cutover PROD observations are NOT automatically merged into the older journal.
  Quarantine them for a separately approved identity/order-verified reconciliation/replay;
  retain evidence of any gap. Do not double-write both runtimes into one store or claim
  zero data loss without reconciliation evidence.
- [ ] Notify Rin through the authorized review process with failure evidence and the
  unblock condition. Leave migration acceptance pending; no retry without a corrected plan.

Every checkbox above is currently unexecuted. A checklist or an earlier empty-database
smoke test is not evidence of completed production migration, market validation or soak.
