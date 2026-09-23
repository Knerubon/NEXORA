# NEXORA Multi-Agent Development Protocol V1

NEXORA เป็น price-structure research platform แยกจาก QuantoraTrade; ใช้ Python core, FastAPI/WebSocket, PostgreSQL และ React/Next.js ตาม architecture เดิม

This file defines workflow and governance. It does not change product scope or system design.

## 0. Source of truth และ safety

- [docs/requirements.md](docs/requirements.md) คือ product scope; [docs/architecture.md](docs/architecture.md) คือ system design; accepted ADRs in [docs/decisions](docs/decisions) freeze specific contracts/formulas
- ไฟล์นี้กำหนด workflow; task/skill/research/ADR ห้าม override source of truth แบบเงียบ ๆ หากขัดกันให้บันทึก conflict และหยุดเฉพาะส่วนที่พึ่ง decision
- Phase 1 ครอบคลุม execution tasks P1–P13 และ DQ1 ตาม [ADR-007](docs/decisions/ADR-007-task-roadmap.md): research, observation, backtest และ paper simulation เท่านั้น ห้าม live auto-trading หรือส่ง broker order รวมถึง demo order
- ห้าม secrets ใน code, docs, fixtures, logs, PR และ config examples; ใช้ runtime environment/local secret store เท่านั้น
- ห้ามเปิด PostgreSQL/MT5 สู่ public Internet; remote web ต้อง authenticated + encrypted
- OX screenshots เป็น observation เท่านั้น; BOX 10/20/30 ไม่ใช่ confirmed specification และห้าม copy proprietary implementation
- Core engine ไม่ขึ้นกับ UI/API/DB; replay/backtest/live observation ใช้ engine เดียวกัน; ทุก signal ต้องมี reasons, evidence, data/config/version references

## 1. Multi-agent team model

A role is a responsibility, not a requirement to spawn an agent. One agent may perform several roles in sequence, but must state which role it is acting in and must state self-review honestly. Delegate only when authorized and when scopes do not collide.

Existing role references in `agents/*/AGENT.md` remain valid. The workstream roles below specialize them: DEV-* roles act as `developer`, QA acts as `tester`, ARCHITECT/INTEGRATOR combines `architect` and the integration duties of `rin`. Quant still owns formula/threshold decisions; Security and Lingo reviews still apply where triggered (section 12).

| Role | Owns | Must not |
|---|---|---|
| ARCHITECT / INTEGRATOR | System architecture, cross-component boundaries, freezing shared contracts before dependent work, dependency/integration order, review of architecture-impacting changes, multi-agent coordination, PR scope and integration readiness | Authorize merge to `main` — human approval is always required |
| DEV-PERF | Backend performance, startup/recovery performance, snapshot/checkpoint/recovery, recovery-related persistence, replay reduction | Trade recovery correctness for speed; recovered state must be deterministic and equivalent to full replay |
| DEV-PNF | P&F domain logic: X/O column interpretation, pattern and structure detection (Double/Triple Top/Bottom, ascending/descending structures, HH/HL/LH/LL, X raises X, O raises O, breakout/breakdown, catapult) | Emit BUY/SELL permission; pattern state is evidence only |
| DEV-CHART | P&F visualization, structure/pattern overlays, previous X High / O Low lines, support/resistance, breakout/breakdown markers, chart UX | Build an independent pattern/trading engine in the UI; it renders the shared Pattern contract |
| DEV-ENTRY | Entry Readiness: deterministic entry permission/filter logic, consumption of Pattern evidence, trendline-aware filtering, confirmation/blocker handling | Treat a detected pattern as automatic BUY/SELL permission, or guess unfinished Pattern semantics |
| QA | Acceptance verification, targeted/regression/contract/integration/frontend tests, reproductions, edge cases, PR test evidence | Change production logic just to make a test pass; report the failure instead |

Current DEV-PERF workstream: Research recovery must not replay the entire historical event journal on every restart, while preserving deterministic recovery correctness.

## 2. Core architecture rule — one source of truth for patterns

```text
Market / X-O data
        │
        ▼
P&F Pattern Engine          (calculates pattern/structure state ONCE)
        │
        ▼
Shared Pattern Contract
    ┌───┴──────────┐
    ▼              ▼
  Chart       Entry Readiness
 (renders)    (consumes evidence)
                   │
                   ▼
        deterministic decision
                   │
                   ▼
      AI explanation / commentary
```

- The Pattern Engine is the only place pattern/structure logic is computed. No duplicate pattern algorithms in frontend, API, Entry Readiness or AI layers.
- Chart renders pattern state; Entry Readiness consumes it; neither recomputes it.
- AI may explain or analyze deterministic results but must never silently override deterministic trading state, and its output must stay visibly distinct from system state.
- This refines, and does not replace, existing design: market structure lifecycle ([ADR-009](docs/decisions/ADR-009-market-structure-lifecycle.md)), signal evidence ([ADR-011](docs/decisions/ADR-011-signal-evidence-policy.md)) and risk policy ([ADR-015](docs/decisions/ADR-015-risk-engine-policy.md)).

## 3. Shared contract governance

Cross-subsystem interfaces must be frozen (by ADR or recorded architecture review) before dependent work is implemented in parallel.

Conceptual example only — not a mandated schema:

```text
PatternResult
  pattern_type, direction, status, trigger_price,
  reference_level, column_index, evidence
```

The actual schema is whatever the ADR/architecture review freezes; if the existing model already fits better, extend it rather than forcing these fields. Agents must not invent their own incompatible versions of a shared contract.

To change a frozen contract:

1. Stop dependent implementation.
2. Document the proposed change.
3. Evaluate every consumer.
4. Update and approve the ADR / architecture decision.
5. Update contract tests.
6. Resume dependent work.

## 4. Parallel development model

Independent workstreams may run at the same time when dependencies allow.

```text
        P&F Contract (frozen)
              │
        ┌─────┴─────┐
        ▼           ▼
     DEV-PNF    DEV-CHART
        │           │
        └─────┬─────┘
              ▼
         DEV-ENTRY
              ▼
             QA
              ▼
     Integration Review
```

- DEV-CHART may build rendering infrastructure early; anything that depends on Pattern semantics waits for the frozen contract.
- DEV-ENTRY must not guess unfinished Pattern Engine semantics.
- DEV-PERF is independent of the pattern chain but shares persistence/recovery code with the research runtime; coordinate on shared files (section 7).

Current workstreams (these describe work in flight, not permanent architecture):

| Stream | Role | Scope |
|---|---|---|
| A. Startup Performance | DEV-PERF | snapshot/checkpoint/recovery optimization |
| B. P&F Pattern Engine | DEV-PNF | deterministic X/O pattern and structure detection |
| C. P&F Chart Overlay | DEV-CHART | structure lines / pattern visualization |
| D. Entry Readiness Integration | DEV-ENTRY | consume deterministic pattern evidence |
| E. QA | QA | independent validation across A–D |

## 5. Worktree isolation

Every implementation workstream uses its own Git worktree:

```text
D:\NEXORA\
  NEXORA\                      main / integration worktree — NOT a dev workspace
  NEXORA-<SCOPE>\              one per active workstream, e.g.
  NEXORA-STARTUP-RECOVERY\       claude/startup-recovery-v1
  NEXORA-AGENTS-PROTOCOL\        claude/agents-protocol-v1
```

Directory names are examples. They describe Git checkouts only and say nothing about which runtime environment a worktree serves (section 8).

Never implement features directly in `D:\NEXORA\NEXORA` while it serves as the main/integration worktree. If it (or any worktree you do not own) has uncommitted changes, treat them as someone else's work in progress: do not stash, reset, restore, checkout, clean, commit, move, reformat or overwrite them — report them. If a change of yours must leave such a worktree, copy only your own files into your dedicated worktree and leave the original untouched until explicitly authorized.

Before starting a workstream:

1. `git fetch origin`
2. Verify the target branch and worktree path do not already exist and are not in use by another agent/session (section 5.1).
3. Verify the intended base (approved `origin/main` SHA).
4. Create the branch from that SHA and a dedicated worktree: `git worktree add ..\NEXORA-<SCOPE> -b <branch> <sha>`
5. Verify `git status --short` is clean in the new worktree and `git rev-parse HEAD` equals the base SHA; report both.

### 5.1 Worktree ownership and concurrent sessions

One active implementation workstream owns exactly one branch and one worktree:

```text
Workstream
   ├── owner (agent/session, e.g. scratchpad or session id)
   ├── branch
   └── worktree
```

- Only the owner edits files, commits, runs benchmarks or starts processes in that worktree. Ownership changes only by explicit human/Rin decision.
- Other sessions may read committed history from Git, but must not modify the worktree, commit to its branch, stop its processes, touch its scratchpad or build a competing implementation of the same workstream.
- Signs of another active owner include: the branch/worktree already exists, reflog entries you did not create, running processes whose command line points into the worktree, or another session's scratch files referencing it.
- If you find any of these and ownership was not assigned to you: **STOP — WORKTREE ALREADY IN USE.** Report the branch, worktree, evidence (process/scratchpad/reflog) and wait. Do not take over, clean up, or create a parallel copy.

## 6. Branch convention

New workstream branches use `<agent>/<scope>-<feature>-vN`, where the prefix identifies the owning implementation agent: `claude/<scope>-<feature>-vN` or `codex/<scope>-<feature>-vN`. Examples: `claude/startup-recovery-v1`, `claude/pnf-pattern-engine-v1`, `claude/pnf-overlay-v1`, `claude/pattern-entry-integration-v1`. Existing `codex/*` branches are historical and must not be renamed or modified.

- One primary purpose per branch.
- No unrelated refactoring or opportunistic cleanup outside scope.
- Do not reuse stale branches for unrelated tasks.
- Every handoff/report states the base SHA.
- Commit messages are English, `docs:` / `feat:` / `fix:` style.

## 7. File / code ownership

Ownership is logical responsibility, not permission to rewrite everything in a directory.

| Role | Logical ownership |
|---|---|
| DEV-PERF | performance, recovery, recovery-related persistence |
| DEV-PNF | P&F domain and pattern calculation |
| DEV-CHART | chart rendering and visualization |
| DEV-ENTRY | entry-readiness / permission pipeline |
| QA | tests, fixtures, test infrastructure |

Shared files:

- Identify shared-file impact before editing; check for path collisions with other active branches.
- Keep shared-file changes minimal; never overwrite another agent's concurrent design.
- Shared contract/schema changes require Architect coordination (section 3).
- If you reach code owned by another active workstream, stop and report the dependency instead of redesigning it.

## 8. Environment isolation (DEV / PROD)

Development must not disturb any running NEXORA runtime. Never overwrite or contaminate its database, research state, snapshots, checkpoints, journal, storage, logs, ports, runtime directories, live feed state, configuration or secrets.

**Git worktree identity ≠ runtime environment identity.** A worktree is a code checkout; a runtime environment is defined by configuration, per [docs/environment-isolation.md](docs/environment-isolation.md) and `apps/api/nexora_api/environment.py`:

- `NEXORA_ENV` (`development` | `production`) selects the profile in `apps/api/nexora_api/environments.json` (host/ports) and the matching root `.env.development` / `.env.production`; the legacy root `.env` is not loaded.
- `NEXORA_RUNTIME_ROOT` (default `<worktree>/.runtime/<env>`) owns writable paths; `NEXORA_JOURNAL_PATH`, `NEXORA_STATE_PATH`, `NEXORA_CHECKPOINT_PATH`, `NEXORA_LOG_PATH`, `NEXORA_CACHE_PATH` must resolve inside it, and `.nexora-environment.json` markers record ownership.
- `NEXORA_POSTGRES_DSN`, when set, replaces SQLite and must pass the Postgres environment identity check.
- Any worktree can run as DEV or PROD, and a worktree's name (including `D:\NEXORA\NEXORA`) is never proof of either. Layout statements in docs describe intent at the time they were written. For example, PROD has been observed running from a feature worktree rather than `D:\NEXORA\NEXORA`, and older checkouts may still use pre-isolation paths such as `data/research.sqlite`.

### 8.1 Runtime identity check before touching a running environment

Before any action that could affect a running environment (starting/stopping/restarting services, killing processes, reading large live databases, migrations, writing to runtime storage, binding ports), determine and report:

```text
worktree, branch, NEXORA_ENV, runtime root,
configured journal/storage (SQLite path or Postgres identity), service ports,
owning process (PID + command line) if running
```

Obtain these from the actual process/configuration, not from directory names or documentation alone. If ownership or runtime identity is ambiguous: **STOP — RUNTIME OWNERSHIP UNCLEAR.** Do not kill processes, modify runtime storage, migrate databases or restart services until it is resolved.

### 8.2 Data safety

- Dev worktrees and tests point every runtime variable above at isolated locations, never at a runtime used for observation.
- Never point destructive tests, migrations, benchmarks or recovery experiments at production storage; do not copy, VACUUM, checkpoint or replay-write production databases. Use synthetic/fixture journals unless a read-safe validation procedure is explicitly approved.
- Tests use isolated test data and temporary runtime directories.
- Dev servers use the development profile ports; occupied ports are reported, never freed by killing processes.

## 9. Trading logic safety

Trading semantics are architecture-sensitive. Stop and escalate (Architect + Quant, then human) when requirements are ambiguous around BUY, SELL, WAIT, BLOCKED, READY, Entry Readiness, pattern confirmation, breakout/breakdown, stop loss, risk logic, position sizing or any deterministic trading decision.

- Do not invent trading semantics to finish an implementation; speculative defaults must not become specification.
- Pattern detection is evidence. Pattern detection alone is never permission to trade.
- AI commentary must remain distinguishable from deterministic system state.
- Phase 1 remains research/observation/backtest/paper only (section 0).

## 10. Absolute main-branch safety rule

Agents MUST NOT:

- merge a PR into `main`
- commit directly to `main` or push directly to `main`
- force-push `main`
- bypass branch protection, required review or CI
- bypass Claude Auto-mode safety controls or work around blocked commands
- delete, move or rewrite release tags
- rewrite published history

This holds even if all tests pass, CI is green, the PR is approved, the change looks trivial, or the user merged similar work before. The agent stops after preparing the PR and validation report. **Final merge requires an explicit human instruction.**

If Claude Auto mode (or any permission/safety control) blocks an operation: STOP and report the blocked action. Never try an alternative command whose purpose is to get around that decision.

## 11. Emergency stop / escalation

Stop implementation and report when you encounter:

- ambiguous architecture or unclear ownership
- another active session/process in your target worktree (**STOP — WORKTREE ALREADY IN USE**, section 5.1)
- unclear DEV/PROD runtime identity (**STOP — RUNTIME OWNERSHIP UNCLEAR**, section 8.1)
- incompatible shared contracts
- dependency on another unfinished workstream
- unexpected production impact, production database risk or destructive migration
- secret/credential exposure
- trading-state corruption risk
- unexplained large diff or unexpected generated files
- failing baseline tests
- a need to bypass a safety mechanism

Do not "solve around" these conditions. For task-level blockers, stop only the part that depends on the blocker and state the unblock condition.

## 12. Task workflow and context loading

ตัวอย่างคำสั่ง: `ทำ tasks/P1-foundation.md ตาม AGENTS.md`

1. ตรวจ branch, worktree, working tree และไฟล์เดิม; อย่าทับงานผู้อื่น อ่าน root และ scoped AGENTS.md ที่เกี่ยวข้อง (เช่น [apps/web/AGENTS.md](apps/web/AGENTS.md))
2. ใช้ [roadmap](docs/roadmap.md) และ ADR-007 resolve historical P5–P8 references; P1–P4 records เป็น immutable history เปิด task ที่ผู้ใช้ระบุ; ถ้าระบุเพียง Pn ให้ resolve เป็นไฟล์ Pn ใน tasks ห้ามเริ่ม phase ถัดไปเอง
3. อ่านเฉพาะ `docs`, `skills`, `agents` ที่ task ระบุ; ทุก task ต้องระบุ requirements และ architecture และต้องตรวจ dependency evidence ก่อนเริ่ม implementation
4. `agents/*/AGENT.md` คือ role reference; `skills/*/SKILL.md` คือ repo-local instruction ที่เปิดตาม path ใน task ไม่ถือว่ามี runtime registration/auto-discovery
5. อย่าโหลด docs/skills ทั้ง directory หรือแปลเอกสารซ้ำ อ่าน code/tests เฉพาะ scope และ dependency ที่จำเป็น
6. หากต้องเพิ่ม context ให้ระบุ path + เหตุผลใน task ก่อนอ่าน; scoped AGENTS.md และ dependency status เป็นข้อยกเว้นที่ต้องตรวจเสมอ
7. เริ่มจากตรวจ implementation จริง ไม่ถือว่า checkbox, directory หรือข้อความในแชตแปลว่า implementation เสร็จ

Task status: `ready -> in_progress -> in_review -> done`; `blocked` ใช้เมื่อขาด decision/dependency พร้อม unblock condition; `changes_requested -> in_progress` สำหรับแก้ review. Task ที่สร้างใหม่ยังไม่ใช่ completed implementation และ `ready` เริ่มได้ต่อเมื่อ dependencies ผ่าน

Review flow:

1. Architect/Integrator ตรวจ scope/dependencies; Architect/Quant ล็อก contracts และ formula decisions ก่อนเขียน logic
2. DEV-* ส่ง implementation + tests + handoff (section 16) ให้ QA
3. QA ส่ง PASS หรือ FAIL พร้อม command, expected/actual และ minimal reproduction; FAIL กลับ developer
4. Reviewer ตรวจ requirement coverage, regressions, determinism และ evidence; ให้ `approve` หรือ `changes_requested` พร้อม file/line และเหตุผล
5. Security review ใช้กับ data adapter, network/auth, persistence, secrets และ paper boundary; Lingo ตรวจเฉพาะเอกสารที่เปลี่ยน
6. Integrator รวมผลและอัปเดต task Execution record; unresolved acceptance/safety failure ห้าม mark done
7. ถ้าไม่มี independent reviewer ให้ระบุ `self-review; independent review pending` และเปิด draft PR; อย่าอ้างว่าแยก review แล้ว ห้ามสร้างผลตรวจหรือ approval ที่ยังไม่เกิดขึ้น

## 13. QA gate

A PR is not integration-ready just because it compiles. Where applicable, QA verifies:

- acceptance criteria and deterministic behavior
- targeted tests, regression suite, backend and frontend tests
- contract compatibility with all consumers
- runtime isolation and no production-data contamination
- recovery correctness and restart behavior
- no unexpected diff and no hidden scope expansion

Performance work includes BEFORE/AFTER measurements where practical.

Snapshot/checkpoint work must additionally verify:

- cold recovery correctness
- checkpoint recovery correctness
- fallback when the checkpoint is missing
- fallback when the checkpoint is invalid/corrupt
- recovery after new events are appended
- deterministic state equivalence with full replay

## 14. PR protocol

Every implementation workstream finishes through a scoped PR (draft when independent review is pending).

Before opening the PR:

- inspect the exact staged diff; verify no unexpected files and no secrets
- run targeted tests, the relevant full suite, and lint/type/build checks per [development guide](docs/development.md)
- document failures honestly; `not_run` needs a reason and impact, and is never reported as pass
- fetch `origin/main` and verify branch/base (section 15)

PR description includes: scope, problem/outcome, architecture/ADR reference, base SHA, branch, worktree, commits, exact changed-file list, tests executed with results, known limitations, dependencies, blockers, migration/runtime impact. No unrelated changes may be hidden in the PR.

For docs-only changes: ตรวจ links/context paths, metadata, dependency graph, source-of-truth diff และ `git diff --check`; อย่าอ้างว่า application tests ผ่าน

## 15. Stale main / rebase / conflict protocol

Before final validation, fetch `origin/main` and check whether the branch base is stale. If `main` changed materially: report the new SHA, decide whether a rebase/update is needed, and rerun affected tests after updating.

- Do not blindly resolve semantic conflicts; never pick ours/theirs for domain logic without understanding both sides.
- Cross-agent contract conflicts require Architect review.
- Trading-decision conflicts require escalation (section 9).
- Rerun all affected validation after resolving conflicts.
- Rebasing your own unpublished feature branch is fine; never rewrite published shared history.

## 16. Agent handoff format

Every development agent finishes with:

```text
ROLE:
OWNER SESSION:
WORKSTREAM:
WORKTREE:
BRANCH:
BASE SHA:

STATUS: COMPLETED | BLOCKED | READY FOR REVIEW

COMMITS:
- ...
CHANGED FILES:
- ...
TESTS:
- <exact command>
  <pass | fail | not_run (reason)> — <short evidence>
ACCEPTANCE CRITERIA:
- PASS/FAIL ...
DEPENDENCIES:
- ...
BLOCKERS:
- <issue and unblock condition, or none>
ARCHITECTURE NOTES:
- <decision/ADR paths, contract changes, or none>
RUNTIME / DATA IMPACT:
- ...
PR:
- <number/link or NOT CREATED>
MERGE:
NOT PERFORMED

NEXT RECOMMENDED ACTION:
...
```

Keep a short copy of the handoff/evidence in the task's Execution record; detailed diff/review lives in the PR.

## 17. Definition of Done

- Deliverables และ acceptance ของ task มี evidence; requirement/design ที่อ้างอิงไม่ถูกเปลี่ยนโดยปริยาย
- Tests ที่เกี่ยวข้องผ่าน รวม negative/boundary/replay cases เมื่อเปลี่ยน engine; lint/type/build ผ่านตาม toolchain ที่มีจริง
- บอก exact commands และผล; unavailable/not_run ต้องระบุเหตุผลและผลกระทบ ไม่ถือว่า pass
- ไม่มี live order path, secrets, public DB/MT5 exposure, production-data contamination หรือ unverified OX semantics
- Pattern logic exists only in the Pattern Engine; Entry Readiness and Chart consume the frozen contract
- Behavior/config/formula changes มี docs/version/decision ที่สอดคล้อง; ไม่มี speculative defaults แอบกลายเป็น specification
- Handoff และ review findings ถูกจัดการ; done หลัง required review และ human merge evidence ครบ ไม่ใช่เพียงเปิด PR

Repo มี runnable P1–P4 implementation แล้ว; commands อยู่ใน [development guide](docs/development.md) และ historical task evidence ต้องตรวจว่าใช้ได้กับ checkout ปัจจุบันก่อนอ้าง pass

## 18. ภาษา

คำอธิบายไทยกระชับ; technical terms, code, API, variables, commit messages และ error messages เป็น English
Lingo ช่วย documentation ได้; แปลเต็มเฉพาะ `translation_needed: true` หรือคำขอเอกสารภายนอก

## 19. Corrective work

[FIX1](tasks/FIX1-system-readiness.md) tracks the post-merge readiness corrections under [ADR-018](docs/decisions/ADR-018-readiness-corrections.md). Historical P1–P4 evidence remains unchanged; see [runtime release gates](docs/research-runtime.md) for unverified operational requirements.
