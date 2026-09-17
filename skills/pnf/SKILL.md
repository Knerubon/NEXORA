---
name: pnf
description: กำหนดและ implement Point & Figure engine เมื่อ task เปลี่ยน X/O transitions หรือ replay behavior
---

# pnf

รับ ordered normalized events และ versioned config; คืน transitions/state ที่อ้าง data_ref ได้
- ใช้ rule table ที่ Quant ตัดสิน: seed/grid, precision, threshold equality, reversal, multi-box gaps, price source และ OHLC policy
- ถ้ายังไม่มี rule ให้ทำ decision + hand-calculated fixtures ก่อน; อย่าเลือก 3-box reversal หรือค่าใดเป็น requirement เอง
- Core ต้อง pure/deterministic; ไม่อ่าน clock/network/DB โดยตรง
- ตรวจ rise/fall/flat/exact threshold/reversal/gap/duplicate/restart; streaming กับ replay ต้องเท่ากัน
ส่ง state contract, decision/version และ golden test evidence
