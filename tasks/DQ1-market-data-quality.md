---
task: "DQ1"
status: "blocked"
depends_on: ["tasks/P2-market-data.md"]
agents: ["agents/rin/AGENT.md", "agents/architect/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md", "agents/security/AGENT.md"]
skills: ["skills/market-data/SKILL.md", "skills/postgres/SKILL.md", "skills/testing/SKILL.md", "skills/security/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md", "docs/decisions/ADR-004-market-data.md"]
translation_needed: false
---

# DQ1 — Market Data Quality / Observability

## เป้าหมาย / traceability
FR-01, FR-08 System และ traceable logs NFR: additive follow-up ของ P2; ไม่ reopen หรือ rewrite completed P2 contract

## Entry gate / context
อ่าน [AGENTS.md](../AGENTS.md) และ exact paths ใน metadata; ตรวจ status, execution, review และ merge evidence ของทุก dependency ก่อน ready
เลข phase เป็นลำดับส่งมอบ ไม่ใช่ runtime dependency; contracts ต้องเป็น pure domain ไม่ขึ้นกับ UI/API/DB
Blocker: ต้องยืนยัน dependency completion evidence และ decisions ด้านล่างยัง pending; ห้ามใช้ roadmap เป็น implementation evidence
หากเพิ่ม context ให้บันทึก exact path + เหตุผลก่อนอ่าน; scoped AGENTS.md และ dependency evidence เป็นข้อยกเว้น

## Scope / deliverables
Paths: `packages/nexora/market_data`, `tests`, `infra`, `docs/development.md`
Deliverables: sidecar quality events/health snapshots, metrics contract, reconnect/backfill audit และ local troubleshooting runbook; P9 consume System view

Phase 1: research/backtest/local paper simulation เท่านั้น; ห้าม live/demo broker orders, secrets, public DB/MT5 และ unverified OX semantics
P1–P4 behavior/history คงเดิม; contract change ต้อง explicit ADR/version/migration ไม่ rewrite output ของ run เดิม

## Decision gates / ขั้นตอน
1. ล็อก quality flags, configurable freshness/latency thresholds, event_time/received_at/clock skew และ market-session closure semantics
2. แยก observed sequence gap กับ suspected missing ticks; source ไม่มี sequence ห้ามอ้างรู้จำนวน ticks ที่หาย
3. นิยาม disconnect/reconnect/backfill/duplicate/out-of-order/reject counters และ readiness/liveness; structured logs ไม่มี credentials/account details
4. Quality metadata เป็น sidecar/versioned data; ไม่แก้ raw events, ordering/dedup หรือ replay outputs ของ P2; historical quality ใช้ recorded observation time ไม่ใช้ wall clock ปัจจุบัน

## Acceptance / validation
- [ ] fake feed ทดสอบ stale/disconnect/reconnect/gap/backfill/clock-skew และ closed-session cases ด้วย expected flags/counters
- [ ] latency units, configurable thresholds และ readiness/liveness ชัด; unknown completeness แสดง unknown
- [ ] reconnect/backfill ไม่สร้าง duplicate downstream transitions และ audit rejected/duplicate events ได้
- [ ] P2 frozen fixtures/replay outputs ไม่เปลี่ยน; sidecar restart/rebuild deterministic และ trace event/run refs ได้
- [ ] ไม่มี secrets ใน logs/metrics; local-only health access และ versioned schema ให้ P9/P10/P11/P13
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
