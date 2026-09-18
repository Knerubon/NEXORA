---
task: P10
status: blocked
depends_on: ["tasks/P9-web-dashboard.md", "tasks/DQ1-market-data-quality.md"]
agents: ["agents/rin/AGENT.md","agents/quant/AGENT.md","agents/researcher/AGENT.md","agents/developer/AGENT.md","agents/tester/AGENT.md","agents/reviewer/AGENT.md"]
skills: ["skills/backtesting/SKILL.md","skills/pnf/SKILL.md","skills/adaptive-box/SKILL.md","skills/postgres/SKILL.md","skills/fastapi/SKILL.md","skills/frontend/SKILL.md","skills/testing/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md", "docs/research/adaptive-box.md"]
translation_needed: false
---

# P10 — Backtest & Research Lab

## เป้าหมาย / traceability
เปรียบเทียบ baseline/fixed/adaptive แบบ reproducible และเชื่อม Backtest Lab กับผลจริง
Requirement: FR-09; FR-08 Backtest Lab

## Entry gate / context
อ่าน root AGENTS.md แล้วโหลดเฉพาะ paths ใน metadata; code/tests อ่านตาม scope
ตรวจทุก depends_on ใน metadata พร้อม Execution record และ merge/check evidence ของ P9 ก่อนเปลี่ยนเป็น ready; dependency file อ่านเฉพาะ status/evidence ไม่โหลด context ของ phase นั้นต่อทั้งหมด
Blocker/decision: baseline/metric/false-entry proxy ยังต้องนิยาม; ห้ามรายงานค่าประเมินก่อน decision/fixtures พร้อม
หากต้องอ่าน implementation contract/ADR ที่ phase ก่อนเพิ่มภายหลัง ให้เพิ่ม exact path + เหตุผลใน metadata ก่อนอ่าน; ไม่เดา path หรืออ่านทั้ง docs

## Scope / deliverables
Paths ที่คาดว่าจะเกี่ยวข้อง: `packages/nexora/backtest`, `apps/api`, `apps/web`, `tests`, `scripts`, `infra`; ตรวจไฟล์จริงก่อนสร้าง
Deliverables: backtest runner, execution/metric decisions, persisted runs, Lab integration และ reproducibility report
Phase 1 เท่านั้น: ห้าม live auto-trading/broker order, secrets และ confirmed OX 10/20/30 semantics

## ขั้นตอน
1. ล็อก dataset manifest/hash, time range, price source, split และ engine/config versions; แยก tuning กับ evaluation
2. Quant กำหนด candlestick baseline, entry/exit/position sizing, execution timing, spread/commission/slippage และ metric definitions ใน decision
3. replay ผ่าน core เดียวกับ observation; ไม่ implement P&F อีกชุดใน backtest
4. คำนวณ trade count, win rate, expectancy, profit factor, max drawdown, false-entry proxy และ latency/entry delay พร้อม units/undefined policies
5. persist run manifest/trades/evidence/metrics และให้ Backtest Lab เลือก runs/compare parameters/ดู assumptions; UI ไม่สร้าง metrics เอง
6. รายงานผลตามข้อมูลจริงพร้อม sample limits/overfitting risks โดยไม่ตีความเป็น live approval

## Dataset Versioning / execution boundary
- Dataset manifest ต้องมี content hashes ของ immutable raw/normalized partitions, schema/normalizer version, source/symbol/price units/timezone, exact range/order policy, gap/reject/correction provenance และ DQ1 quality snapshot
- แยก dataset ID, run ID, engine commit/build version, complete config, seed (ถ้ามี), cost/execution policy และ environment/dependency versions; hash ต้อง canonicalize Decimal/timestamp และ ordering ชัดเจน
- Corrections/backfills สร้าง dataset version ใหม่พร้อม parent/provenance; ไม่ overwrite manifest หรือผลเดิม และ reject missing/hash-mismatched partitions ก่อน run
- P10 sizing เป็น versioned research assumption สำหรับ baseline comparisons เท่านั้น; ไม่ implement production risk authorization ซ้ำ
- P11 ส่งมอบ reusable Risk Engine และ replay parity evidence ก่อน P12; P10 ไม่ขึ้นกับ P11 เพื่อไม่สร้าง task cycle

## Acceptance / validation

- [ ] clean rerun ด้วย dataset + full config + engine/environment versions เดิม ให้ transitions/signals/trades/metrics เท่ากันตาม canonical comparison contract (แยก operational timestamps)
- [ ] mutation/missing partition/hash mismatch fail ชัดเจน; correction/backfill ได้ ID ใหม่และ reproduce dataset เดิมได้
- [ ] manifest completeness/quality flags trace จาก ingestion ถึง stored run/API/Lab; failed/partial runs ไม่แสดงเป็น successful comparison
- [ ] golden replay ให้ engine transitions/signals เท่ากับ observation path
- [ ] hand-calculated trades ตรวจ fees/spread/slippage/equity และทุก metric; zero trades/zero losses ไม่หารศูนย์หรือให้ค่าชวนเข้าใจผิด
- [ ] baseline/fixed/adaptive ใช้ dataset/cost/split protocol เดียวกันและ reproduce ได้
- [ ] ตรวจ no look-ahead รวม signal confirmation/fill timing และ tuning leakage
- [ ] Backtest Lab แสดง stored runs, comparison และ failure/empty states จาก API จริง; metric definitions/assumptions ตรวจย้อนกลับได้
- [ ] ผ่าน root Definition of Done และ handoff/review flow; ไม่ mark done เพียงเพราะ checklist ถูกสร้าง

ใช้ commands ที่ P1 จัดทำและตรวจว่าใช้งานได้กับ checkout ปัจจุบัน; บันทึก exact command/result ด้านล่าง ห้ามอ้าง pass จากคำสั่งตัวอย่าง
Documentation-only change ใช้ path/link/metadata checks และ git diff --check; behavior changes ต้อง relevant tests

## Handoff
Quant decision -> Developer -> Tester -> Reviewer -> Rin
ถ้า FAIL/changes_requested ส่ง expected/actual + minimal reproduction กลับ Developer
หาก self-review ให้ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: not_started
- Context additions: ADR-007 สำหรับ numbering/dependency clarification; docs/development.md สำหรับ validation commands
- Decisions: pending
- Changed files / commit / PR: none
- Checks: not_run
- Review: pending
- Blockers: dependencies P9/DQ1 not completed; ดู decision gate ด้านบน
- Next action: ตรวจ dependency completion evidence แล้วทำ decision/contracts ของ task
