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
P5–P13 implementation merged ถึง e16ef34 แต่ audit พบ integration/correctness gaps; merged ไม่เท่ากับผ่าน acceptance ทั้งหมด ดู [FIX1](../tasks/FIX1-system-readiness.md) และ [ADR-018](decisions/ADR-018-readiness-corrections.md). Historical execution records คงเดิม; independent review และ operational evidence ยังเป็น release gates.
P5 ล็อก configs/snapshot; P6 pivot/S&R; P7 regime rules; P8 signal rules; P9 transport/auth; P10 dataset/costs/metrics/splits; P11 risk policy; P12 simulated fill/accounting; P13 measurable recovery/operational targets
Numeric thresholds/formulas ยังต้อง reviewed decisions และ golden fixtures; OX observations ไม่ใช่ specification

## Historical references
Original P5 scope แยก P5–P8; original P6/P7/P8 ย้าย P9/P10/P12 และแยก Risk เป็น P11
[Original task snapshot](https://github.com/Knerubon/NEXORA/tree/346c9524fa2aeca1bb95b14a579a69424f6c73c0/tasks) เก็บรายละเอียดก่อน refactor
[UX1 local quote preview](../tasks/UX1-mt5-price-preview.md) เป็น historical bounded preview; ไม่ใช่ completion evidence ของ full P9
Future live execution อยู่นอก task graph นี้และต้อง separate scope/approval/risk controls

## Status ledger (2026-09-27)

Reconciled against `main` `8437cdc` (Merge PR #42). This ledger is a navigation index only. Completion evidence still lives in each task Execution record plus its review/merge evidence (see "Status and decision gates" above). `done` requires recorded required review **and** human merge (AGENTS.md §17). Merged work without recorded independent review stays `in_review`: it is merged and not active, so do not re-implement it.

### Complete: merged, independent review recorded
| Work | Record | PR (merge) | Review evidence |
|---|---|---|---|
| DRAW1 Manual Drawing V1 | [DRAW1](../tasks/DRAW1-manual-drawing-v1.md) `done` | #40 (`a449f19`) | independent APPROVED at `ce5a934` |
| UI-DECISION-1 signal summary | [UI-DECISION-1](../tasks/UI-DECISION-1-signal-summary.md) `done` | #38 (`fb0ff3b`) | independent review APPROVED (PR #38) |
| UI-READINESS-1 Entry Readiness UI | [UI-READINESS-1](../tasks/UI-READINESS-1-entry-readiness-ui.md) `done` | #41 (`ad9dcb9`) | independent review APPROVED (PR #41) |
| Pattern Engine V1 Phase 2 (ADR-023/024) | [PNF2](../tasks/PNF2-pattern-phase2-integration.md) `done` | #39 (`816c8d7`) | final independent review APPROVED (PR #39) |
| M30 Next-Candle Bias Phase 2A (ADR-026) | [M30B1](../tasks/M30B1-next-candle-bias-v1.md) (task `blocked` on Phase 2B) | #33 (`f8267da`) | independent re-review APPROVED (PR #33) |
| PERF-2 Experience replay performance (ADR-031 Phase 2A) | no task file; ADR-031 | #37 (`6cdff36`) | independent review APPROVED (PR #37) |
| MT5 multi-broker Phase 2A (ADR-025, ADR-003 Amendment 1) | no task file; ADR-025 | #42 (`8437cdc`) | original content independently APPROVED; republication merged by Rin |

### Merged, independent review not recorded (`in_review`, not active)
| Work | Record | PR (merge) |
|---|---|---|
| Trendline Engine Phase 1 (ADR-020) | no task file; ADR-020 accepted | #28 (`89264e4`) |
| Entry Readiness V1 backend (ADR-021) | no task file; ADR-021 accepted | #29 (`efeef59`) |
| Chart Visual Intelligence V1 overlays | no task file | #31 (`e2ba8ba`) |
| Startup Recovery V1 (ADR-022) | no task file; ADR-022 revision 2 | #32 (`4f69e9c`) |
| VALID-1 Phase 2A (ADR-030) | [VALID1](../tasks/VALID1-replay-validation-v1.md) (task `blocked` on Phase 2B) | #34 (`f5bbdfa`) |
| PERF-1 Phase 2A (ADR-029 draft) | [PERF1](../tasks/PERF1-recovery-checkpoint-hardening.md) | #35 (`f2d51ad`) |
| EXC1 Experience journal compatibility (ADR-028 accepted) | [EXC1](../tasks/EXC1-experience-journal-compat.md) | #36 (`ec0aad5`) |
| FIX2 API startup recovery | [FIX2 API](../tasks/FIX2-api-startup-recovery.md) | #22 (`f5f388f`, commit `c902721`) |
| FIX3 realtime quote | [FIX3](../tasks/FIX3-realtime-quote.md) | #22 (`f5f388f`) |
| FIX2 MT5 feed time | [FIX2 feed](../tasks/FIX2-mt5-feed-time.md) | #14 (`e2b0bed`) |
| UX2 P&F cell centering | [UX2 local](../tasks/UX2-local-pnf-cell-center.md), [UX2 rendering](../tasks/UX2-pnf-cell-centered-rendering.md) | #15 (`fa562c9`) |
| UI1 chart-first restoration | [UI1](../tasks/UI1-chart-first-restoration.md) | #17 (`d856884`) |
| CI1 Python validation | [CI1](../tasks/CI1-python-validation.md) | #18 (`c136dc7`) |
| P8B Signal Intelligence | [P8B](../tasks/P8B-signal-intelligence.md) | #19 (`8dd31a2`) |
| Matrix decision panel | [UI matrix](../tasks/UI-matrix-decision-panel.md) | #20 (`81854be`) |
| EX1 Experience Engine V1 | [EX1](../tasks/EX1-experience-engine-v1.md) | #21 (`130f260`) |
| ENV1 environment isolation | [ENV1](../tasks/ENV1-environment-isolation.md) | #23 (`ee1d88d`) |
| DC1 Decision Clarity + Bias V1 | [DC1](../tasks/DC1-decision-clarity-bias-v1.md) | #24 (`33fc471`) |
| UX1 local quote preview (historical bounded preview) | [UX1](../tasks/UX1-mt5-price-preview.md) | #3 (`10564eb`) |

P1–P3 are `done`. P4–P13, DQ1 and FIX1 keep their historical records unchanged: they are merged, and independent review, acceptance and release gates remain as stated above and in [FIX1](../tasks/FIX1-system-readiness.md). REMOTE1 is implemented and awaits external 5G verification and tunnel provisioning (#25, #27). [P8A](../tasks/P8A-signal-pattern-scoring.md) has no status field and its completion is unverified; this needs a Rin decision.

### In progress
None active as of `8437cdc`.

### Blocked / on hold
| Work | Unblock condition |
|---|---|
| M30 Phase 2B (sidecar, checkpoint section, wiring) | Quant Q-M3/Q-M4/Q-M8; Track B coordination on `research/runtime.py` and `checkpoint_state.py` |
| VALID-1 Phase 2B (offline read-only journal extraction) / 2C | explicit Rin authorization (HOLD 2026-09-27 while REPLAY-MEM-1 was open); Quant Q-V2–Q-V5 before evidence-grade runs |
| Pattern Engine Phase 3 (legacy SignalEngine pattern migration) | Quant Q-PE2 (lock strategy), Q-PE3 (`structure_double_*` migration) |
| MT5 multi-broker Phase 2B (live feed wiring) | Quant Q-Q1 (grid vs box_size); fix MINOR-1 (`observe_symbol` records the requested name, not `info.name`) before wiring. Carried items: NIT-1 (stale ADR-025 wording), NIT-2 (duplicate `terminal_path` with a different company fails only at resolve), ADR-025 terminal-path wording (`normcase+abspath` vs implemented `normcase(normpath)`), discovery-tool runbook note (check→initialize race; shared terminal with PROD) |
| Journal Payload V2 / compaction (ADR-027 draft, local branch only, not on `main`) | architecture review; related to the O(n²) journal growth documented by REPLAY-MEM-1 |
| TS1 time semantics | separate task approval and Architect/Quant versioned time contract |

### Release / recovery (deferred)
- REPLAY-MEM-1 (`claude/replay-memory-bound-v1`, including `daa065e`): the independent review concluded **NO_MEMORY_BLOCKER_FOUND**. The historical `MemoryError` root cause remains unconfirmed. The branch is preserved and not merged or deployed. Do not reopen without Rin.
- Gate 2 / release `1.4.0`: not passed and not re-run; `1.4.0` is not tagged. The validated candidate `a449f19` is no longer the `main` HEAD.
- ADR-029 D3 (time/idle checkpoint trigger) and H6 (deferred skipped-row verification); ADR-031 C3(c) and C8: deferred by their ADRs.

### Backlog (not started)
| ID | Goal | Priority | Status | Constraints to preserve |
|---|---|---|---|---|
| READINESS-MARKET-1 Market-Aware Feed Readiness | Distinguish an intentionally closed market from a broken or stale feed (states such as LIVE, MARKET_CLOSED, STALE, DISCONNECTED, DEGRADED) | MEDIUM | BACKLOG / NOT STARTED | not hard-coded to Saturday/Sunday; session-, holiday- and instrument-specific market hours; stale or closed-market prices must never create actionable decisions |
| MEM-CAPACITY-1 Runtime Memory Capacity & Retention Policy | Long-term capacity planning for intentionally retained runtime state (`_events`, `_seen`, per-runner decisions, Experience history, checkpoint write/restore spikes) | — | BACKLOG / DEFERRED | not a current defect blocker (REPLAY-MEM-1: NO_MEMORY_BLOCKER_FOUND); do not reopen the memory investigation without Rin |
