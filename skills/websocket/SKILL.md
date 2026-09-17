---
name: websocket
description: ออกแบบ realtime NEXORA event stream และ reconnect/resync เมื่อ task เปลี่ยน WebSocket
---

# websocket

กำหนด envelope: event type/schema version, identity/sequence, event time, symbol, run/config reference ตาม contract
- initial snapshot + updates ต้องมี sequence boundary; ระบุ duplicate/gap/resync behavior
- reconnect ไม่ทำให้ client ใช้ state ค้าง; bounded buffers/backpressure ไม่ silently drop structural events
- auth ใช้ boundary เดียวกับ REST; ไม่ส่ง credentials ผ่าน URL/logs
- ตรวจ snapshot-update race, duplicate, out-of-order, disconnect และ slow client
ส่ง protocol decision + integration tests; core ไม่ขึ้นกับ transport
