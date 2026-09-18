---
task: "P8"
status: "in_review"
depends_on: ["tasks/P7-market-regime.md"]
agents: ["agents/rin/AGENT.md", "agents/quant/AGENT.md", "agents/architect/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md"]
skills: ["skills/market-structure/SKILL.md", "skills/postgres/SKILL.md", "skills/testing/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md"]
translation_needed: false
---

# P8 — Signal Engine

## เป้าหมาย / traceability
FR-06: explainable research signals; Signal ระบุโอกาส ส่วน P11 Risk ตัดสิน simulated permission/size

## Entry gate / context
อ่าน [AGENTS.md](../AGENTS.md) และ exact paths ใน metadata; ตรวจ status, execution, review และ merge evidence ของทุก dependency ก่อน ready
เลข phase เป็นลำดับส่งมอบ ไม่ใช่ runtime dependency; contracts ต้องเป็น pure domain ไม่ขึ้นกับ UI/API/DB
Blocker: ต้องยืนยัน dependency completion evidence และ decisions ด้านล่างยัง pending; ห้ามใช้ roadmap เป็น implementation evidence
หากเพิ่ม context ให้บันทึก exact path + เหตุผลก่อนอ่าน; scoped AGENTS.md และ dependency evidence เป็นข้อยกเว้น

## Scope / deliverables
Paths: `packages/nexora/signals`, `tests`, `infra`
Deliverables: ResearchSignal contract, rule decisions, event log/rebuild และ schema/examples ให้ P9/P10/P11

Phase 1: research/backtest/local paper simulation เท่านั้น; ห้าม live/demo broker orders, secrets, public DB/MT5 และ unverified OX semantics
P1–P4 behavior/history คงเดิม; contract change ต้อง explicit ADR/version/migration ไม่ rewrite output ของ run เดิม

## Decision gates / ขั้นตอน
1. Quant ล็อก signal/no-signal, confirmation, expiry, repeat/dedup/cooldown และ conflicting resolutions policy ก่อน implement
2. ใช้ confirmed structure/regime/Matrix ที่ available ณ decision time เท่านั้น
3. เก็บ human reasons, machine reason_codes/evidence, data refs, signal/config/engine versions; ไม่ออก order หรือกำหนด risk limits

## Acceptance / validation
- [x] golden sequences ตรวจ signal/no-signal, conflict, expired/stale/insufficient inputs และ repeat policy
- [x] future pivot/regime confirmation ไม่เปลี่ยน signal prefix; occurrence/confirmation/decision timestamps trace ได้
- [x] ทุก signal มี reasons + machine evidence + source/config/rule refs; persisted inputs rebuild ได้ผลเดิม
- [x] duplicate/restart ไม่สร้าง signal ซ้ำ; isolation และ rejection/error schema ตรวจได้
- [x] ไม่มี order calls, position sizing หรือ risk approval ใน Signal Engine; downstream ได้ versioned schema/examples
- [ ] ผ่าน root Definition of Done; มี exact commands/results, handoff, review และ merge evidence ก่อน done

ใช้ commands ใน docs/development.md ตาม changed scope; behavior ต้องมี synthetic golden/boundary/replay tests และ relevant lint/type/integration checks
Unavailable checks ระบุ not_run พร้อมเหตุผล; docs-only ตรวจ metadata, links, task graph และ git diff --check ไม่อ้าง application tests ผ่าน

## Handoff
Rin -> Architect/Quant decisions (ตาม scope) -> Developer -> Tester -> Reviewer (+ Security เมื่อมี persistence/network/risk/paper boundary) -> Rin
FAIL ให้ expected/actual + minimal reproduction; self-review ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: explainable research signal contract, deterministic signal engine, dedup/cooldown/expiry policy, and persistence/rebuild contract implemented
- Context additions: [ADR-011](../docs/decisions/ADR-011-signal-evidence-policy.md), [migration 002](../infra/migrations/002_structure_regime_signals.sql)
- Decisions: decision-time-only inputs, human + machine evidence fields, active-signal dedup, event-window cooldown and expiry
- Changed files / commit / PR: `packages/nexora/signals/*`, `tests/test_signals.py`, `docs/decisions/ADR-011-signal-evidence-policy.md`
- Checks:
  - `& .venv/Scripts/python.exe -m pytest tests/test_signals.py`: PASS
  - `& .venv/Scripts/python.exe -m ruff check packages/nexora/signals tests/test_signals.py`: PASS
  - `& .venv/Scripts/python.exe -m mypy packages/nexora/signals tests/test_signals.py`: PASS
- Review: self-review complete; independent review pending
- Blockers: none for implementation scope
- Next action: open integrated review PR for P5-P8
