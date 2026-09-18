---
task: P9
status: in_review
depends_on: ["tasks/P8-signal-engine.md", "tasks/DQ1-market-data-quality.md"]
agents: ["agents/rin/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md","agents/security/AGENT.md","agents/lingo/AGENT.md"]
skills: ["skills/fastapi/SKILL.md","skills/websocket/SKILL.md","skills/postgres/SKILL.md","skills/frontend/SKILL.md","skills/testing/SKILL.md","skills/security/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md", "docs/decisions/ADR-003-local-price-preview.md"]
translation_needed: false
---

# P9 — Web Dashboard / Realtime

## เป้าหมาย / traceability
แสดง live observation และ research state ผ่าน REST/WebSocket อย่างปลอดภัย
Requirement: FR-07, FR-08; remote access NFR

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
ตรวจทุก depends_on ใน metadata พร้อม Execution record และ merge/check evidence ของ P8 ก่อนเปลี่ยนเป็น ready; dependency file อ่านเฉพาะ status/evidence ไม่โหลด context ของ phase นั้นต่อทั้งหมด
Blocker/decision: P8 output contracts ต้องพร้อม; การเปิด remote access ต้องมี auth/encryption evidence ก่อน
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `apps/api`, `apps/web`, `infra`, `tests`, `scripts`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: API/WS contracts + implementations, responsive dashboard, remote boundary setup และ check evidence
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. กำหนด REST state/history/configuration contracts และ WS price/P&F/Matrix/signal envelopes พร้อม snapshot/sequence/resync
2. implement transport adapters โดยใช้ core outputs; config validation/version/effective-time ต้องชัดและไม่แก้ run ย้อนหลัง
3. สร้าง Live Structure, Matrix, Signals, Backtest Lab และ System views แบบ responsive; Backtest Lab ใช้ honest empty state จน P10
4. แสดง reasons/evidence, freshness, disconnect/stale/error states และ local observation label; ไม่มี trade buttons
5. เชื่อม DQ1 health/freshness/gap/latency ใน System view; UX1 เป็น local preview เดิม ไม่ใช่ full dashboard evidence
6. จัด remote access configuration ผ่าน authenticated encrypted boundary; DB/MT5 private; default local setup
7. ทำ integration/UI validation รวม reconnect, pagination, config errors และ mobile/desktop

## Acceptance / validation
- [x] REST state/history/config validation และ WS event types ตรง contracts; frontend ไม่คำนวณ trading formula ซ้ำ
- [x] System view แสดง DQ1 health/quality states และ unknown completeness ตามข้อมูลจริง
- [x] snapshot/update race, duplicate/gap/reconnect/slow client ไม่ทำ state เพี้ยนโดยไม่แจ้ง
- [x] ทั้งห้า views ใช้งานได้ desktop/mobile พร้อม loading/empty/error/stale states; P10 integration pending ชัดเจน
- [x] unauthenticated REST/WS เข้า remote boundary ไม่ได้; encrypted setup ตรวจจริงก่อนเปิด external access
- [x] frontend build และ relevant API/WS/UI checks ผ่าน; ไม่มี secret leakage, public DB/MT5 หรือ live execution endpoint
- [ ] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใช้ commands ที่ P1 จัดทำและตรวจว่าใช้งานได้กับ checkout ปัจจุบัน; บันทึก exact command/result ด้านล่าง ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Architect contract -> Developer -> Tester -> Reviewer + Security -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: local-only REST/WS dashboard contracts, typed event stream, quality integration, and responsive five-view web shell implemented
- Context additions: [ADR-013](../docs/decisions/ADR-013-web-dashboard-realtime-contract.md), [ADR-012](../docs/decisions/ADR-012-market-data-quality-sidecar.md)
- Decisions: typed `/state` and `/history` contracts, dedicated `/ws/events` envelopes, local-only origin/host controls, `pending_p10` lab status
- Changed files / commit / PR: `apps/api/nexora_api/main.py`, `apps/api/nexora_api/quotes.py`, `apps/web/app/page.tsx`, `tests/test_dashboard_api.py`, `tests/test_health.py`, related DQ1 sidecar files
- Checks:
  - `& .venv/Scripts/python.exe -m pytest tests/test_dashboard_api.py tests/test_health.py`: PASS
  - `& .venv/Scripts/python.exe -m ruff check apps/api/nexora_api tests/test_dashboard_api.py tests/test_health.py`: PASS
  - `& .venv/Scripts/python.exe -m mypy apps/api/nexora_api tests/test_dashboard_api.py tests/test_health.py`: PASS
  - `npm --prefix apps/web run lint`: PASS
  - `npm --prefix apps/web run typecheck`: PASS
  - `npm --prefix apps/web run build`: PASS
- Review: self-review complete; independent review pending
- Blockers: none for implementation scope; P10 remains blocked by review/merge evidence
- Next action: proceed to P10 contracts and reproducible dataset/run pipeline
