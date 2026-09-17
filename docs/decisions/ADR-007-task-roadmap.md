# ADR-007 — Task roadmap decomposition and completion evidence

Status: proposed for review in this docs-only PR; execution-plan clarification, not formula approval
Sources: [Requirements](../requirements.md), [Architecture](../architecture.md), [AGENTS](../../AGENTS.md)

## Context / repository evidence
ตรวจ origin/main ที่ `346c9524fa2aeca1bb95b14a579a69424f6c73c0` ก่อนแก้; latest merged [PR #5](https://github.com/Knerubon/NEXORA/pull/5) merge เวลา 2026-09-17T15:34:39Z
[P4 implementation commit](https://github.com/Knerubon/NEXORA/commit/346c9524fa2aeca1bb95b14a579a69424f6c73c0) มี adaptive_box package, P&F integration, tests/test_adaptive_box.py และ ADR-006
PR บันทึก pytest (29 tests), targeted Ruff/mypy PASS; เป็น historical reported evidence ไม่ใช่ผล rerun ใน PR เอกสารนี้
GitHub metadata ที่ตรวจ: state MERGED, reviews [], statusCheckRollup []
[P4 Execution record](../../tasks/P4-adaptive-box.md) ยัง in_review, independent review pending และ DoD unchecked; [ADR-006](ADR-006-adaptive-box-sizing.md) ยัง independent review pending
สรุป: implementation merged ยืนยันได้; full completion ตาม AGENTS ยังยืนยันไม่ได้ ไม่เติม approval/checks ที่ไม่มี
ผู้ใช้ขอให้สแกน/refactor tasks ต่อหลังแจ้ง merge; ดำเนิน planning ได้ แต่ P5 คง blocked จน required P4 review/DoD evidence ครบ
P1–P4 task files และ ADR-001–006 ไม่แก้; history/execution evidence เดิมเก็บ byte-for-byte

## Numbering conflict and proposed resolution
Requirements v0.1 ระบุ P1–P8 เดิม ส่วนแผนนี้ P1–P13; product Phase 1 ไม่ใช่ task P1
ไม่แก้ requirements/architecture ใน PR นี้; ใช้ mapping ต่อไปนี้เพื่อ trace scope เดิมอย่างเปิดเผย
Reviewer ต้องยืนยัน clarification นี้ก่อนเริ่ม implementation ตามแผนใหม่; หากไม่ยอมรับ ให้หยุดเฉพาะส่วนที่พึ่ง decision และแก้ ADR ก่อน ไม่ override source of truth

| Original gate | Refined task ownership |
|---|---|
| P1–P4 | unchanged history and behavior |
| P5 Matrix/Structure (รวม regime/signals เดิม) | P5 Matrix, P6 Market Structure, P7 Market Regime, P8 Signal Engine |
| P6 Live Web | P9 Web Dashboard / Realtime |
| P7 Backtest Validation | P10 Backtest & Research Lab (รวม Dataset Versioning) |
| P8 Paper Trading | P11 Risk Engine -> P12 Paper Trading |
| Existing deployment/security NFR | P13 Production Hardening; ไม่เลื่อน security gates ของ P9/P12 |
| Existing FR-01 and System health | DQ1 additive market-data follow-up; ไม่ reopen P2 |

Original task versions remain available at [baseline tasks](https://github.com/Knerubon/NEXORA/tree/346c9524fa2aeca1bb95b14a579a69424f6c73c0/tasks).
Historical phase numbers in ADR-002/003 and UX1 are original identifiers; use this mapping, not those historical notes, to assign current work.

## Architecture boundary / risk placement
Architecture v0.1 defines the observation flow through ResearchSignal and has no execution branch. Keep that flow unchanged.
Proposed local simulation branch: ResearchSignal + simulated account/quality snapshot -> P11 RiskDecision -> P12 local Paper Execution -> ledger.
Risk เป็น separate pure domain responsibility: sizing, exposure, daily loss, drawdown และ allow/reject ไม่อยู่ใน Signal Engine
Proposed `packages/nexora/risk` และ simulator placement ต้องมี reviewed contract/ADR ก่อน implementation; ไม่อ้างว่า packages หรือ policies เหล่านี้มีแล้ว
P10 ส่งมอบ reproducible research baseline พร้อม explicit sizing/cost assumptions; P11 integrate shared risk policy กับ P10 replay ก่อน P12 โดยไม่ทำให้ P10 depends_on P11
Research baseline เดิมไม่ถูก rewrite เมื่อเพิ่ม risk policy; version new runs/configs และเก็บ comparison assumptions
Task graph เป็น delivery gates ไม่ใช่ package imports; P10 depends_on P9 เพื่อส่ง Lab integration ไม่ได้ทำ core ขึ้นกับ UI

## Data quality and immutable research datasets
DQ1 เพิ่ม sidecar quality/health/provenance รอบ P2 contracts; P9 ใช้ System view, P10 ใช้ frozen quality/manifest, P11 consume quality ก่อน simulated approval
ไม่แก้ normalization/ordering/dedup semantics หรือ completed P1–P4 outputs; incompatible behavior ต้อง ADR/version/migration แยก
P10 เป็น owner ของ immutable dataset manifest/hash/version/correction lineage และ clean reproducibility acceptance

## Safety and consequences
ทั้ง P1–P13 และ DQ1 อยู่ใน Phase 1 research/observation/backtest/local paper เท่านั้น
ห้าม live/demo broker order calls, secrets ใน Git/log fixtures, public PostgreSQL/MT5 และ unverified OX BOX semantics
P13 ไม่อนุญาต live execution; future live เป็น separate scope/approval/risk review นอก graph นี้
ทุก new task เป็น blocked/not_started; done ต้องมี evidence ตาม AGENTS ไม่ใช่จาก numbering หรือ checkbox
การเปลี่ยนครั้งนี้ docs-only: implementation, P1–P4, requirements และ architecture ไม่เปลี่ยน
