# Glossary

| Term | ความหมายใน NEXORA |
|---|---|
| Phase 1 | research/observation/backtest/paper scope P1–P13 + DQ1 ตาม [ADR-007](decisions/ADR-007-task-roadmap.md); ห้าม live auto-trading |
| P1 | Foundation task เท่านั้น |
| P&F | Point & Figure; price structure เป็น X/O columns |
| X / O | ขาขึ้น / ขาลงของ column ไม่ใช่คำสั่ง buy/sell |
| box_size | price increment; ต้องระบุ unit/precision ไม่เท่ากับ broker points โดยอัตโนมัติ |
| reversal_boxes | จำนวน boxes สำหรับ reversal ตาม versioned rule |
| price_source | bid/ask/mid/close หรือ source ที่ contract ระบุ |
| ATR | Average True Range; candidate สำหรับ adaptive sizing ยังไม่ใช่สูตรที่ยืนยัน |
| Fast / Medium / Slow | independently configured structure resolutions; ไม่ใช่ OX BOX 10/20/30 |
| S/R | candidate Support / Resistance จาก confirmed structure |
| regime | trend/range/high-volatility metadata ตาม rule/version |
| ResearchSignal | output พร้อม reasons/evidence; ไม่ใช่ broker order |
| live observation | อ่าน market data ปัจจุบันโดยไม่ส่ง order |
| backtest | replay historical data ผ่าน core engines เดียวกัน |
| paper trading | local simulation of orders/fills; ไม่มี broker execution |
| deterministic | input/order/config/version เดิมให้ semantic output เดิม |
| event_time / received_at | เวลา source event / เวลาระบบรับ; normalize timezone ชัดเจน |
| look-ahead | ใช้ข้อมูลที่ ณ เวลาตัดสินใจยังไม่เกิด |
| data_ref / config_version | reference เพื่อ rebuild และ audit |
| hypothesis / validated | ข้อเสนอรอทดสอบ / มีหลักฐานในขอบเขตการทดลอง; ไม่รับประกันกำไร |
| ADR | Architecture Decision Record; proposed/accepted/superseded พร้อม rationale |
| DoD | Definition of Done ใน root AGENTS.md + acceptance ของ task |
