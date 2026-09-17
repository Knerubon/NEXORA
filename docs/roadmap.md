# Roadmap

อ้างอิง [Requirements](requirements.md) / [Architecture](architecture.md); เป็น execution plan ไม่ใช่ specification ใหม่
Phase 1 ใน requirements ครอบคลุม P1–P8; P1 เป็นเพียง Foundation ไม่ใช่ชื่อย่อของ Phase 1 ทั้งหมด

| Task | Dependency | Requirement | Exit gate |
|---|---|---|---|
| [P1 Foundation](../tasks/P1-foundation.md) | none | NFR, architecture | skeleton, local smoke, toolchain commands |
| [P2 Market Data](../tasks/P2-market-data.md) | P1 | FR-01 | normalized/persisted events, deterministic replay |
| [P3 P&F Engine](../tasks/P3-pnf-engine.md) | P2 | FR-02 | versioned rules, golden transitions |
| [P4 Adaptive Box](../tasks/P4-adaptive-box.md) | P3 | FR-03 | fixed parity, causal sizing |
| [P5 Matrix/Structure](../tasks/P5-matrix.md) | P4 | FR-04/05/06 | independent resolutions, explainable signals |
| [P6 Web Dashboard](../tasks/P6-web-dashboard.md) | P5 | FR-07/08 | REST/WS, responsive views, protected remote boundary |
| [P7 Backtest](../tasks/P7-backtest.md) | P6 | FR-09, FR-08 Backtest Lab | reproducible comparison and Lab integration |
| [P8 Paper Trading](../tasks/P8-paper-trading.md) | P7 | requirements Phase gates | local simulated fills, risk/recovery evidence |

P6 สร้าง Backtest Lab shell พร้อม empty state; P7 เติมผลจริง ไม่ถือ shell เป็น FR-09 completion
ทุก phase ใช้ task Execution record เป็นสถานะจริง; roadmap ไม่อ้างว่า implementation เสร็จ

## Decision gates

- P1: package/toolchain/layout contracts
- P2: timestamp, price source, duplicate/order policy, persistence schema
- P3: seed/grid/reversal/gap/OHLC policy
- P4: sizing formula, warm-up, effective boundary และ versioning
- P5: structure confirmation, regime/signal definitions, Fast/Medium/Slow parameters
- P7: baseline, costs, metric definitions และ validation split
- P8: simulated fill/risk policy; paper-only ไม่เปิดสิทธิ์ live execution

รายละเอียดที่ยังไม่ยืนยันบันทึกใน research แล้วตัดสินใน ADR โดยไม่ override source of truth
Future live trading อยู่นอก task ชุดนี้ ต้อง scope/approval/risk controls แยก
