---
name: adaptive-box
description: ออกแบบหรือทดสอบ fixed/adaptive box sizing และ causal update policy ใน P4/P7
---

# adaptive-box

รับ past-only volatility inputs, precision และ sizing config; คืน size + rule_version + effective event
- ทำ fixed-mode parity ก่อน adaptive; ATR เป็น candidate ไม่ใช่สูตรที่ล็อกแล้ว
- ตัดสิน warm-up, rounding, clamp และ effective boundary ก่อน implement; ไม่เปลี่ยน boxes ย้อนหลังใน run เดิม
- ทดสอบ insufficient data, zero volatility, gap, precision, resize boundary และ append-future prefix invariance
ส่ง formula decision, config snapshot และ fixed/adaptive comparison; ไม่สรุปกำไรจาก visual fit
