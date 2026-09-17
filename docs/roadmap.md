# Roadmap

[Requirements](requirements.md) / [Architecture](architecture.md) เป็น source of truth; แผน P1–P13 เป็น execution decomposition ตาม [ADR-007](decisions/ADR-007-task-roadmap.md)
Phase 1 หมายถึง research/observation/backtest/local paper ทั้งชุด ไม่ใช่เฉพาะ P1; ไม่มี live/demo broker orders
Requirements ยังใช้ P1–P8 เดิม: ADR-007 ระบุ mapping/conflict ชัด ไม่เปลี่ยน requirement หรือ architecture โดยปริยาย

| Task | Direct dependencies | Traceability | Exit gate |
|---|---|---|---|
| [P1 Foundation](../tasks/P1-foundation.md) | none | NFR | local foundation |
| [P2 Market Data](../tasks/P2-market-data.md) | P1 | FR-01 | normalized replay |
| [P3 Fixed P&F Engine](../tasks/P3-pnf-engine.md) | P2 | FR-02 | golden deterministic transitions |
| [P4 Adaptive Box](../tasks/P4-adaptive-box.md) | P3 | FR-03 | fixed parity, causal sizing; review pending |
| [P5 Multi-Resolution Matrix](../tasks/P5-matrix.md) | P4 | FR-04 | isolated resolutions and rebuild |
| [P6 Market Structure](../tasks/P6-market-structure.md) | P5 | FR-05 | confirmed pivots and candidate S/R |
| [P7 Market Regime](../tasks/P7-market-regime.md) | P6 | FR-05 | causal trend/range/high-volatility metadata |
| [P8 Signal Engine](../tasks/P8-signal-engine.md) | P7 | FR-06 | explainable versioned research signals |
| [P9 Web Dashboard / Realtime](../tasks/P9-web-dashboard.md) | P8, DQ1 | FR-07/08 | REST/WS, five views, protected remote access |
| [P10 Backtest & Research Lab](../tasks/P10-backtest.md) | P9, DQ1 | FR-09/08 | immutable datasets, metrics, real Lab integration |
| [P11 Risk Engine](../tasks/P11-risk-engine.md) | P10 | Phase gates / safety | sizing/limits and replay-tested decisions |
| [P12 Paper Trading](../tasks/P12-paper-trading.md) | P11, P10 | Phase gates | risk-gated local fills and recovery |
| [P13 Production Hardening](../tasks/P13-production-hardening.md) | P12 | NFR | restore/recovery/security/monitoring drills |
| [DQ1 Market Data Quality / Observability](../tasks/DQ1-market-data-quality.md) | P2 | FR-01, FR-08 System, NFR | quality sidecar, gap/stale/reconnect/latency evidence |

DQ1 เป็น additive market-data task ที่ทำหลัง P2 และต้องผ่านก่อน P9; ไม่เพิ่ม acceptance ย้อนหลังให้ P2
P9 มี Backtest Lab shell/empty state; P10 ต้องเชื่อม stored runs จริงก่อนถือ FR-09 และ Lab integration ครบ
P10 กำหนด versioned research sizing assumptions; P11 เป็น owner ของ reusable risk approval/limits และ replay integration ก่อน P12; ไม่มี dependency cycle
P13 harden operation ไม่เลื่อน auth/encryption, risk หรือ paper safety จาก P9/P11/P12

## Status and decision gates
ใช้แต่ละ task Execution record + review/merge evidence ไม่ใช้ roadmap เป็น completion record
P4 implementation merged ใน PR #5 แต่ required review/DoD ยังไม่ครบหลักฐาน: ดู ADR-007; P1–P4 history คงเดิมและ P5 ยัง blocked
P5–P13 และ DQ1 ยัง not_started/blocked; dependency completion และ ADR-007 review เป็น entry gates
P5 ล็อก configs/snapshot; P6 pivot/S&R; P7 regime rules; P8 signal rules; P9 transport/auth; P10 dataset/costs/metrics/splits; P11 risk policy; P12 simulated fill/accounting; P13 measurable recovery/operational targets
Numeric thresholds/formulas ยังต้อง reviewed decisions และ golden fixtures; OX observations ไม่ใช่ specification

## Historical references
Original P5 scope แยก P5–P8; original P6/P7/P8 ย้าย P9/P10/P12 และแยก Risk เป็น P11
[Original task snapshot](https://github.com/Knerubon/NEXORA/tree/346c9524fa2aeca1bb95b14a579a69424f6c73c0/tasks) เก็บรายละเอียดก่อน refactor
[UX1 local quote preview](../tasks/UX1-mt5-price-preview.md) เป็น historical bounded preview; ไม่ใช่ completion evidence ของ full P9
Future live execution อยู่นอก task graph นี้และต้อง separate scope/approval/risk controls
