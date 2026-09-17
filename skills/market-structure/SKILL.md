---
name: market-structure
description: สร้าง Matrix, confirmed pivots, S/R, regimes และ explainable research signals ใน P5
---

# market-structure

รับ P&F transitions ของ independently configured resolutions
- อย่า hard-code Fast/Medium/Slow เป็น OX 10/20/30; label ไม่ได้กำหนด units
- ระบุ pivot occurrence_time กับ confirmation_time; signal ใช้ได้หลัง confirm เท่านั้น
- กำหนด unavailable/stale/disagree states; อย่าแสดง alignment/strength ที่คำนวณไม่ได้
- ทุก signal เก็บ reason_codes, human-readable reasons, data refs และ rule/config version
- ตรวจ interleaved resolutions/symbols, conflicting directions, no future confirmation และ reproducible rebuild
ส่ง contract + golden sequences; output เป็น research signal ไม่ใช่ order
