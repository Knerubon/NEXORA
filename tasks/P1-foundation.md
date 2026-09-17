---
task: P1
status: ready
depends_on: []
agents: ["agents/rin/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md","agents/security/AGENT.md","agents/lingo/AGENT.md"]
skills: ["skills/testing/SKILL.md","skills/documentation/SKILL.md","skills/security/SKILL.md"]
docs: ["docs/requirements.md","docs/architecture.md"]
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
- [ ] clean checkout ทำตาม documented setup แล้วรัน API health + web shell ได้; บันทึก prerequisite และ commands จริง
- [ ] unit smoke/import tests ผ่านโดยไม่มี network/DB/MT5; ไม่มี UI/API imports ใน core
- [ ] lint/type checks และ frontend build ผ่านตาม selected toolchain; local DB smoke บันทึกผลจริงหรือ blocker
- [ ] ตรวจ config examples, ignore rules, binding defaults และไม่มี broker order path; ไม่สร้าง credentials
- [ ] handoff มี package map, commands และ decision references ให้ P2 เริ่มได้
- [ ] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใน P1 ให้กำหนดและทดลอง commands ตาม toolchain ที่เลือกก่อน; บันทึก exact command/result ด้านล่างให้ phase ถัดไปใช้ ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Architect contract -> Developer -> Tester -> Reviewer + Security -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: not_started
- Context additions: none
- Decisions: pending
- Changed files / commit / PR: none
- Checks: not_run
- Review: pending
- Blockers: none; toolchain decision เป็นขั้นแรกของงาน
- Next action: ตรวจ current tree และกำหนด toolchain/layout
