---
task: P12
status: in_review
depends_on: ["tasks/P11-risk-engine.md", "tasks/P10-backtest.md"]
agents: ["agents/rin/AGENT.md","agents/quant/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md","agents/security/AGENT.md"]
skills: ["skills/paper-trading/SKILL.md","skills/backtesting/SKILL.md","skills/postgres/SKILL.md","skills/fastapi/SKILL.md","skills/websocket/SKILL.md","skills/frontend/SKILL.md","skills/testing/SKILL.md","skills/security/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md"]
translation_needed: false
---

# P12 — Paper Trading

## เป้าหมาย / traceability
ติดตาม simulated orders/fills และ risk controls บน live observation โดยไม่มี broker execution
Requirement: requirements Phase gates; safety/NFR; reuse FR-06/07/08/09

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
ตรวจทุก depends_on ใน metadata พร้อม Execution record และ merge/check evidence ของ P10 ก่อนเปลี่ยนเป็น ready; dependency file อ่านเฉพาะ status/evidence ไม่โหลด context ของ phase นั้นต่อทั้งหมด
Blocker/decision: P11 risk completion และ P10 reproducibility ต้องผ่าน; live trading ต้อง separate future scope/approval ไม่อยู่ใน task นี้
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `packages/nexora/backtest`, `apps/api`, `apps/web`, `infra`, `tests`, `scripts`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: local simulator เชื่อม P11 Risk Engine, ledger/recovery, paper UI, runbook และ safety evidence
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. กำหนด local paper simulator boundary/package placement ใน ADR; ใช้ research signals/core เดียวกับ P10
2. Quant ล็อก simulated fills/costs/partial-rejection timing/accounting และการ consume P11 RiskDecision; ไม่ implement sizing/limits ซ้ำ; ไม่ใช้เงินจริงหรือ demo broker execution
3. implement paper ledger, positions, simulated orders/fills, P11 risk rejection และ kill switch enforcement; order gateway จริงอยู่นอก scope
4. persist idempotency/recovery checkpoints; restart/reconcile ไม่สร้าง fill ซ้ำ
5. เพิ่ม API/WS/UI paper views/labels และ trace signal -> simulated order/fill -> ledger; ใช้ fake/replay feed ทดสอบได้
6. จัด runbook start/pause/recover/reconcile และ evidence safety review; completion เป็น paper capability เท่านั้น

Proposed simulator package ต้องมี ADR ก่อนสร้าง; account namespace เป็น paper เท่านั้น
ทุก simulated proposal ต้องผ่าน P11 policy/version และ approval freshness/reservation contract ก่อน fill; rejected/expired/replayed approval ต้องไม่ bypass risk

## Acceptance / validation

- [x] signal -> P11 RiskDecision -> simulated order/fill -> ledger trace ครบ; reject/expired decision, pause/kill switch และ stale account ไม่มี fill
- [x] boundary inspection + negative tests ยืนยันไม่มี broker order call หรือ live gateway ใน paper path
- [x] known sequence ตรวจ fills/costs/cash/positions/PnL/rejections และ ledger reconciliation
- [x] duplicate/restart/disconnect/stale data ไม่สร้าง fill ซ้ำ; risk limit/kill switch หยุด simulated execution ตาม contract
- [x] paper replay deterministic และ shared signal core ให้ผลสอดคล้อง P10; account namespace แยกชัด
- [ ] API/WS/UI แสดง paper status/failures ถูกต้อง; Security review ผ่านและ runbook recover ทดลองแล้ว
- [ ] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใช้ commands ที่ P1 จัดทำและตรวจว่าใช้งานได้กับ checkout ปัจจุบัน; บันทึก exact command/result ด้านล่าง ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Quant/Architect decision -> Developer -> Tester -> Reviewer + Security -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: local paper simulator package, checkpoint persistence contract, risk-integrated replay flow, API/UI paper status surfaces, and deterministic idempotency tests implemented
- Context additions: [ADR-016](../docs/decisions/ADR-016-paper-trading-simulator-boundary.md), [migration 006](../infra/migrations/006_paper_trading.sql)
- Decisions: paper-only namespace enforcement, proposal-id idempotency, fail-closed runtime pause/kill-switch, and checkpoint restore contract
- Changed files / commit / PR: `packages/nexora/paper/*`, `packages/nexora/backtest/service.py`, `apps/api/nexora_api/main.py`, `apps/web/app/page.tsx`, `tests/test_paper.py`, `tests/test_dashboard_api.py`, `docs/development.md`
- Checks:
  - `d:/NEXORA/NEXORA/.venv/Scripts/python.exe -m pytest tests/test_paper.py tests/test_dashboard_api.py tests/test_backtest.py`: PASS
  - `d:/NEXORA/NEXORA/.venv/Scripts/python.exe -m ruff check packages/nexora/paper packages/nexora/backtest apps/api/nexora_api tests/test_paper.py tests/test_dashboard_api.py`: PASS
  - `d:/NEXORA/NEXORA/.venv/Scripts/python.exe -m mypy packages/nexora/paper packages/nexora/backtest apps/api/nexora_api tests/test_paper.py tests/test_dashboard_api.py`: PASS
  - `npm --prefix apps/web run lint`: PASS
  - `npm --prefix apps/web run typecheck`: PASS
  - `npm --prefix apps/web run build`: PASS
- Review: self-review complete; independent review pending
- Blockers: security review and independent review pending before done gate
- Next action: open draft PR and request review evidence for completion
