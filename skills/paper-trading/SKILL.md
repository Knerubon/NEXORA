---
name: paper-trading
description: สร้าง local paper execution/risk simulator และ recovery validation ใน P12 โดยใช้ Risk Engine จาก P11
---

# paper-trading

รับ research signals + P11 RiskDecision + simulated account/config + observed/replayed market events; คืน simulated orders/fills/ledger
- ไม่มี broker execution adapter; order/fill IDs และ UI ต้องแยก paper ชัดเจน
- ตัดสิน fill timing, bid/ask/spread/slippage, partial/rejected fills และ fees ก่อน implement
- P11 เป็น owner ของ risk limits/sizing; simulator enforce RiskDecision/kill switch และไม่ bypass หรือ implement policy ซ้ำ; ไม่แปลว่าอนุญาต real execution
- ใช้ stable event/idempotency keys; restart ไม่สร้าง fill ซ้ำ; reconcile ledger กับ positions/cash
- ทดสอบ stale data, disconnect, rejection, duplicate, restart, breached limit และ kill switch
ส่ง local-only boundary evidence, ledger replay และ limitations; ไม่ promote paper pass เป็น live approval
