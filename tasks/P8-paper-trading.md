---
task: P8
status: blocked
depends_on: ["tasks/P7-backtest.md"]
agents: ["agents/rin/AGENT.md","agents/quant/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md","agents/security/AGENT.md"]
skills: ["skills/paper-trading/SKILL.md","skills/backtesting/SKILL.md","skills/postgres/SKILL.md","skills/fastapi/SKILL.md","skills/websocket/SKILL.md","skills/frontend/SKILL.md","skills/testing/SKILL.md","skills/security/SKILL.md"]
docs: ["docs/requirements.md","docs/architecture.md"]
translation_needed: false
---

# P8 — Paper Trading

## เป้าหมาย / traceability
ติดตาม simulated orders/fills และ risk controls บน live observation โดยไม่มี broker execution
Requirement: requirements Phase gates; safety/NFR; reuse FR-06/07/08/09

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
ตรวจ Execution record และ merge/check evidence ของ P7 ก่อนเปลี่ยนเป็น ready; dependency file อ่านเฉพาะ status/evidence ไม่โหลด context ของ phase นั้นต่อทั้งหมด
Blocker/decision: P7 reproducibility ต้องผ่าน; live trading ต้อง separate future scope/approval ไม่อยู่ใน task นี้
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `packages/backtest`, `packages/risk`, `apps/api`, `apps/web`, `infra`, `tests`, `scripts`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: local simulator/risk controls, ledger/recovery, paper UI, runbook และ safety evidence
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. กำหนด local paper simulator boundary/package placement ใน ADR; ใช้ research signals/core เดียวกับ P7
2. Quant ล็อก simulated fills/costs/partial-rejection timing/accounting และ configurable risk limits; ไม่ใช้เงินจริงหรือ demo broker execution
3. implement paper ledger, positions, simulated orders/fills, risk rejection และ kill switch; order gateway จริงอยู่นอก scope
4. persist idempotency/recovery checkpoints; restart/reconcile ไม่สร้าง fill ซ้ำ
5. เพิ่ม API/WS/UI paper views/labels และ trace signal -> simulated order/fill -> ledger; ใช้ fake/replay feed ทดสอบได้
6. จัด runbook start/pause/recover/reconcile และ evidence safety review; completion เป็น paper capability เท่านั้น

## Acceptance / validation
- [ ] boundary inspection + negative tests ยืนยันไม่มี broker order call หรือ live gateway ใน paper path
- [ ] known sequence ตรวจ fills/costs/cash/positions/PnL/rejections และ ledger reconciliation
- [ ] duplicate/restart/disconnect/stale data ไม่สร้าง fill ซ้ำ; risk limit/kill switch หยุด simulated execution ตาม contract
- [ ] paper replay deterministic และ shared signal core ให้ผลสอดคล้อง P7; account namespace แยกชัด
- [ ] API/WS/UI แสดง paper status/failures ถูกต้อง; Security review ผ่านและ runbook recover ทดลองแล้ว
- [ ] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใช้ commands ที่ P1 จัดทำและตรวจว่าใช้งานได้กับ checkout ปัจจุบัน; บันทึก exact command/result ด้านล่าง ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Quant/Architect decision -> Developer -> Tester -> Reviewer + Security -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: not_started
- Context additions: none
- Decisions: pending
- Changed files / commit / PR: none
- Checks: not_run
- Review: pending
- Blockers: dependency P7 not completed; ดู decision gate ด้านบน
- Next action: ตรวจ dependency completion evidence แล้วทำ decision/contracts ของ task
