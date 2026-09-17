---
task: P2
status: blocked
depends_on: ["tasks/P1-foundation.md"]
agents: ["agents/rin/AGENT.md","agents/architect/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md","agents/security/AGENT.md"]
skills: ["skills/market-data/SKILL.md","skills/postgres/SKILL.md","skills/testing/SKILL.md","skills/security/SKILL.md"]
docs: ["docs/requirements.md","docs/architecture.md"]
translation_needed: false
---

# P2 — Market Data

## เป้าหมาย / traceability
อ่าน XAUUSD ผ่าน read-only adapter และ persist normalized events ที่ replay ได้
Requirement: FR-01; persistence/reproducibility NFR

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
ตรวจ Execution record และ merge/check evidence ของ P1 ก่อนเปลี่ยนเป็น ready; dependency file อ่านเฉพาะ status/evidence ไม่โหลด context ของ phase นั้นต่อทั้งหมด
Blocker/decision: ต้องมี P1 done evidence; ขาด MT5 runtime ไม่ขวาง fake/replay implementation แต่ต้องบันทึก integration limitation
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `packages/market_data`, `tests`, `infra`, `scripts`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: adapter + normalized contract, migrations, synthetic fixtures และ replay interface
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. กำหนด MarketTick/Bar -> NormalizedPriceEvent contract: source, symbol, event_time, received_at, identity/order, price_source, units/precision และ schema version
2. ล็อก tick/OHLC mapping, timezone, duplicate/out-of-order/gap policy ใน decision ก่อน implement; อย่าเติม price-source default เงียบ ๆ
3. implement adapter interface + fake และ MT5 read-only implementation โดย local runtime config; ไม่มี order methods
4. ทำ PostgreSQL schema/migration สำหรับ provenance/raw/normalized events และ deterministic query order
5. เพิ่ม reconnect/backfill, validation และ replay reader; failure ต้องไม่ทำ raw data หายหรือ rewrite

## Acceptance / validation
- [ ] fixtures ครอบคลุม bid/ask/close mapping, timezone, precision, missing/invalid prices, duplicate และ out-of-order
- [ ] disconnect/reconnect/backfill ให้ event identities/order ที่คาดไว้; duplicate ไม่เพิ่ม derived processing
- [ ] persist -> reload/replay ให้ normalized semantic events เท่ากัน; rollback/restart ไม่สร้าง record ซ้ำ
- [ ] fake adapter tests รัน offline ได้; MT5 smoke ต้อง read-only และไม่มี secrets ใน output; unavailable integration ระบุ gap
- [ ] ไม่มี broker order calls และ PostgreSQL private; P3 รับ event contract ที่มีตัวอย่าง synthetic แล้ว
- [ ] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใช้ commands ที่ P1 จัดทำและตรวจว่าใช้งานได้กับ checkout ปัจจุบัน; บันทึก exact command/result ด้านล่าง ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Architect contract -> Developer -> Tester -> Reviewer + Security -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: not_started
- Context additions: none
- Decisions: pending
- Changed files / commit / PR: none
- Checks: not_run
- Review: pending
- Blockers: dependency P1 not completed; ดู decision gate ด้านบน
- Next action: ตรวจ dependency completion evidence แล้วทำ decision/contracts ของ task
