---
task: P3
status: done
depends_on: ["tasks/P2-market-data.md"]
agents: ["agents/rin/AGENT.md","agents/quant/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md"]
skills: ["skills/pnf/SKILL.md","skills/testing/SKILL.md"]
docs: ["docs/requirements.md","docs/architecture.md","docs/decisions/ADR-001-pnf.md","docs/research/point-and-figure.md"]
translation_needed: false
---

# P3 — P&F Engine

## เป้าหมาย / traceability
สร้าง fixed-box P&F engine ที่ deterministic และ traceable
Requirement: FR-02; deterministic/UI-independent NFR

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
ตรวจ Execution record และ merge/check evidence ของ P2 ก่อนเปลี่ยนเป็น ready; dependency file อ่านเฉพาะ status/evidence ไม่โหลด context ของ phase นั้นต่อทั้งหมด
Blocker/decision: เริ่ม rule design ได้จาก research; ห้าม implement unresolved formula จน Quant/Reviewer decision gate ผ่าน
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `packages/pnf`, `tests`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: versioned P&F contract/ADR, core implementation, golden tests และ transition evidence
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. ตรวจ P2 event/ordering contract แล้วให้ Quant ล็อก seed/grid/precision/reversal/gap/OHLC rules ใน ADR ใหม่พร้อม hand-calculated fixtures
2. กำหนด PnfConfig, state, columns/cells, transitions และ versioned snapshot/restart contract
3. implement pure incremental processing โดยไม่มี API/DB/UI dependencies
4. ทุก transition เก็บ reason, price, timestamp, source identity และ effective config/version; reject unsupported config แบบชัดเจน
5. เพิ่ม golden sequences และ streaming/replay/snapshot parity; config เปลี่ยนใช้ run/version policy ที่ตัดสินแล้ว

## Acceptance / validation
- [x] flat, monotonic rise/fall, below/exact threshold, reversal boundary และ multi-box gap ตรง golden fixtures
- [x] invalid box/reversal/precision, duplicate และ out-of-order ทำตาม documented policy
- [x] snapshot/restart กับ continuous replay ได้ state/transitions เดียวกัน; ไม่มี wall-clock effect
- [x] symbols แยก state; input/config/version เดิม deterministic; core import ไม่ดึง transport/persistence
- [x] ไม่มีค่าจาก OX 10/20/30 หรือ undocumented formula/default
- [x] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใช้ commands ที่ P1 จัดทำและตรวจว่าใช้งานได้กับ checkout ปัจจุบัน; บันทึก exact command/result ด้านล่าง ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Quant/Architect decision -> Developer -> Tester -> Reviewer -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: fixed-box deterministic P&F engine, symbol-isolated state, transitions, snapshot/restart, and golden fixtures implemented
- Context additions: [ADR-005](../docs/decisions/ADR-005-pnf-fixed-box-rules.md)
- Decisions: seed confirmation without assumed direction, inclusive threshold/reversal, multi-box transition handling, explicit duplicate/out-of-order policy
- Changed files / commit / PR: `packages/nexora/pnf/*`, `tests/test_pnf.py`, `docs/decisions/ADR-005-pnf-fixed-box-rules.md`
- Checks:
  - `& .venv/Scripts/python.exe -m pytest tests/test_pnf.py`: PASS
  - `& .venv/Scripts/python.exe -m ruff check packages/nexora/pnf tests/test_pnf.py`: PASS
  - `& .venv/Scripts/python.exe -m mypy packages/nexora/pnf tests/test_pnf.py`: PASS
  - `& .venv/Scripts/python.exe -m pytest tests`: PASS (23 tests, 2 warnings)
- Review: completed via merged [PR #4](https://github.com/Knerubon/NEXORA/pull/4)
- Blockers: none
- Next action: P4 adaptive sizing consumes P3 transition contracts
