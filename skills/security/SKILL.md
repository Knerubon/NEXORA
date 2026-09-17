---
name: security
description: ตรวจ secrets, authenticated remote access และ no-execution boundaries ตาม task NEXORA
---

# security

ตรวจเฉพาะ changed scope + relevant boundary
- secrets ห้ามเข้า Git/fixtures/logs/docs; config examples ใช้ชื่อ variable ไม่ใช้ credential value
- remote REST/WS ต้อง authenticated + encrypted; DB/MT5 private; default local binding
- live execution ไม่มีใน Phase 1; paper path ต้องใช้ local simulator และไม่มี broker order call
- negative tests ตรวจ unauthorized client, malformed input และ prohibited execution path ตาม scope
ส่ง findings/evidence/remediation; ไม่ expose service หรือใช้ live credentials เพียงเพื่อ review
