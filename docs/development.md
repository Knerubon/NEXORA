# Local development — Windows PowerShell

P1 scaffold ตาม [ADR-002](decisions/ADR-002-foundation.md), [requirements](requirements.md) และ [architecture](architecture.md)
Prerequisites: Python 3.13.x, Node.js 24.x/npm 11, Internet สำหรับ install; PostgreSQL 18 เฉพาะ DB smoke
รันจาก repo root; ไม่ต้อง activate virtualenv หรือมี MT5

## Setup
```powershell
python -m venv .tools
.tools/Scripts/python -m pip install uv==0.12.15
.tools/Scripts/uv sync --locked --extra api
npm --prefix apps/web ci
```
`.tools` เป็น ignored local bootstrap; dependencies อยู่ใน `uv.lock` และ `apps/web/package-lock.json`
Core-only environment ใช้ `uv sync --locked --no-dev`; API เป็น optional extra

## Run (คนละ terminal)
```powershell
.venv/Scripts/python -m uvicorn nexora_api.main:app --host 127.0.0.1 --port 8000
npm --prefix apps/web run dev
```
เปิด `http://127.0.0.1:3000`; web เป็น local shell ไม่ poll API
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```
Expected: status `ok`, mode `research`; database/broker `not_configured`, engine `not_implemented`
หยุดด้วย Ctrl+C; remote access ต้องมี authentication/encryption ก่อน

### Dashboard + quality contracts (P9/DQ1)
```powershell
Invoke-RestMethod http://127.0.0.1:8000/config
Invoke-RestMethod http://127.0.0.1:8000/state
Invoke-RestMethod "http://127.0.0.1:8000/history?limit=5"
Invoke-RestMethod http://127.0.0.1:8000/quality
```
Expected: local-only API contracts for realtime quote + quality sidecar status. `backtest_lab_status` becomes `ready` when backtest runs are available.
WebSocket channels: `ws://127.0.0.1:8000/ws/quotes` and `ws://127.0.0.1:8000/ws/events`
Remote unauthenticated access is out of scope and must stay blocked by local host/origin policy.

### Backtest Lab contracts (P10)
```powershell
Invoke-RestMethod http://127.0.0.1:8000/backtest/runs
Invoke-RestMethod "http://127.0.0.1:8000/backtest/compare?run_ids=<run_id_1>,<run_id_2>"
```
Expected: API returns stored reproducible runs and compare payload from persisted metrics; UI reads these values directly.

### Risk replay contracts (P11)
```powershell
Invoke-RestMethod http://127.0.0.1:8000/risk/replay
```
Expected: API returns policy-governed allow/reject decisions over replayed signals with deterministic counters/state.

### Paper replay contracts (P12)
```powershell
Invoke-RestMethod http://127.0.0.1:8000/paper/replay
```
Expected: API returns local-only paper order/fill/ledger trace from `RiskDecision` outputs with checkpointed idempotency state.

### Operations readiness and alerts (P13)
```powershell
Invoke-RestMethod http://127.0.0.1:8000/operations/readiness
Invoke-RestMethod http://127.0.0.1:8000/operations/alerts
```
Expected: explicit `ready|degraded` readiness with reason codes plus observable alerts for quote/quality/paper status.

### Local recovery drill (P13)
```powershell
.venv/Scripts/python scripts/recovery_drill.py
```
Expected: isolated SQLite backup restored in a fresh Python process; paper/risk state hashes match. This does not certify PostgreSQL, host crash recovery or RPO/RTO. See [research runtime](research-runtime.md).

## Validation
```powershell
.tools/Scripts/uv lock --check
.venv/Scripts/python -m pytest
.venv/Scripts/python scripts/smoke_api.py
.venv/Scripts/ruff check .
.venv/Scripts/ruff format --check .
.venv/Scripts/mypy
.tools/Scripts/uv build
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web run start
```
Production smoke เปิด localhost:3000 หลัง build/start; ต้องไม่มี connected claims
ผลจริงอยู่ใน [P1 Execution record](../tasks/P1-foundation.md); command ในคู่มือไม่ใช่หลักฐาน pass

## Local PostgreSQL
ติดตั้ง PostgreSQL 18 ด้วย system installer; credentials อยู่ใน runtime/local secret store
ตรวจ active config กับ [postgresql example](../infra/postgresql.conf.example) และ [loopback rules](../infra/pg_hba.conf.example); app ไม่โหลด examples เอง
ใช้ localhost binding และ SCRAM; ตรวจว่าไม่มี broad allow rules และ restart หลังเปลี่ยน binding
สร้าง local role/database โดย admin ตาม local policy; ไม่เก็บ credentials ใน scripts/examples
ชื่อ variables ดู [.env.example](../.env.example); P1 ยังไม่อ่านหรือเชื่อม DB
เมื่อ provision และตั้ง runtime variables แล้วตรวจ:
```powershell
psql -X -v ON_ERROR_STOP=1 -c "SELECT 1;"
psql -X -v ON_ERROR_STOP=1 -c "SHOW listen_addresses;"
Get-NetTCPConnection -LocalPort 5432 -State Listen
```
Expected: `1`, `localhost` และ loopback listeners; ห้ามเปิด DB/MT5 public
Schema/migrations เป็นงาน P2

## Connected research runtime (FIX1)

See [configuration, recorded-data import and remaining release gates](research-runtime.md). Default startup contains no sample backtests and no configured paper session.

## DEV/PROD isolation

Use the [environment isolation guide](environment-isolation.md) for environment-specific
configuration, safe worktrees and startup scripts. The legacy bare startup examples above
are historical; new isolated deployments use `scripts/nexora.ps1`. Do not reuse the existing
live runtime journal for DEV. Legacy `.env` settings require explicit operator migration.
