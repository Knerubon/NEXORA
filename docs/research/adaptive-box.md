# Adaptive Box research

Status: hypothesis; FR-03 ยืนยันเพียง fixed-first และ volatility-aware candidate
Source: [requirements](../requirements.md)

ATR-derived sizing เป็น candidate; ยังไม่ยืนยัน period, multiplier, clamps, smoothing หรือ numeric defaults
ต้องระบุ unit, tick-size rounding, warm-up, missing bars และ volatility input window
ใช้เฉพาะข้อมูลที่พร้อม ณ event นั้น; ห้ามใช้ future high/low/close ใน event ก่อน bar ปิด

## Experiment / decision gate P4

1. ใช้ fixed baseline และ frozen dataset/config/version
2. เสนอ formula options + effective timing: new column / next event / boundary อื่น พร้อมผลต่อ state
3. ไม่ rewrite historical columns เมื่อ box เปลี่ยน; หาก rebuild ต้องเป็น run/version ใหม่ชัดเจน
4. วัด noise/transition stability และ cost/latency tradeoff ไม่สรุป profitability จาก chart
5. Prefix replay ต้องให้ output prefix เดิมเมื่อ append future data; snapshot/restart ต้องเท่ากับ continuous run
6. บันทึก ADR, golden fixtures และข้อจำกัดก่อนเปิด adaptive mode

[P10](../../tasks/P10-backtest.md) เปรียบเทียบ fixed/adaptive ด้วย protocol เดียวกัน; research result ไม่เป็นอนุญาต live execution

## P4 implementation experiment note

- Candidate implemented in [ADR-006](../decisions/ADR-006-adaptive-box-sizing.md):
  - fixed baseline parity mode
  - ATR-derived proxy from past-only absolute price deltas
  - warm-up hold-last policy
  - clamp + quantization policy with explicit rule version
- Evidence scope:
  - fixed parity against P3 golden fixtures
  - causal prefix invariance under append-future events
  - snapshot/restart parity and per-transition effective box tracing
- Limitations:
  - true range currently uses tick delta proxy (not bar high/low ordering)
  - no profitability validation; research-only behavioral evidence
