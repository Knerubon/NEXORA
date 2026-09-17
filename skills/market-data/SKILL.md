---
name: market-data
description: พัฒนา read-only MT5/broker adapter, normalization และ replay ingestion ใน P2
---

# market-data

รับ ticks/OHLC พร้อม source metadata; ห้ามเรียก order API แม้เพื่อทดสอบ
- ล็อก UTC/precision/price source, symbol mapping, identity, source sequence และ received_at semantics
- เก็บ provenance/replay ordering; explicit duplicate/out-of-order/gap policy พร้อม audit
- ใช้ fake adapter + synthetic fixtures เป็น default; integration จริงใช้ locally supplied runtime secrets เท่านั้น
- reconnect/backfill ไม่สร้าง duplicate downstream transitions; invalid data quarantine/reject ตาม contract
ส่ง event contract, persistence/replay evidence และ adapter disconnect behavior
