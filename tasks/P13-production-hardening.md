---
task: "P13"
status: "in_review"
depends_on: ["tasks/P12-paper-trading.md"]
agents: ["agents/rin/AGENT.md", "agents/architect/AGENT.md", "agents/developer/AGENT.md", "agents/tester/AGENT.md", "agents/reviewer/AGENT.md", "agents/security/AGENT.md"]
skills: ["skills/postgres/SKILL.md", "skills/fastapi/SKILL.md", "skills/websocket/SKILL.md", "skills/paper-trading/SKILL.md", "skills/testing/SKILL.md", "skills/security/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-007-task-roadmap.md", "docs/development.md"]
translation_needed: false
---

# P13 — Production Hardening

## เป้าหมาย / traceability
Windows home PC / persistence / authenticated encrypted remote access / traceability NFR; harden research/paper operation เท่านั้น

## Entry gate / context
อ่าน [AGENTS.md](../AGENTS.md) และ exact paths ใน metadata; ตรวจ status, execution, review และ merge evidence ของทุก dependency ก่อน ready
เลข phase เป็นลำดับส่งมอบ ไม่ใช่ runtime dependency; contracts ต้องเป็น pure domain ไม่ขึ้นกับ UI/API/DB
Blocker: ต้องยืนยัน dependency completion evidence และ decisions ด้านล่างยัง pending; ห้ามใช้ roadmap เป็น implementation evidence
หากเพิ่ม context ให้บันทึก exact path + เหตุผลก่อนอ่าน; scoped AGENTS.md และ dependency evidence เป็นข้อยกเว้น

## Scope / deliverables
Paths: `infra`, `scripts`, `docs/development.md`, `apps/api`, `apps/web`, integration `tests`
Deliverables: deployment/recovery runbooks, PostgreSQL backup/restore drill, monitoring/alerts, access validation และ reproducible operational evidence

Phase 1: research/backtest/local paper simulation เท่านั้น; ห้าม live/demo broker orders, secrets, public DB/MT5 และ unverified OX semantics
P1–P4 behavior/history คงเดิม; contract change ต้อง explicit ADR/version/migration ไม่ rewrite output ของ run เดิม

## Decision gates / ขั้นตอน
1. กำหนด measurable local capacity/latency/error targets, retention, RPO/RTO และ alert thresholds กับ owner ก่อน validation; ไม่ใส่ค่ารับประกันที่ยังไม่ทดสอบ
2. Backup/restore PostgreSQL + immutable dataset manifests + versioned config/checkpoints; ทดสอบ restore ใน isolated local destination และตรวจ hash/ledger/replay consistency
3. Crash/restart/service ordering/DB outage/disk-full recovery และ schema upgrade/rollback/recovery ต้องไม่สร้าง signal/fill ซ้ำหรือ rewrite history
4. ตรวจ auth/TLS/session expiration และ REST/WS authorization; DB/MT5 private, least privilege, secret redaction/rotation procedure; P9 ต้อง secure ตั้งแต่ส่งมอบ ไม่รอ P13
5. System monitoring ใช้ DQ1 quality/latency และ engine/DB/queue/storage metrics, bounded logs/retention; alert delivery/failure/recovery ตรวจด้วย synthetic incidents
6. จัด start/stop/pause/recover/disaster-recovery runbook และ operator drill; ไม่ deploy public service หรือ enable broker execution เพื่อทดสอบ

## Acceptance / validation
- [ ] restore drill จาก backup ใน clean local environment คืน dataset hashes/versions และ consistent DB/engine/paper ledger; วัด RPO/RTO ตาม approved targets
- [ ] crash/DB outage/disk pressure/restart และ migration failure มี recovery evidence, no duplicate fills/signals และ explicit degraded readiness
- [ ] auth bypass/expired session/unauthorized REST+WS negative tests ผ่าน; encrypted boundary ตรวจจริงก่อน remote access และ DB/MT5 ไม่ public
- [ ] synthetic stale feed/latency/DB/storage failures trigger observable alert และ recovery; logs/backup/config ไม่มี committed secrets
- [ ] capacity/slow-client/backpressure/retention tests ผ่าน declared targets พร้อม Windows environment/version evidence
- [ ] runbooks ทดลอง start/pause/recover/restore จริง; Security review ยืนยัน no live/demo order path; P13 completion ไม่เป็น live approval
- [ ] ผ่าน root Definition of Done; มี exact commands/results, handoff, review และ merge evidence ก่อน done

ใช้ commands ใน docs/development.md ตาม changed scope; behavior ต้องมี synthetic golden/boundary/replay tests และ relevant lint/type/integration checks
Unavailable checks ระบุ not_run พร้อมเหตุผล; docs-only ตรวจ metadata, links, task graph และ git diff --check ไม่อ้าง application tests ผ่าน

## Handoff
Rin -> Architect/Quant decisions (ตาม scope) -> Developer -> Tester -> Reviewer (+ Security เมื่อมี persistence/network/risk/paper boundary) -> Rin
FAIL ให้ expected/actual + minimal reproduction; self-review ระบุ independent review pending และเปิด draft PR

## Execution record
- Implementation: local operations readiness/alerts contracts, unauthorized REST+WS guards validation, deterministic recovery drill script, and dashboard system-readiness surface wired
- Context additions: [ADR-017](../docs/decisions/ADR-017-production-hardening-local-operations.md), [docs/development.md](../docs/development.md) operations/recovery commands
- Decisions: degraded-readiness fail-closed status with explicit reason codes, synthetic operations alerts, and migration-presence + deterministic replay checkpoint drill as local hardening evidence
- Changed files / commit / PR: `apps/api/nexora_api/main.py`, `apps/web/app/page.tsx`, `tests/test_dashboard_api.py`, `scripts/recovery_drill.py`, `docs/decisions/ADR-017-production-hardening-local-operations.md`, `docs/development.md`
- Checks:
  - `d:/NEXORA/NEXORA/.venv/Scripts/python.exe -m pytest tests/test_dashboard_api.py tests/test_paper.py tests/test_backtest.py tests/test_health.py`: PASS
  - `d:/NEXORA/NEXORA/.venv/Scripts/python.exe -m ruff check apps/api/nexora_api packages/nexora/backtest packages/nexora/paper tests/test_dashboard_api.py scripts/recovery_drill.py`: PASS
  - `d:/NEXORA/NEXORA/.venv/Scripts/python.exe -m mypy apps/api/nexora_api packages/nexora/backtest packages/nexora/paper tests/test_dashboard_api.py scripts/recovery_drill.py`: PASS
  - `d:/NEXORA/NEXORA/.venv/Scripts/python.exe scripts/recovery_drill.py`: PASS
  - `npm --prefix apps/web run lint`: PASS
  - `npm --prefix apps/web run typecheck`: PASS
  - `npm --prefix apps/web run build`: PASS
  - `d:/NEXORA/NEXORA/.venv/Scripts/python.exe -m pytest tests`: PASS
- Review: self-review complete; independent review pending
- Blockers: security review evidence and independent review pending before done gate
- Next action: open draft PR for P13 and collect security/reviewer evidence
