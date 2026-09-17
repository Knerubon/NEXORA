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
