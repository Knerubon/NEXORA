---
task: P4
status: in_review
depends_on: ["tasks/P3-pnf-engine.md"]
agents: ["agents/rin/AGENT.md","agents/quant/AGENT.md","agents/researcher/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md"]
skills: ["skills/adaptive-box/SKILL.md","skills/pnf/SKILL.md","skills/testing/SKILL.md"]
docs: ["docs/requirements.md","docs/architecture.md","docs/research/adaptive-box.md"]
translation_needed: false
---

# P4 — Adaptive Box

## เป้าหมาย / traceability
เพิ่ม sizing policy ที่ fixed-first และ causal โดยรักษา P&F contract
Requirement: FR-03; shared deterministic engines

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
ตรวจ Execution record และ merge/check evidence ของ P3 ก่อนเปลี่ยนเป็น ready; dependency file อ่านเฉพาะ status/evidence ไม่โหลด context ของ phase นั้นต่อทั้งหมด
Blocker/decision: ยังไม่มี approved adaptive formula; ทำ decision/experiments ก่อนเปิด mode
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `packages/adaptive_box`, `packages/pnf`, `tests`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: sizing policy/ADR, fixed/adaptive implementations, causal tests และ experiment note
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. ตรวจ accepted P3 rules; เพิ่ม ADR สำหรับ volatility inputs/formula, warm-up, rounding/clamps และ box effective boundary
2. implement fixed mode เป็น baseline; ทดลอง ATR-derived candidate ด้วย past-only inputs โดยเก็บ assumptions/ผลจริงใน research
3. เชื่อม sizing result กับ engine ผ่าน contract ไม่ผ่าน UI/API; ทุก derived run เก็บ rule/version/config
4. กำหนด empty/warm-up/missing bar/zero volatility states; ไม่เขียน historical columns ใหม่ใน run เดิม
5. เปรียบเทียบ fixed/adaptive บน frozen dataset; ไม่เลือก formula จากอนาคตหรือ chart appearance

## Acceptance / validation
- [x] fixed mode ให้ output เท่ากับ P3 baseline
- [x] append future events ไม่เปลี่ยน output prefix; ไม่ใช้ unfinished bar information ก่อนพร้อม
- [x] warm-up, zero/invalid volatility, precision, price gaps และ resize boundary ตรง golden expectations
- [x] replay/restart deterministic และ trace effective box size ได้ทุก transition
- [x] adaptive candidate มี rule/version, limitations และ evidence; ไม่มีข้ออ้าง validated profitability
- [ ] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใช้ commands ที่ P1 จัดทำและตรวจว่าใช้งานได้กับ checkout ปัจจุบัน; บันทึก exact command/result ด้านล่าง ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Quant decision -> Developer -> Tester -> Reviewer -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: fixed/adaptive sizing policies, adaptive runner integration, transition box-size tracing, and causal tests implemented
- Context additions: [ADR-006](../docs/decisions/ADR-006-adaptive-box-sizing.md); experiment note in [adaptive-box research](../docs/research/adaptive-box.md)
- Decisions: fixed parity baseline, ATR-derived past-only candidate, warm-up hold-last, clamp + quantize boundaries, non-rewrite history policy
- Changed files / commit / PR: `packages/nexora/adaptive_box/*`, `packages/nexora/pnf/engine.py`, `packages/nexora/pnf/models.py`, `tests/test_adaptive_box.py`, `docs/decisions/ADR-006-adaptive-box-sizing.md`, `docs/research/adaptive-box.md`
- Checks:
  - `& .venv/Scripts/python.exe -m pytest tests/test_pnf.py tests/test_adaptive_box.py`: PASS
  - `& .venv/Scripts/python.exe -m ruff check packages/nexora/pnf packages/nexora/adaptive_box tests/test_pnf.py tests/test_adaptive_box.py`: PASS
  - `& .venv/Scripts/python.exe -m mypy packages/nexora/pnf packages/nexora/adaptive_box tests/test_pnf.py tests/test_adaptive_box.py`: PASS
  - `& .venv/Scripts/python.exe -m pytest tests`: PASS (29 tests, 2 warnings)
- Review: self-review complete; independent review pending
- Blockers: none
- Next action: open PR for P4 and request independent review
