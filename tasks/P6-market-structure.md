---
task: "P6"
status: "in_review"
depends_on: ["tasks/P5-matrix.md"]
agents: ["agents/rin/AGENT.md", "agents/quant/AGENT.md", "agents/architect/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md"]
skills: ["skills/market-structure/SKILL.md", "skills/pnf/SKILL.md", "skills/postgres/SKILL.md", "skills/testing/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md"]
translation_needed: false
---

# P6 — Market Structure

## เป้าหมาย / traceability
FR-05 ส่วน structural highs/lows และ candidate support/resistance; regime แยก P7

## Entry gate / context
อ่าน [AGENTS.md](../AGENTS.md) และ exact paths ใน metadata; ตรวจ status, execution, review และ merge evidence ของทุก dependency ก่อน ready
เลข phase เป็นลำดับส่งมอบ ไม่ใช่ runtime dependency; contracts ต้องเป็น pure domain ไม่ขึ้นกับ UI/API/DB
Blocker: ต้องยืนยัน dependency completion evidence และ decisions ด้านล่างยัง pending; ห้ามใช้ roadmap เป็น implementation evidence
หากเพิ่ม context ให้บันทึก exact path + เหตุผลก่อนอ่าน; scoped AGENTS.md และ dependency evidence เป็นข้อยกเว้น

## Scope / deliverables
Paths: `packages/nexora/structure`, `tests`, `infra`
Deliverables: versioned confirmed pivot/S&R contracts, decision table, persistence และ causal rebuild

Phase 1: research/backtest/local paper simulation เท่านั้น; ห้าม live/demo broker orders, secrets, public DB/MT5 และ unverified OX semantics
P1–P4 behavior/history คงเดิม; contract change ต้อง explicit ADR/version/migration ไม่ rewrite output ของ run เดิม

## Decision gates / ขั้นตอน
1. Quant เสนอ pivot confirmation, equal highs/lows, S/R creation/touch/break/expiry และ per-resolution aggregation พร้อม synthetic expectations
2. แยก occurrence_time/confirmation_time และ source transition refs; consume เฉพาะข้อมูลพร้อม ณ decision time
3. ระบุ candidate/confirmed/invalidated/unavailable states และ versioned lifecycle; levels เป็น candidate ไม่ใช่ certainty

## Acceptance / validation
- [x] golden flat/extension/reversal/equal-level/gap cases ตรวจ highs/lows และ S/R lifecycle
- [x] future confirmation ไม่เปลี่ยน previously published structure; consumers ใช้ confirmation time
- [x] symbol/resolution isolation, stale inputs และ insufficient history ให้ explicit unavailable state
- [x] persist/rebuild/restart deterministic; ทุก level มี source refs และ rule/config version
- [ ] ผ่าน root Definition of Done; มี exact commands/results, handoff, review และ merge evidence ก่อน done

ใช้ commands ใน docs/development.md ตาม changed scope; behavior ต้องมี synthetic golden/boundary/replay tests และ relevant lint/type/integration checks
Unavailable checks ระบุ not_run พร้อมเหตุผล; docs-only ตรวจ metadata, links, task graph และ git diff --check ไม่อ้าง application tests ผ่าน

## Handoff
Rin -> Architect/Quant decisions (ตาม scope) -> Developer -> Tester -> Reviewer (+ Security เมื่อมี persistence/network/risk/paper boundary) -> Rin
FAIL ให้ expected/actual + minimal reproduction; self-review ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: causal pivot confirmation, candidate level lifecycle, and invalidation policy implemented with deterministic persistence/rebuild
- Context additions: [ADR-009](../docs/decisions/ADR-009-market-structure-lifecycle.md), [migration 002](../infra/migrations/002_structure_regime_signals.sql)
- Decisions: 3-transition confirmation window, occurrence vs confirmation timestamp split, explicit confirmed/invalidated/unavailable lifecycle
- Changed files / commit / PR: `packages/nexora/structure/*`, `tests/test_structure.py`, `docs/decisions/ADR-009-market-structure-lifecycle.md`
- Checks:
  - `& .venv/Scripts/python.exe -m pytest tests/test_structure.py`: PASS
  - `& .venv/Scripts/python.exe -m ruff check packages/nexora/structure tests/test_structure.py`: PASS
  - `& .venv/Scripts/python.exe -m mypy packages/nexora/structure tests/test_structure.py`: PASS
- Review: self-review complete; independent review pending
- Blockers: none for implementation scope
- Next action: integrate with P7 regime classifier review
