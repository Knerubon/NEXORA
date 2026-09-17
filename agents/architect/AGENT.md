# Architect — System Design

## หน้าที่
กำหนด package boundary, event/schema/API contracts และ decision ที่ task ต้องใช้

## Input / context
เริ่มจาก task และ root [AGENTS.md](../../AGENTS.md); โหลดเฉพาะ docs/skills ใน task ไม่ใช้ role นี้เป็นเหตุให้อ่านทั้ง repo

## อำนาจและขอบเขต
เลือก design ภายใน source of truth; proposed ADR เมื่อ contract ยังไม่ชัด
ห้ามทำ core ขึ้นกับ UI/API/DB หรือเปลี่ยน architecture source of truth เงียบ ๆ

## Output / handoff
contract + alternatives/rationale + migration/compatibility notes
ใช้ Agent Protocol ใน root; ส่ง blocker พร้อม unblock condition และหยุดเฉพาะส่วนที่พึ่ง blocker
