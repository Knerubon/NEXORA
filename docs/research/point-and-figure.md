# P&F research contract

Status: open decisions; เป็น checklist สำหรับ P3 ไม่ใช่ finalized calculation specification
Sources: [FR-02](../requirements.md), [ADR-001](../decisions/ADR-001-pnf.md)
ยังไม่มี external research ที่ verify ในเอกสารนี้; หากเพิ่ม source ให้บันทึก URL/date/claim และแยก claim จากผลทดลอง

## ต้องตัดสินก่อน logic

- Input: symbol, event identity/time/order, price_source, price precision และ box/reversal config
- Seed/grid anchor: ราคาแรกวาง grid อย่างไร; เมื่อใดเริ่ม X/O; วิธี represent empty/seed state
- Threshold: exact boundary, quantization/rounding, extension vs reversal precedence
- Reversal: reference จาก extreme ใด; cell แรกของ new column; multi-box gap
- Duplicate/out-of-order: ใช้ policy จาก P2 ไม่ sort/rewrite historical state แบบเงียบ ๆ
- OHLC: ภายใน bar ไม่รู้ high/low order; เลือก documented deterministic path หรือ reject ambiguous input
- Output: column/cell identity, source event, effective config, transition reason และ timestamp

## Experiment

สร้าง synthetic price sequence + hand-calculated expected transitions แยกจาก implementation
ครอบคลุม flat/rise/fall/exact threshold/reversal/gap/precision/duplicate และ interleaved symbols
เปรียบเทียบ streaming กับ replay รวม restart boundary; บันทึก rejected inputs
Quant เสนอ rule table และ fixtures ใน ADR; Reviewer ตรวจ decision ก่อน P3 ใช้เป็น behavior
