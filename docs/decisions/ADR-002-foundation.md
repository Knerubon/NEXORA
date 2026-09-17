# ADR-002 — Foundation toolchain and boundaries

Status: accepted for P1 implementation; independent review pending

## Decision / rationale
- Python 3.13.x, uv project lock, Hatchling wheel; จำกัด runtime ที่ minor ซึ่งทดสอบจริง
- Node.js 24 LTS, npm lock, Next.js 16 App Router, React 19, TypeScript 5, ESLint 9.39.5
- pytest, Ruff และ mypy strict สำหรับ Python; PostgreSQL 18 เป็น local persistence contract

เลือก Python/Node ที่มีบน Windows host และตรง upstream requirements; uv lock แยก API extra จาก core ส่วน npm มีบน host จึงไม่เพิ่ม pnpm
หนึ่ง Python distribution ลดการประสาน release; core ไม่มี third-party runtime dependency แต่ API เป็น optional extra
web ใช้ static local state ใน P1; ไม่มี data integration หรือ trading formulas

## Package map / contracts
- `packages/nexora/market_data`: normalization boundary (P2)
- `packages/nexora/pnf`: deterministic engine (P3)
- `packages/nexora/adaptive_box`: sizing (P4)
- `packages/nexora/{matrix,market_regime,structure,signals}`: structure/research (P5)
- `packages/nexora/backtest`: shared-engine replay (P7)
- `apps/api/nexora_api`: FastAPI adapter; core ห้าม import API/UI/DB/MT5
- `apps/web`: presentation; core namespaces ยังเป็น placeholders

`GET /health` คืน HTTP 200: `status=ok`, `mode=research`, `database=not_configured`, `broker=not_configured`, `engine=not_implemented`; เป็น liveness ไม่ใช่ readiness
API/web commands bind loopback; API Host allowlist ไม่ใช่ authentication จึงยังเปิด remote ไม่ได้
PostgreSQL รับ `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` ผ่าน runtime ใน phase ถัดไป; ห้ามส่ง DB config ไป browser

## Limits / compatibility
ยังไม่มี event/formula/schema contract ให้ migrate; P2 ต้องกำหนดก่อนเขียน ingestion
ไม่เปลี่ยน requirements/architecture; P2 รอ P1 review/merge gate
DB smoke ต้องตรวจบนเครื่องที่มี PostgreSQL
ESLint 9 เป็น compatibility exception สำหรับ dev tooling: หมด upstream support แล้ว แต่ทดสอบ ESLint 10.10.0 พบ peer conflicts และ `react/display-name` crash ใน plugins ที่ `eslint-config-next@16.3.5` ใช้ จึง pin 9.39.5 โดยไม่ force peer dependencies; ต้องติดตาม upgrade เมื่อ plugin stack รองรับ ESLint 10

## Official references consulted
- [Python 3.13](https://www.python.org/downloads/release/python-3130/)
- [Node.js release schedule](https://github.com/nodejs/Release)
- [uv project layout and lock](https://docs.astral.sh/uv/concepts/projects/layout/)
- [Next.js installation](https://nextjs.org/docs/app/getting-started/installation)
- [Next.js 16 requirements](https://nextjs.org/docs/app/guides/upgrading/version-16)
- [FastAPI server](https://fastapi.tiangolo.com/deployment/manually/)
- [PostgreSQL support policy](https://www.postgresql.org/support/versioning/)
- [ESLint support policy](https://eslint.org/version-support/)
