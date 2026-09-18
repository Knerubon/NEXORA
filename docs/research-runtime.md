# Connected research runtime and release gates

[FIX1](../tasks/FIX1-system-readiness.md) / [ADR-018](decisions/ADR-018-readiness-corrections.md) / [requirements](requirements.md) / [architecture](architecture.md)

This implementation supports local research and paper simulation. Historical task completion claims do not certify end-to-end readiness. No broker order path is added. PostgreSQL remains the primary target; SQLite is an explicitly labelled offline fallback. Remote authenticated/encrypted deployment remains a requirement and is currently unavailable, not silently removed.

## Explicit configuration

Install the locked API and PostgreSQL extras with `uv sync --locked --extra api --extra postgres`. Supply configuration paths through runtime environment variables; the application never loads examples automatically.

- `NEXORA_RESEARCH_CONFIG`: JSON decoded as `RuntimeConfig`, containing units, three independently configured resolutions, regime/signal rules and optional paper configuration. See [example](examples/research-config.json). The example's numeric values are synthetic test assumptions, not approved trading settings or OX semantics.
- `NEXORA_BACKTEST_CONFIG_DIR`: directory of saved `BacktestConfig` JSON files. The UI selects filenames without accepting arbitrary paths. See [baseline example](examples/baseline-config.json); fixed/adaptive modes require an explicit pipeline. Baseline uses completed close-price bars only. Config changes create a new run identity.
- `NEXORA_POSTGRES_DSN`: local/private PostgreSQL connection supplied through a secret store/environment. Never log or commit its value. Failure to connect stops startup; it does not silently switch storage.
- `NEXORA_JOURNAL_PATH`: local SQLite file if no PostgreSQL DSN is set; default `data/research.sqlite`. Never share one file between unrelated experiments.

Use separate runtime configurations and stores for recorded bars and quote observation. Quote observation requires all resolutions to select the same bid/ask/mid source; sampled quotes have unknown completeness and fail the paper completeness gate. Configure MT5 only under the existing local read-only adapter instructions in [development](development.md).

Paper is disabled when `paper` is null. Enabling it requires explicit `PaperSessionConfig`: unique `paper-` namespace, account ID, starting cash, fees, slippage, risk policy, requested size and stop distance. Its immutable configuration is journaled; changing it requires a new namespace. Position sizing assumes linear cash units, not broker lot/contract conversion. Stop distance is a reservation assumption, not a simulated stop-order trigger. Reservations remain until the session is flat; partial netting is conservative and can block further orders. Kill is latched; resume cannot clear it. Use a separately reviewed new session after diagnosis.

## Recorded datasets and execution

A dataset directory contains `manifest.json` and `normalized.json`. Create it through `save_dataset(directory, manifest_for(events, quality=...), events)` using normalized inputs from the market-data layer. Files are immutable: missing partitions, changed content, duplicate identities, mixed symbols/sources/units or unordered events are rejected. Corrected data must be saved as a new dataset with parent provenance. This boundary verifies normalized partitions; retain original raw input separately under the market-data contract.

```powershell
.venv/Scripts/python scripts/research_cli.py --dataset data/datasets/recorded --runtime-config data/config/research.json --journal data/research.sqlite --backtest-config data/config/baseline.json
$env:NEXORA_RESEARCH_CONFIG = "data/config/research.json"
$env:NEXORA_JOURNAL_PATH = "data/research.sqlite"
$env:NEXORA_BACKTEST_CONFIG_DIR = "data/config/backtests"
.venv/Scripts/python -m uvicorn nexora_api.main:app --host 127.0.0.1 --port 8000
```

These paths are operator-created inputs, not bundled market data. Start the built frontend on loopback port 3000 or 3100. A fresh app has no fixture runs and no active paper session. Imports use the same pipeline as observation; duplicate imports do not repeat fills. The CLI's backtest is scoped to the supplied dataset even when the journal already contains earlier events.

Backtest uses first observed prices at/after decision delay and holding interval, explicit linear costs and decision-time signals. A signal lacking future exit data remains unfilled; `partial` reports this. This is per-signal research trade accounting, not a portfolio execution model, bid/ask microstructure model, profitability validation or walk-forward validation. Never compare strategy quality across mismatched dataset/config/cost assumptions.

## API and dashboard

State/history contracts are schema 2. `/ws/events` sends authoritative `state_snapshot` envelopes containing quotes, P&F transitions, matrix, structure, regime and signals; clients replace state on reconnect rather than guessing missed deltas. Quote-only streaming remains available. This is a transport clarification of FR-07, not removal of its data channels. Dashboard charts show the last calculation separately from feed readiness.

`POST /backtest/runs` accepts `parameter_set`; `POST /paper/control` accepts `pause`, `resume`, `kill`. Mutations require an allowed local Origin and local client. `/risk/replay` and `/paper/replay` retain their old routes but now read persisted session results instead of creating fixture executions. `/health` is process liveness only; `/operations/readiness` reports actual initialization/freshness/storage reasons and explicitly unverified hardening. A ready local observation scope is never production approval.

## Persistence and recovery

The journal stores immutable accepted events, full output artifacts, run/config data and ordered paper proposals/controls. Replay rebuilds risk and paper together; content hashes detect accidental corruption, not a hostile database administrator. One writer per runtime is supported; optimistic count checks reject stale concurrent writers. Restart the affected worker to load another writer's committed state. Do not run multiple API workers against one session.

[Migration 007](../infra/migrations/007_research_journal.sql) adds the journal without dropping historical tables. Provision on a private PostgreSQL instance; current adapter creates the table if missing, so startup requires schema creation rights. A migration-only least-privilege deployment needs further hardening. CI's ephemeral loopback PostgreSQL uses trust solely for isolated synthetic tests; that configuration must never be copied into deployment.

```powershell
.venv/Scripts/python scripts/recovery_drill.py
.venv/Scripts/python scripts/recovery_drill.py --source data/research.sqlite --backup data/research-backup.sqlite --namespace paper-research
```

Default drill uses isolated synthetic data. Operator mode refuses an existing backup destination, restores in a new Python process and checks the selected paper/risk session hash. It does not prove whole-database restore, PostgreSQL backup, hardware crash durability, MT5 reconnect behavior or RPO/RTO. Avoid copying a live SQLite database file directly; use its backup API.

## Remaining release evidence

- Independent architecture/quant/code/security review of FIX1 and historical unresolved gates.
- Real PostgreSQL backup/restore, crash/restart and retained-artifact integrity on the Windows target; least-privilege roles, schema migration/rollback and retention policy.
- Real MT5 disconnect/reconnect/gaps and capture completeness; sampled quote polling cannot certify a lossless dataset.
- Authenticated encrypted remote access and adversarial network tests before enabling any remote route.
- Measured capacity and bounded engine/journal retention/checkpoint strategy. Operational quote/quality histories are bounded; full research histories are intentionally retained and not yet load-certified.
- Reviewed strategy parameters, held-out/walk-forward research, instrument units and portfolio/partial-close risk semantics before claiming realistic paper execution.

These are open acceptance gates, not passes inferred from the existence of code or successful unit tests.

## Feed timestamp correction

`NEXORA_MT5_TIME_OFFSET_SECONDS` defaults to 0 (UTC contract). Set a nonzero value only as an explicit, verified local correction; positive values are subtracted. [ADR-019](decisions/ADR-019-explicit-feed-time-correction.md) documents the observed +3-hour feed exception, raw provenance, continued stale/future checks and revalidation requirement. This is not automatic timezone or DST detection. The dashboard shows the applied correction and raw time.
