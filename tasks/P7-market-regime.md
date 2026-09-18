---
task: "P7"
status: "blocked"
depends_on: ["tasks/P6-market-structure.md"]
agents: ["agents/rin/AGENT.md", "agents/quant/AGENT.md", "agents/architect/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md"]
skills: ["skills/market-structure/SKILL.md", "skills/adaptive-box/SKILL.md", "skills/postgres/SKILL.md", "skills/testing/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md"]
translation_needed: false
---

# P7 — Market Regime

## เป้าหมาย / traceability
FR-05 ส่วน trend/range/high-volatility metadata; ไม่เพิ่ม signal หรือ execution rules

## Entry gate / context
อ่าน [AGENTS.md](../AGENTS.md) และ exact paths ใน metadata; ตรวจ status, execution, review และ merge evidence ของทุก dependency ก่อน ready
เลข phase เป็นลำดับส่งมอบ ไม่ใช่ runtime dependency; contracts ต้องเป็น pure domain ไม่ขึ้นกับ UI/API/DB
Blocker: ต้องยืนยัน dependency completion evidence และ decisions ด้านล่างยัง pending; ห้ามใช้ roadmap เป็น implementation evidence
หากเพิ่ม context ให้บันทึก exact path + เหตุผลก่อนอ่าน; scoped AGENTS.md และ dependency evidence เป็นข้อยกเว้น

## Scope / deliverables
Paths: `packages/nexora/market_regime`, `tests`, `infra`
Deliverables: RegimeState/schema, causal decision table, persisted evidence และ rebuild

Phase 1: research/backtest/local paper simulation เท่านั้น; ห้าม live/demo broker orders, secrets, public DB/MT5 และ unverified OX semantics
P1–P4 behavior/history คงเดิม; contract change ต้อง explicit ADR/version/migration ไม่ rewrite output ของ run เดิม

## Decision gates / ขั้นตอน
1. Quant กำหนด inputs/lookback/threshold units, warm-up, transition/hysteresis policy และ overlap/precedence ระหว่าง trend/range/high-volatility ใน ADR
2. ระบุ confirmation/effective time, source structure/Matrix refs และ unknown/stale states
3. Version rules/config; ห้ามตั้ง thresholds จาก OX screenshot หรือข้อมูลอนาคต

## Acceptance / validation
- [ ] golden trend/range/high-volatility และ threshold/overlap transitions ตรง decision table
- [ ] warm-up, flat/zero-volatility, gaps และ stale data ไม่สร้าง regime certainty ปลอม
- [ ] prefix invariance, replay/restart และ interleaved symbols/resolutions ผ่าน
- [ ] ทุก classification มี reasons, input refs, effective time, rule/config versions และ persist/rebuild evidence
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
