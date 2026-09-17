---
name: fastapi
description: พัฒนา REST contracts ของ NEXORA เมื่อ task เกี่ยวข้องกับ state/history/configuration หรือ Backtest Lab API
---

# fastapi

แยก transport/schema validation จาก domain engine
- ระบุ schema/version, pagination, error codes, symbol/run/config identity และ configuration effective time
- reject invalid config ก่อนเปลี่ยน state; ไม่มี order execution endpoint
- ใช้ dependency injection/fakes เพื่อทดสอบโดยไม่ต้อง MT5
- ตรวจ auth boundary, validation errors, empty/history results และ core import independence
ส่ง endpoint contract และ integration evidence; ใช้ toolchain/version ของ repo ไม่เดา command
