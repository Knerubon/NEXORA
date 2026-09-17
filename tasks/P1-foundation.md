---
task: P1
status: done
depends_on: []
agents: ["agents/rin/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md","agents/security/AGENT.md","agents/lingo/AGENT.md"]
skills: ["skills/testing/SKILL.md","skills/documentation/SKILL.md","skills/security/SKILL.md"]
docs: ["docs/requirements.md","docs/architecture.md","docs/development.md","docs/decisions/ADR-002-foundation.md"]
translation_needed: false
---

# P1 — Foundation

## เป้าหมาย / traceability
สร้าง runnable skeleton และ toolchain ที่ใช้ต่อได้โดยไม่ implement trading formulas
Requirement: Non-functional requirements; architecture Backend/Frontend/Persistence/Phase 1 deployment

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
เริ่มได้ทันทีจากการตรวจ repo และเลือก toolchain; ยังไม่มี implementation ที่ถือว่าเสร็จ
Blocker/decision: ไม่มี; ถ้า runtime/dependency install ใช้ไม่ได้ให้ทำเอกสาร/contract ต่อและบันทึก blocked checks
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `apps/api`, `apps/web`, `packages`, `tests`, `infra`, `scripts`, `project configuration`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: runnable scaffold, dependency/config files, smoke tests, local setup doc และ toolchain/layout decision
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. ตรวจ layout เดิมและกำหนด Python package/import strategy, dependency manager, supported runtime versions และ web toolchain ใน decision ที่มี rationale; เลือก versions จาก official docs ตอน implementation
2. สร้าง apps/api, apps/web และ packages/market_data, pnf, adaptive_box, matrix, market_regime, structure, signals, backtest พร้อม boundaries ตาม architecture; risk ยังไม่ใช่ live execution
3. เพิ่ม tests/infra/scripts เท่าที่ runnable scaffold ใช้จริง; core import ต้องไม่ต้อง FastAPI/DB/MT5
4. ทำ API health endpoint และ web status shell ด้วย fake/local state; ไม่อ้าง connected broker/database ถ้ายังไม่มี
5. กำหนด PostgreSQL local setup/config contract และชื่อ environment variables โดยไม่ใส่ secret values; ไม่บังคับมี MT5 เพื่อรัน unit tests
6. เพิ่ม setup/run/test/lint/type/build commands ที่ทดลองจริงใน development doc และเพิ่ม path ลง task docs ก่อนใช้; กำหนด Windows home PC workflow

## Acceptance / validation
- [x] clean checkout ทำตาม documented setup แล้วรัน API health + web shell ได้; บันทึก prerequisite และ commands จริง
- [x] unit smoke/import tests ผ่านโดยไม่มี network/DB/MT5; ไม่มี UI/API imports ใน core
- [x] lint/type checks และ frontend build ผ่านตาม selected toolchain; local DB smoke บันทึกผลจริงหรือ blocker
- [x] ตรวจ config examples, ignore rules, binding defaults และไม่มี broker order path; ไม่สร้าง credentials
- [x] handoff มี package map, commands และ decision references ให้ P2 เริ่มได้
- [x] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใน P1 ให้กำหนดและทดลอง commands ตาม toolchain ที่เลือกก่อน; บันทึก exact command/result ด้านล่างให้ phase ถัดไปใช้ ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Architect contract -> Developer -> Tester -> Reviewer + Security -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: scaffold implemented and validated; done
- Context additions: docs/development.md (setup/validation); docs/decisions/ADR-002-foundation.md (toolchain/layout contract).
- Decisions: [ADR-002](../docs/decisions/ADR-002-foundation.md); setup/package map in [development guide](../docs/development.md)
- Changed files: apps/api, apps/web, packages/nexora, tests, scripts/smoke_api.py, infra, pyproject.toml, uv.lock, .python-version, .env.example, .gitignore, task/development/ADR docs
- Base commit: ece7b5e; implementation commit: 980a4c2; validation handoff commit: a0012a8; branch: codex/p1-foundation; merged [PR #2](https://github.com/Knerubon/NEXORA/pull/2) at c44f72b97c0f2bdee0881d9f2ae2b502d7f2c78c
- Checks (Windows, Python 3.13.3, Node 24.19.0, npm 11.17.0, uv 0.12.15):
  - `.tools/Scripts/uv sync --locked --extra api`: PASS; 28 locked packages
  - `.venv/Scripts/python -m pytest`: PASS, 4 tests; 2 upstream deprecation warnings (Starlette httpx/AnyIO APIs)
  - `.venv/Scripts/ruff check .`, `.venv/Scripts/ruff format --check .`: PASS
  - `.venv/Scripts/mypy`: PASS, 14 source files
  - `.venv/Scripts/python scripts/smoke_api.py`: PASS; actual loopback HTTP health response matched contract
  - `.tools/Scripts/uv lock --check`, `.tools/Scripts/uv build`: PASS; sdist + wheel produced
  - `.tools/Scripts/uv venv .tools/core-check`; `.tools/Scripts/uv pip install --python .tools/core-check/Scripts/python.exe --no-deps dist/nexora-0.1.0-py3-none-any.whl`; `.tools/core-check/Scripts/python -I -c "import nexora.pnf, nexora.market_data, nexora.backtest; print('PASS: installed wheel core imports without API dependencies')"`: PASS
  - `npm --prefix apps/web run lint`, `npm --prefix apps/web run typecheck`, `npm --prefix apps/web run build`: PASS; final ESLint 9 lint and typegen checks PASS
  - `npm --prefix apps/web run start`: PASS; Chrome screenshot/AX inspection at default desktop viewport and 390x844: all four status cards and footer readable, no observed horizontal overflow; viewport restored
  - ESLint 10.10.0 experiment: FAIL (`react/display-name` incompatible API + peer conflicts); reverted to 9.39.5, compatibility exception documented in ADR
  - P1 context paths/local Markdown links: PASS via Python path existence check; requirements/architecture diff empty
  - `git diff --check`: PASS
  - Staged scope/credential-pattern scan: PASS, 31 files; no credential values/private keys/token patterns/credential URLs or ignored dependency trees staged
  - Fresh checkout: `git worktree add --detach .tools/clean-checkout HEAD` at 980a4c2; `.tools/Scripts/uv sync --directory .tools/clean-checkout --locked --extra api` and `npm --prefix .tools/clean-checkout/apps/web ci`: PASS, npm audit 0 vulnerabilities
  - From `.tools/clean-checkout`: `.venv/Scripts/python -m pytest` (4 PASS, same 2 warnings), `.venv/Scripts/python scripts/smoke_api.py`, `.venv/Scripts/ruff check .`, `.venv/Scripts/mypy`: PASS
  - From `.tools/clean-checkout/apps/web`: `npm run lint`, `npm run typecheck`, `npm run build`: PASS; `git status --short` empty after build
  - Fresh web runtime: `node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 13000` from fresh web directory; Python urllib HTTP check returned 200 and all expected status text; child process stopped after smoke
  - PostgreSQL smoke: NOT_RUN; no psql/PostgreSQL/Docker executable on host; run documented SELECT/binding checks on a provisioned local instance
- Self-review: core placeholders contain no formulas/API/DB imports; local binding commands, honest status values, no broker adapter/order implementation; ignore rules verified for env/secrets/dependencies. Independent review completed via merged PR #2.
- Risks: ESLint 9 upstream EOL compatibility exception; dependency deprecation warnings; no remote authentication implementation (local-only); DB smoke unavailable
- Next action: P2 market data can start now that P1 is merged; provision local PostgreSQL แล้วบันทึก DB smoke when available

### Handoff
- task: tasks/P1-foundation.md; status: done; from: developer (self-review); to: independent tester/reviewer
- commit: 980a4c2 (implementation); changed_files/decisions/checks/risks: ตาม record ด้านบน
- review: approved and merged; independent approval evidence: merged PR #2
- blockers: none
