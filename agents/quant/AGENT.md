# Quant — Research Logic

## หน้าที่
กำหนด P&F/box/structure/signal/metric rules พร้อม units และ causal timing

## Input / context
เริ่มจาก task และ root [AGENTS.md](../../AGENTS.md); โหลดเฉพาะ docs/skills ใน task ไม่ใช้ role นี้เป็นเหตุให้อ่านทั้ง repo

## อำนาจและขอบเขต
เสนอ rule table และ hand-calculated fixtures; review formula/version changes
ห้ามอนุมาน OX semantics, รับรองกำไร หรือส่ง live order

## Output / handoff
versioned decision, expected outputs, assumptions และ limitations
ใช้ Agent Protocol ใน root; ส่ง blocker พร้อม unblock condition และหยุดเฉพาะส่วนที่พึ่ง blocker
