---
task: UX1
status: in_progress
depends_on: ["tasks/P1-foundation.md"]
agents: ["agents/rin/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md","agents/security/AGENT.md"]
skills: ["skills/market-data/SKILL.md","skills/fastapi/SKILL.md","skills/websocket/SKILL.md","skills/frontend/SKILL.md","skills/testing/SKILL.md","skills/security/SKILL.md"]
docs: ["docs/requirements.md","docs/architecture.md","docs/development.md","docs/decisions/ADR-003-local-price-preview.md"]
translation_needed: false
---

# UX1 — Local MT5 price preview

## User-requested scope / dependency conflict
ผู้ใช้ขอ layout ตาม screenshot และเชื่อมราคาจาก MT5 ที่เปิดอยู่โดยตรง
P1 merged (#2, c44f72b) แต่ independent review ยัง pending; P2/P6 gates ยังไม่ผ่าน จึงไม่ mark phases เหล่านั้น ready/done
งานนี้เป็น bounded local quote/UI preview ตามคำขอใหม่ ไม่ใช่การรับรอง P2 ingestion/replay หรือ P6 dashboard ครบ scope
Conflict ที่คงไว้: P&F/Matrix/structure/live research state ต้องรอ P2–P5 contracts ตาม requirements; หยุดเฉพาะส่วนนั้นและแสดง honest empty state
ไม่มีการเปลี่ยน source-of-truth, trading formulas, persistence contracts หรือ remote access

## Context additions
- docs/development.md: existing run commands and local setup
- docs/decisions/ADR-003-local-price-preview.md: preview transport/source/freshness contract
- apps/web/AGENTS.md: scoped instruction (untracked generated file; preserve)
- apps/web/node_modules/next/dist/docs/01-app/01-getting-started/05-server-and-client-components.md: installed Next.js client boundary guidance before UI edits
- apps/web/node_modules/next/dist/docs/01-app/03-api-reference/01-directives/use-client.md: installed Next.js client lifecycle guidance

## Deliverables / acceptance
- [ ] Read existing configured terminal only; visible XAUUSD-STD quote, no account credentials/info or order APIs
- [ ] Local API snapshot/WS + safe unavailable/disconnected/stale/error states; reject foreign origins
- [ ] Screenshot-inspired chart workspace with real Bid/Ask and timestamp; no fake P&F/Matrix values
- [ ] Bounded session quote chart, source selection and reconnect behavior; no claim of full tick history/replay
- [ ] Offline adapter/API tests; live read-only smoke; frontend lint/type/build and desktop/mobile QA
- [ ] Setup/handoff and draft PR; self-review, independent review pending

## Execution record
- Existing implementation: P1 scaffold only; no market-data adapter
- Discovery: running terminal found; read-only MT5 probe succeeded; XAUUSD-STD is visible, digits=2
- Unknown/untracked files preserved: apps/web/AGENTS.md, apps/web/CLAUDE.md
- Checks/review/commit: pending

## Roadmap reference clarification
ข้อความ P6/P2–P5 และ context additions ด้านบนเป็น historical preview record ก่อน roadmap refactor; ไม่อัปเดต execution evidence ย้อนหลัง
Current full dashboard คือ [P9](P9-web-dashboard.md); current structure/Matrix/regime/signals คือ P5–P8 ตาม [ADR-007](../docs/decisions/ADR-007-task-roadmap.md)
Paths ของ generated/untracked AGENTS/CLAUDE และ node_modules ด้านบนเป็น historical local context ไม่ใช่ required checkout links; การทำงานต่อใช้ tracked context ใน metadata และตรวจ scoped instructions ที่มีจริง
