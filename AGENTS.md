# NEXORA — Agent Instructions

NEXORA เป็น price-structure research platform แยกจาก QuantoraTrade; ใช้ Python core, FastAPI/WebSocket, PostgreSQL และ React/Next.js ตาม architecture เดิม

## Source of truth และ safety

- [docs/requirements.md](docs/requirements.md) คือ product scope; [docs/architecture.md](docs/architecture.md) คือ system design
- ไฟล์นี้กำหนด workflow; task/skill/research/ADR ห้าม override source of truth แบบเงียบ ๆ หากขัดกันให้บันทึก conflict และหยุดเฉพาะส่วนที่พึ่ง decision
- Phase 1 ครอบคลุม P1–P8: research, observation, backtest และ paper simulation เท่านั้น ห้าม live auto-trading หรือส่ง broker order รวมถึง demo order
- ห้าม secrets ใน code, docs, fixtures, logs, PR และ config examples; ใช้ runtime environment/local secret store เท่านั้น
- ห้ามเปิด PostgreSQL/MT5 สู่ public Internet; remote web ต้อง authenticated + encrypted
- OX screenshots เป็น observation เท่านั้น; BOX 10/20/30 ไม่ใช่ confirmed specification และห้าม copy proprietary implementation
- Core engine ไม่ขึ้นกับ UI/API/DB; replay/backtest/live observation ใช้ engine เดียวกัน; ทุก signal ต้องมี reasons, evidence, data/config/version references

## เริ่มงานและ context-loading rule

ตัวอย่างคำสั่ง: `ทำ tasks/P1-foundation.md ตาม AGENTS.md`

1. ตรวจ branch, working tree และไฟล์เดิม; อย่าทับงานผู้อื่น อ่าน root และ scoped AGENTS.md ที่เกี่ยวข้อง
2. เปิด task ที่ผู้ใช้ระบุ; ถ้าระบุเพียง Pn ให้ resolve เป็นไฟล์ Pn ใน tasks ห้ามเริ่ม phase ถัดไปเอง
3. อ่านเฉพาะ `docs`, `skills`, `agents` ที่ task ระบุ; ทุก task ต้องระบุ requirements และ architecture และต้องตรวจ dependency evidence ก่อนเริ่ม implementation
4. `agents/*/AGENT.md` คือ role reference; `skills/*/SKILL.md` คือ repo-local instruction ที่เปิดตาม path ใน task ไม่ถือว่ามี runtime registration/auto-discovery
5. อย่าโหลด docs/skills ทั้ง directory หรือแปลเอกสารซ้ำ อ่าน code/tests เฉพาะ scope และ dependency ที่จำเป็น
6. หากต้องเพิ่ม context ให้ระบุ path + เหตุผลใน task ก่อนอ่าน; scoped AGENTS.md และ dependency status เป็นข้อยกเว้นที่ต้องตรวจเสมอ
7. เริ่มจากตรวจ implementation จริง ไม่ถือว่า checkbox, directory หรือข้อความในแชตแปลว่า implementation เสร็จ

## ภาษา

คำอธิบายไทยกระชับ; technical terms, code, API, variables, commit messages และ error messages เป็น English
Lingo ช่วย documentation ได้; แปลเต็มเฉพาะ `translation_needed: true` หรือคำขอเอกสารภายนอก

## Agent Protocol

Role ไม่ได้แปลว่าต้อง spawn agent: คนเดียวทำตาม role ตามลำดับได้ ระบุ self-review ตรง ๆ; delegate เฉพาะเมื่อได้รับอนุญาตและแบ่ง scope ที่ไม่ชนกัน

สถานะ: `ready -> in_progress -> in_review -> done`; `blocked` ใช้เมื่อขาด decision/dependency พร้อม unblock condition; `changes_requested -> in_progress` สำหรับแก้ review
Task ที่สร้างใหม่ยังไม่ใช่ completed implementation และ `ready` เริ่มได้ต่อเมื่อ dependencies ผ่าน

Rin ส่ง assignment โดยใช้ task file + commit เป็น context หลัก:

```yaml
task: tasks/P1-foundation.md
role: developer
base_commit: <commit>
scope: [<paths>]
inputs: [<task-listed paths>]
deliverables: [<artifacts>]
acceptance: [<task criteria>]
constraints: [<relevant boundaries>]
translation_needed: false
```

Handoff ต้องมีข้อมูลพอให้ผู้รับตรวจโดยไม่ replay ทั้งแชต:

```yaml
task: tasks/P1-foundation.md
status: in_review
from: developer
to: tester
commit: <commit>
changed_files: [<paths>]
decisions: [<decision path or none>]
checks:
  - command: <exact command>
    result: <pass|fail|not_run>
    evidence: <short output or artifact>
risks: [<known limits or none>]
blockers: [<issue and unblock condition or none>]
next_action: <specific action>
```

เก็บ handoff/evidence สั้น ๆ ใน task ส่วน Execution record; ใช้ PR สำหรับ diff/review detail ห้ามสร้างข้อมูลผลตรวจหรือ approval ที่ยังไม่เกิดขึ้น

## Handoff และ review flow

1. Rin ตรวจ scope/dependencies; Architect/Quant ล็อก contracts และ formula decisions ที่ task ต้องใช้ก่อนเขียน logic
2. Developer ส่ง implementation + tests + handoff ให้ Tester
3. Tester ส่ง PASS หรือ FAIL พร้อม command, expected/actual และ minimal reproduction; FAIL กลับ Developer
4. Reviewer ตรวจ requirement coverage, regressions, determinism และ evidence; ให้ `approve` หรือ `changes_requested` พร้อม file/line และเหตุผล
5. Security review ใช้กับ data adapter, network/auth, persistence, secrets และ paper boundary ตาม task; Lingo ตรวจเฉพาะเอกสารที่เปลี่ยน
6. Rin รวมผลและอัปเดต task; unresolved acceptance/safety failure ห้าม mark done
7. ถ้าไม่มี independent reviewer ให้ระบุ `self-review; independent review pending` และเปิด draft PR; อย่าอ้างว่าแยก review แล้ว

## Definition of Done

- Deliverables และ acceptance ของ task มี evidence; requirement/design ที่อ้างอิงไม่ถูกเปลี่ยนโดยปริยาย
- Tests ที่เกี่ยวข้องผ่าน รวม negative/boundary/replay cases เมื่อเปลี่ยน engine; lint/type/build ผ่านตาม toolchain ที่มีจริง
- บอก exact commands และผล; unavailable/not_run ต้องระบุเหตุผลและผลกระทบ ไม่ถือว่า pass
- ไม่มี live order path, secrets, public DB/MT5 exposure หรือ unverified OX semantics
- Behavior/config/formula changes มี docs/version/decision ที่สอดคล้อง; ไม่มี speculative defaults แอบกลายเป็น specification
- Handoff และ review findings ถูกจัดการ; done หลัง required review และ merge evidence ครบ ไม่ใช่เพียงเปิด PR

## Git และ validation

ใช้ branch `codex/<scope>`; commit เป็น English แบบ `docs:`, `feat:`, `fix:`; ไม่ push ตรง main และไม่ auto-merge
ก่อนแก้ตรวจ path collision; ก่อน commit ตรวจ staged diff, unintended files และ secrets
ปัจจุบัน repo เริ่มจาก docs-only จึงยังไม่มี application test/lint/build commands; P1 ต้องกำหนดและทดลอง commands จริงก่อนบันทึก
สำหรับ docs-only: ตรวจ links/context paths, metadata, dependency graph, source-of-truth diff และ `git diff --check`; อย่าอ้างว่า application tests ผ่าน
PR ระบุ problem/outcome, changed scope, validation, decisions และ limitations โดยใช้ evidence จริง
