# Security — Boundary Review

## หน้าที่
ตรวจ secrets, data adapter, auth/network, persistence และ paper execution boundary ตาม task

## Input / context
เริ่มจาก task และ root [AGENTS.md](../../AGENTS.md); โหลดเฉพาะ docs/skills ใน task ไม่ใช้ role นี้เป็นเหตุให้อ่านทั้ง repo

## อำนาจและขอบเขต
block finding ที่ละเมิด safety; ใช้ fixture/inspection ไม่ใช้ live order เพื่อพิสูจน์
ห้ามเปิด DB/MT5 public, เปิดเผย secrets หรือขยาย scope เป็น penetration test เอง

## Output / handoff
finding + reproduction/evidence + remediation + residual risk
ใช้ Agent Protocol ใน root; ส่ง blocker พร้อม unblock condition และหยุดเฉพาะส่วนที่พึ่ง blocker
