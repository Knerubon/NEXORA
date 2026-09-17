---
task: P5
status: blocked
depends_on: ["tasks/P4-adaptive-box.md"]
agents: ["agents/rin/AGENT.md","agents/quant/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md"]
skills: ["skills/market-structure/SKILL.md","skills/pnf/SKILL.md","skills/adaptive-box/SKILL.md","skills/postgres/SKILL.md","skills/testing/SKILL.md"]
docs: ["docs/requirements.md","docs/architecture.md"]
translation_needed: false
---

# P5 — Matrix / Structure / Signals

## เป้าหมาย / traceability
รวม Fast/Medium/Slow, structure/regime และ explainable research signals
Requirement: FR-04, FR-05, FR-06

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
ตรวจ Execution record และ merge/check evidence ของ P4 ก่อนเปลี่ยนเป็น ready; dependency file อ่านเฉพาะ status/evidence ไม่โหลด context ของ phase นั้นต่อทั้งหมด
Blocker/decision: สูตร structure/regime/signal ยังต้องมี decision ก่อน implement; signals ใช้เพื่อ research เท่านั้น
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `packages/matrix`, `packages/structure`, `packages/market_regime`, `packages/signals`, `tests`, `infra`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: Matrix/structure/regime/signal contracts + decisions, implementations, persistence และ replay tests
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. กำหนด independent resolution configs และ MatrixSnapshot contract พร้อม current direction/latest transition/freshness
2. Quant ตัดสิน pivot confirmation, candidate S/R, trend/range/high-volatility และ signal rules; นิยาม alignment/strength เฉพาะเมื่อมีสูตรและ evidence
3. implement structure/regime จากข้อมูลที่ confirm แล้ว; แยก occurrence_time/confirmation_time ป้องกัน look-ahead
4. สร้าง ResearchSignal พร้อม human reasons, machine evidence, source refs และ rule/config versions
5. persist snapshots/levels/signals พร้อม rebuild contract; empty/stale/conflicting states ต้องแสดงความไม่พร้อม

## Acceptance / validation
- [ ] อย่างน้อยสาม independent configs ไม่ hard-code OX mapping และไม่ share mutable state
- [ ] sequence ที่รู้คำตอบตรวจ directions/transitions, confirmed pivots, S/R, regimes และ signal/no-signal
- [ ] future pivot confirmation ไม่เปลี่ยน signal ที่เคยออก; unavailable inputs ไม่สร้าง certainty ปลอม
- [ ] ทุก signal trace ไป data/config/rule ได้; rebuild จาก persisted inputs ให้ output เดิม
- [ ] ไม่มี order execution; P6 ได้ schema/examples รวม stale/error states
- [ ] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใช้ commands ที่ P1 จัดทำและตรวจว่าใช้งานได้กับ checkout ปัจจุบัน; บันทึก exact command/result ด้านล่าง ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Quant/Architect decision -> Developer -> Tester -> Reviewer -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: not_started
- Context additions: none
- Decisions: pending
- Changed files / commit / PR: none
- Checks: not_run
- Review: pending
- Blockers: dependency P4 not completed; ดู decision gate ด้านบน
- Next action: ตรวจ dependency completion evidence แล้วทำ decision/contracts ของ task
