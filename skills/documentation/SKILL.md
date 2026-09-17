---
name: documentation
description: จัดทำ concise Thai docs, ADR, task handoff หรือคำแปลที่ร้องขอสำหรับ NEXORA
---

# documentation

รักษา requirements/architecture เป็น source of truth; อย่า duplicate specification
- Thai explanations + English technical terms/code/API/variables/commit/error
- แยก accepted decision, proposed hypothesis และ observed fact; เก็บ provenance เมื่อมี claim
- อัปเดต task context paths ก่อนใช้ docs/skills เพิ่ม; ตรวจ links และ stale references
- ย่อโดยรักษา constraints, units, acceptance และ caveats; ไม่แปลซ้ำทั้ง context
- แปลเต็มเมื่อ translation_needed=true หรือ user ขอ external document
ส่ง changed meaning/paths และ validation; ไม่เปลี่ยน trading formulas ในนาม language cleanup
