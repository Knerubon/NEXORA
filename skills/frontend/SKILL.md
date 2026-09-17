---
name: frontend
description: สร้าง responsive React/Next.js dashboard ของ NEXORA และแสดง research/backtest state
---

# frontend

ใช้ server contracts; UI ไม่คำนวณสูตร trading ซ้ำเอง
- แสดง symbol/config/version, data freshness และ observation/paper labels อย่างชัดเจน
- มี loading/empty/error/stale/disconnected states; reconnect ขอ snapshot ก่อนใช้ updates ต่อ
- P&F/Matrix/S&R/signals ต้องอธิบาย source/evidence ได้; ไม่สร้าง performance/strength ตัวเลขปลอม
- ตรวจ desktop/mobile, keyboard access, readable labels และ bounded rendering ของ history
ส่ง user-flow evidence, screenshots เมื่อมี UI จริง และ build/check results; ห้ามเพิ่ม live trade controls
