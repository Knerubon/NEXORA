---
task: "P11"
status: "in_review"
depends_on: ["tasks/P10-backtest.md"]
agents: ["agents/rin/AGENT.md", "agents/quant/AGENT.md", "agents/architect/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md", "agents/security/AGENT.md"]
skills: ["skills/backtesting/SKILL.md", "skills/paper-trading/SKILL.md", "skills/postgres/SKILL.md", "skills/testing/SKILL.md", "skills/security/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md"]
translation_needed: false
---

# P11 — Risk Engine

## เป้าหมาย / traceability
Phase gates / safety NFR: แยก risk responsibility ก่อน Paper Execution; เป็น planning refinement ตาม ADR-007 ไม่อนุญาต live trading

## Entry gate / context
อ่าน [AGENTS.md](../AGENTS.md) และ exact paths ใน metadata; ตรวจ status, execution, review และ merge evidence ของทุก dependency ก่อน ready
เลข phase เป็นลำดับส่งมอบ ไม่ใช่ runtime dependency; contracts ต้องเป็น pure domain ไม่ขึ้นกับ UI/API/DB
Blocker: ต้องยืนยัน dependency completion evidence และ decisions ด้านล่างยัง pending; ห้ามใช้ roadmap เป็น implementation evidence
หากเพิ่ม context ให้บันทึก exact path + เหตุผลก่อนอ่าน; scoped AGENTS.md และ dependency evidence เป็นข้อยกเว้น

## Scope / deliverables
Proposed path: `packages/nexora/risk` (ยังไม่มี; Architect ยืนยัน boundary ก่อนสร้าง), `packages/nexora/backtest` integration, `tests`, `infra`
Deliverables: pure RiskDecision contract (allow/reject/size/reasons), versioned policy/state, audit และ P10 replay integration; Signal Engine ไม่ import execution

Phase 1: research/backtest/local paper simulation เท่านั้น; ห้าม live/demo broker orders, secrets, public DB/MT5 และ unverified OX semantics
P1–P4 behavior/history คงเดิม; contract change ต้อง explicit ADR/version/migration ไม่ rewrite output ของ run เดิม

## Decision gates / ขั้นตอน
1. ล็อก input ResearchSignal + simulated account/exposure/equity + quality/price snapshot + versioned limits ณ decision time
2. Quant ตัดสิน position sizing, per-trade/max risk, aggregate/symbol exposure, max drawdown, daily loss, trading-day timezone/reset, currency/units และ size quantization; ไม่เดา numeric defaults
3. Fail closed สำหรับ invalid/missing/stale price/account data, undefined equity/stop distance หรือ unsupported units; decisions มี source refs/reasons/effective policy version
4. Risk Engine อยู่ก่อน simulator: Signal -> RiskDecision -> local Paper Execution; restart/duplicate/reservation/release/kill-switch policy ต้อง atomic/idempotent และ persist/reconcile ได้
5. เชื่อม P10 replay harness เปรียบเทียบ accepted/rejected decisions และ sizing กับ policy fixtures; versioned research baselines เดิมยัง reproduce ได้

## Acceptance / validation
- [x] hand-calculated sizing/exposure/daily-loss/drawdown thresholds ตรวจ below/at/above limits, rounding, zero/negative inputs และ multi-symbol account cases
- [x] stale/missing account/quality/price และ unsupported currency/units reject พร้อม reasons; ไม่มี silent permissive fallback
- [x] kill switch, daily reset/timezone boundary, concurrent proposals และ duplicate/restart ไม่เกิน aggregate budget หรือเพิ่ม reservation ซ้ำ
- [x] ทุก decision trace signal/dataset/account snapshot/config/policy version; same inputs/state replay ได้ decisions และ resulting risk state เดิม
- [x] P10 replay integration มี parity/explicit policy-version differences; Risk ไม่ขึ้นกับ API/UI/DB และไม่มี broker order adapter
- [ ] Security boundary review และ crash/recovery evidence ผ่านก่อนส่ง contract ให้ P12
- [ ] ผ่าน root Definition of Done; มี exact commands/results, handoff, review และ merge evidence ก่อน done

ใช้ commands ใน docs/development.md ตาม changed scope; behavior ต้องมี synthetic golden/boundary/replay tests และ relevant lint/type/integration checks
Unavailable checks ระบุ not_run พร้อมเหตุผล; docs-only ตรวจ metadata, links, task graph และ git diff --check ไม่อ้าง application tests ผ่าน

## Handoff
Rin -> Architect/Quant decisions (ตาม scope) -> Developer -> Tester -> Reviewer (+ Security เมื่อมี persistence/network/risk/paper boundary) -> Rin
FAIL ให้ expected/actual + minimal reproduction; self-review ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: pure risk policy/decision engine, replay integration with P10 signals, and deterministic decision/state stores implemented
- Context additions: [ADR-015](../docs/decisions/ADR-015-risk-engine-policy.md), [migration 005](../infra/migrations/005_risk_decisions.sql)
- Decisions: fail-closed on unknown quality/stale price/currency mismatch, quantized sizing, idempotent proposal decisions, kill-switch and daily reset behavior
- Changed files / commit / PR: `packages/nexora/risk/*`, `packages/nexora/backtest/service.py`, `apps/api/nexora_api/main.py`, `tests/test_risk.py`, `tests/test_dashboard_api.py`
- Checks:
  - `& .venv/Scripts/python.exe -m pytest tests/test_risk.py tests/test_backtest.py tests/test_dashboard_api.py`: PASS
  - `& .venv/Scripts/python.exe -m ruff check packages/nexora/risk packages/nexora/backtest apps/api/nexora_api tests/test_risk.py tests/test_dashboard_api.py`: PASS
  - `& .venv/Scripts/python.exe -m mypy packages/nexora/risk packages/nexora/backtest apps/api/nexora_api tests/test_risk.py tests/test_dashboard_api.py`: PASS
- Review: self-review complete; independent review pending
- Blockers: security review evidence pending before P12 delivery
- Next action: implement P12 paper simulator consuming RiskDecision contracts
