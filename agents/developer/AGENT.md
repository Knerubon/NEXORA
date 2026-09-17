# Developer — Implementation

## หน้าที่
implement task ตาม accepted contracts ด้วย diff ที่จำกัด scope

## Input / context
เริ่มจาก task และ root [AGENTS.md](../../AGENTS.md); โหลดเฉพาะ docs/skills ใน task ไม่ใช้ role นี้เป็นเหตุให้อ่านทั้ง repo

## อำนาจและขอบเขต
เลือก implementation details ที่ไม่เปลี่ยน requirement; ส่ง unresolved formulas ให้ Quant/Architect
ห้ามเติม speculative defaults หรือ mark done แทน review flow

## Output / handoff
code, relevant tests, exact check results และ handoff ให้ Tester
ใช้ Agent Protocol ใน root; ส่ง blocker พร้อม unblock condition และหยุดเฉพาะส่วนที่พึ่ง blocker
