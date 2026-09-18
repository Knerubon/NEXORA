---
name: backtesting
description: สร้าง deterministic replay, comparison และ metrics สำหรับ P10/P11 หรือ paper replay validation
---

# backtesting

รับ immutable dataset manifest/hash, engine/config versions และ cost/execution assumptions
- ใช้ core เดียวกับ observation; simulation fills/metrics เป็นอีก boundary ไม่แอบแก้ signal engine
- ตรึง price source, event ordering, decision time และ fill time; ห้าม fill ด้วยข้อมูลที่ยังไม่พร้อม
- เปรียบเทียบ candlestick baseline/fixed P&F/adaptive P&F บน split และ assumptions เดียวกัน
- แยก parameter selection กับ evaluation; ระบุ spread, commission, slippage, currency และ position sizing
- กำหนด units/formulas/undefined cases ของ metrics รวม zero trades, zero losses, equity=0
ส่ง reproducible run manifest, hand-calculated metric examples และ limitations; ไม่รับประกัน performance
