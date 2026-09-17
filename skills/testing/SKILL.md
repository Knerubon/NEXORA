---
name: testing
description: ตรวจ NEXORA acceptance ด้วย unit/integration/replay/boundary tests เมื่อ task ต้อง validate behavior
---

# testing

เริ่มจาก task criteria และ accepted rule ไม่เริ่มจาก implementation outputs
- ใช้ synthetic fixtures และ expected results ที่ derive แยกจาก code
- core: threshold/precision/gap/ordering/restart และ semantic replay equality
- transport/persistence: failure/reconnect/idempotency/auth boundary ตาม scope
- ระบุ command, environment, input/config version, expected/actual และ result
- failing case ต้อง minimal reproduction; unavailable integration ไม่เท่ากับ pass
ไม่เพิ่ม tests ที่เพียง assert ถ้อยคำใน docs; docs-only ตรวจ paths/metadata/links และ diff
