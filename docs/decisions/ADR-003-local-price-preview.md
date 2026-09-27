# ADR-003 — Local quote preview

Status: implementation decision for user-requested UX1; independent review pending

## Boundary
FR-01/07/08 preview ใช้ FastAPI + Next.js ตาม architecture; core ไม่มี MT5/API dependency
P2 persistence/replay และ P3–P5 formulas ยังไม่ implement; preview snapshot ไม่ใช่ NormalizedPriceEvent
Broker source มาจาก terminal ที่เปิดและล็อกอินไว้ ผู้ใช้ยืนยันให้ใช้ terminal นั้น; ไม่อ่าน account details หรือส่ง credentials
Runtime ต้องกำหนด NEXORA_MT5_PATH และ NEXORA_MT5_SYMBOL; ไม่ค้นแล้วเลือก terminal/symbol ใหม่อัตโนมัติ
ตรวจ process ก่อน initialize เพื่อไม่เปิด terminal ที่ปิดอยู่; ไม่เปลี่ยน Market Watch, account หรือ terminal settings

## Data / transport
Read-only calls: initialize, terminal_info (อ่านเฉพาะ connected, company, path — ดู Amendment 1), symbol_info, symbol_info_tick, shutdown
MT5 calls อยู่ใน adapter thread แยกจาก event loop; หนึ่ง poll ต่อวินาทีและแชร์ snapshot ให้ clients
Quote: symbol, bid/ask/spread เป็น decimal strings ตาม symbol digits, event_time จาก time_msc ใน UTC, received_at UTC
เก็บทั้ง Bid และ Ask; UI เลือกเส้นที่แสดงได้ ไม่มี default price source สำหรับ engine
Envelope: schema_version=1, stream_id (process session), sequence (snapshot order), status, code, quote หรือ null
GET /quotes เป็น current snapshot; WS /ws/quotes ส่ง snapshot ล่าสุดก่อน ตามด้วย snapshots เมื่อ sequence เปลี่ยน; reconnect ใช้ snapshot ใหม่และเริ่ม chart session ใหม่
Buffer ฝั่ง browser จำกัด 120 observed snapshots; ไม่ใช่ tick history/backfill และไม่ใช้กับ research engine
polling อาจข้าม ticks ได้อย่างชัดเจน; ไม่มี signal/transition stream จึงไม่อ้าง lossless event delivery

## Freshness / failures
Stale หลัง tick age > 10s (UI freshness threshold ไม่ใช่ trading formula); future tick > 5s แสดง clock_skew
Missing/invalid/NaN/crossed/zero prices และ invalid timestamps ไม่ถูกแสดงเป็น live; ส่ง sanitized code ไม่มี exception/account/path data
Disconnected/unavailable/error snapshot ไม่มี quote; browser ปิด websocket หรือขาด snapshot > 5s แสดง reconnecting/stale และไม่แสดง live badge
ปิด terminal แล้ว service คงทำงานและ retry เมื่อ terminal เดิมกลับมา; shutdown ปิด Python IPC เท่านั้น

## Local access
API/web bind 127.0.0.1; REST/WS ปฏิเสธ non-loopback clients; WS รับ Origin เฉพาะ local web ports 3000/3100
Host allowlist คงอยู่; ไม่มี permissive CORS หรือ remote tunnel; local origin checks ไม่ใช่ remote authentication
MT5 dependencies เป็น optional extra; tests ใช้ fake และไม่ require terminal

## Amendment 1 — terminal identity fields (ADR-025)
Status: accepted พร้อม [ADR-025](ADR-025-mt5-instrument-resolution-v1.md) rev 3 (Rin, 2026-09-25); มีผลกับ runtime เมื่อ Phase 2B wiring merge — ก่อนหน้านั้น runtime ยังอ่าน "connected เท่านั้น" ตามเดิม
terminal_info อ่านได้เฉพาะ `connected`, `company`, `path` — `company`/`path` เป็น terminal identity metadata (ใช้ตรวจ terminal ที่ bind ไว้) ไม่ใช่ account identity
field อื่นของ terminal_info (เช่น `community_*`, `mqid`, `data_path`, `commondata_path`) ยังห้ามอ่าน เว้นแต่มี accepted ADR อื่นอนุญาตโดยตรง
account_info() ยังห้ามเรียก; ไม่อ่าน login, server, balance, equity, positions, orders, history หรือ credentials; ส่วนอื่นของ ADR นี้ไม่เปลี่ยน

## Sources
- [MT5 initialize](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py)
- [Latest tick](https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfotick_py)
- [Symbol metadata](https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py)
