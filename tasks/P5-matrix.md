---
task: "P5"
status: "in_review"
depends_on: ["tasks/P4-adaptive-box.md"]
agents: ["agents/rin/AGENT.md", "agents/quant/AGENT.md", "agents/architect/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md"]
skills: ["skills/market-structure/SKILL.md", "skills/pnf/SKILL.md", "skills/adaptive-box/SKILL.md", "skills/postgres/SKILL.md", "skills/testing/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md", "docs/decisions/ADR-005-pnf-fixed-box-rules.md", "docs/decisions/ADR-006-adaptive-box-sizing.md"]
translation_needed: false
---

# P5 — Multi-Resolution Matrix

## เป้าหมาย / traceability
FR-04: Fast/Medium/Slow อย่างน้อยสาม independent resolutions; แยก structure, regime และ signal rules ไป P6–P8

## Entry gate / context
อ่าน [AGENTS.md](../AGENTS.md) และ exact paths ใน metadata; ตรวจ status, execution, review และ merge evidence ของทุก dependency ก่อน ready
เลข phase เป็นลำดับส่งมอบ ไม่ใช่ runtime dependency; contracts ต้องเป็น pure domain ไม่ขึ้นกับ UI/API/DB
Blocker: ต้องยืนยัน dependency completion evidence และ decisions ด้านล่างยัง pending; ห้ามใช้ roadmap เป็น implementation evidence
หากเพิ่ม context ให้บันทึก exact path + เหตุผลก่อนอ่าน; scoped AGENTS.md และ dependency evidence เป็นข้อยกเว้น

## Scope / deliverables
Paths: `packages/nexora/matrix`, P3/P4 public contracts, `tests`, `infra` เมื่อ persist snapshots; ไม่แก้ completed engine behavior
Deliverables: versioned MatrixConfig/MatrixSnapshot, fan-out orchestration, persistence/rebuild contract และ schema/examples ให้ P6–P9

Phase 1: research/backtest/local paper simulation เท่านั้น; ห้าม live/demo broker orders, secrets, public DB/MT5 และ unverified OX semantics
P1–P4 behavior/history คงเดิม; contract change ต้อง explicit ADR/version/migration ไม่ rewrite output ของ run เดิม

## Decision gates / ขั้นตอน
1. ล็อก resolution identity, config/version/effective boundary และ event watermark ของ snapshot
2. แต่ละ resolution ใช้ P3/P4 public contract พร้อม isolated state; Fast/Medium/Slow ไม่ใช่ OX 10/20/30 mapping
3. ระบุ current X/O direction/latest transition, warm-up/unavailable/stale/conflicting states; ไม่สร้าง pivot/regime/signal สูตรใน task นี้
4. นิยาม snapshot persistence/restart และ configuration change เป็น explicit new version/run

## Acceptance / validation
- [x] สาม configs ไม่ share mutable state; interleaved symbols/resolutions ให้ผลเท่ากับ isolated replay
- [x] golden sequence ตรวจ direction/latest transition/watermark; empty/warm-up/conflicting directions แสดงตามข้อมูลจริง
- [x] duplicate/out-of-order เป็นไปตาม P2/P3 contracts; snapshot/restart/rebuild เท่ากับ continuous replay
- [x] append future events ไม่เปลี่ยน snapshot prefix; config change ไม่ rewrite historical snapshots
- [x] persisted snapshot trace ถึง event identities, P&F/sizing/config versions; ส่ง schema/examples รวม stale/error ให้ downstream
- [ ] ผ่าน root Definition of Done; มี exact commands/results, handoff, review และ merge evidence ก่อน done

ใช้ commands ใน docs/development.md ตาม changed scope; behavior ต้องมี synthetic golden/boundary/replay tests และ relevant lint/type/integration checks
Unavailable checks ระบุ not_run พร้อมเหตุผล; docs-only ตรวจ metadata, links, task graph และ git diff --check ไม่อ้าง application tests ผ่าน

## Handoff
Rin -> Architect/Quant decisions (ตาม scope) -> Developer -> Tester -> Reviewer (+ Security เมื่อมี persistence/network/risk/paper boundary) -> Rin
FAIL ให้ expected/actual + minimal reproduction; self-review ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: matrix contracts/fan-out orchestration/state classification and snapshot persistence/rebuild implemented
- Context additions: [ADR-008](../docs/decisions/ADR-008-multi-resolution-matrix.md), [migration 002](../infra/migrations/002_structure_regime_signals.sql)
- Decisions: resolution isolation by independent runners, sequence watermarking, explicit warmup/stale/unavailable statuses, append-only snapshot policy
- Changed files / commit / PR: `packages/nexora/matrix/*`, `tests/test_matrix.py`, `infra/migrations/002_structure_regime_signals.sql`, `docs/decisions/ADR-008-multi-resolution-matrix.md`
- Checks:
  - `& .venv/Scripts/python.exe -m pytest tests/test_matrix.py`: PASS
  - `& .venv/Scripts/python.exe -m ruff check packages/nexora/matrix tests/test_matrix.py`: PASS
  - `& .venv/Scripts/python.exe -m mypy packages/nexora/matrix tests/test_matrix.py`: PASS
- Review: self-review complete; independent review pending
- Blockers: none for implementation scope; dependency review evidence from prior tasks remains historical
- Next action: package with P6-P8 changes for integrated review PR
