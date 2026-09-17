---
task: "P6"
status: "blocked"
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
- [ ] golden flat/extension/reversal/equal-level/gap cases ตรวจ highs/lows และ S/R lifecycle
- [ ] future confirmation ไม่เปลี่ยน previously published structure; consumers ใช้ confirmation time
- [ ] symbol/resolution isolation, stale inputs และ insufficient history ให้ explicit unavailable state
- [ ] persist/rebuild/restart deterministic; ทุก level มี source refs และ rule/config version
- [ ] ผ่าน root Definition of Done; มี exact commands/results, handoff, review และ merge evidence ก่อน done

ใช้ commands ใน docs/development.md ตาม changed scope; behavior ต้องมี synthetic golden/boundary/replay tests และ relevant lint/type/integration checks
Unavailable checks ระบุ not_run พร้อมเหตุผล; docs-only ตรวจ metadata, links, task graph และ git diff --check ไม่อ้าง application tests ผ่าน

## Handoff
Rin -> Architect/Quant decisions (ตาม scope) -> Developer -> Tester -> Reviewer (+ Security เมื่อมี persistence/network/risk/paper boundary) -> Rin
FAIL ให้ expected/actual + minimal reproduction; self-review ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: not_started
- Context additions: ADR-007 สำหรับ numbering, dependency policy และ preserved evidence; docs/development.md สำหรับ validation commands
- Decisions: pending ตาม decision gates; ไม่มีสูตรหรือ numeric defaults ที่อนุมัติใน task นี้
- Changed files / commit / PR: none (implementation)
- Checks: not_run (implementation)
- Review: pending
- Blockers: dependency completion evidence และ decisions ด้านบน
- Next action: ตรวจ dependencies แล้วเสนอ contracts/decisions พร้อม golden expectations ก่อน implementation
