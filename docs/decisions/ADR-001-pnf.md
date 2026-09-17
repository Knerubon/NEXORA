# ADR-001 — Independent P&F research core

Status: accepted (บันทึกข้อกำหนดเดิม ไม่ใช่ approval ใหม่)
Source: [requirements FR-02/03/04/09](../requirements.md), [architecture Design decisions](../architecture.md)

## Decision

ใช้ P&F เป็น price-structure research core ที่ deterministic และไม่ขึ้นกับ UI/API/DB
replay/backtest/live observation ใช้ engine เดียวกัน; fixed box มาก่อน adaptive
Fast/Medium/Slow เป็น NEXORA configurations; ไม่อนุมาน semantics ของ OX BOX 10/20/30

## Consequences

ทุก transition ต้องมี reason, price, timestamp และ config/version/data reference
P3 ต้องกำหนด seed/grid/reversal/rounding/gap/price-source policy พร้อม fixtures ก่อน implement
ADR นี้ไม่เลือก formula, numeric defaults, ATR period หรือ strategy entry/exit rules

## Decision updates

สำหรับ decision ใหม่ ให้เพิ่ม ADR แยก: Status, task, source requirements, context, options, decision, rationale, consequences, validation evidence และ supersedes
ใช้ proposed จน review ตาม task สำเร็จ; หากขัด requirements/architecture ให้ขอ explicit scope decision ก่อน ไม่แก้สองไฟล์นั้นเงียบ ๆ
