---
name: paper-trading
description: สร้าง local paper execution/risk simulator และ recovery validation ใน P8
---

# paper-trading

รับ research signals + simulated account/config + observed/replayed market events; คืน simulated orders/fills/ledger
- ไม่มี broker execution adapter; order/fill IDs และ UI ต้องแยก paper ชัดเจน
- ตัดสิน fill timing, bid/ask/spread/slippage, partial/rejected fills และ fees ก่อน implement
- risk limits/kill switch คุม simulator; ไม่แปลว่าอนุญาต real execution
- ใช้ stable event/idempotency keys; restart ไม่สร้าง fill ซ้ำ; reconcile ledger กับ positions/cash
- ทดสอบ stale data, disconnect, rejection, duplicate, restart, breached limit และ kill switch
ส่ง local-only boundary evidence, ledger replay และ limitations; ไม่ promote paper pass เป็น live approval
