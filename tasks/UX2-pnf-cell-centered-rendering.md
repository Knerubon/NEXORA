---
task: UX2
status: in_review
depends_on: ["tasks/P9-web-dashboard.md", "tasks/FIX1-system-readiness.md"]
agents: []
skills: ["skills/frontend/SKILL.md", "skills/testing/SKILL.md"]
docs: ["docs/requirements.md", "docs/architecture.md", "docs/development.md"]
translation_needed: false
---

# UX2 — P&F cell-centered rendering

## Scope / context
Visualization-only correction requested by user; base 2b683e9 (latest main, PR #13).
Read apps/web, packages/nexora/pnf/{models,engine}.py and packages/nexora/research/pipeline.py to verify the existing serialized geometry contract; engine files are read-only context.
P9/FIX1 implementation is merged but independent review remains pending. This bounded UX correction does not certify those phases or alter their gates.
Read requirements/architecture for FR-02/FR-07 boundaries and development/frontend/testing instructions for validation.

## Acceptance
- Center X/O within rendered price cells, preserving actual source prices in tooltips.
- Do not change box/reversal calculations, transitions, Matrix, signals, persistence or API semantics.
- Verify multiple rows/columns and responsive scaling; focused frontend tests, lint/type/build.
- Commit and draft PR; self-review, independent review pending; do not merge.

## Execution record
- Dependency inspection: existing StructureChart draws column spans and endpoint labels, not a box grid. Inspect available source geometry before choosing a faithful display.

## Implementation / handoff
- Scope: `apps/web/app/{page.tsx,structure-chart.tsx,pnf-layout.ts,globals.css}`, `apps/web/tests`, `apps/web/package.json`, `.github/workflows/validate.yml` (run the new frontend regression tests in CI), this task.
- ใช้ transitions ที่ API ส่งอยู่แล้วเพื่อแสดง confirmed boxes; ไม่อนุมาน box size จาก open/close หรือคำนวณ reversal ใหม่
- Cell lower boundary คือราคาจริง; upper boundary คือราคาถัดไปในคอลัมน์ (ช่องบนสุดใช้ recorded box size) รองรับ adaptive intervals โดยช่องไม่ซ้อนกัน
- Glyph center คือ midpoint ของ boundary Y ทั้งสอง; รูป X/circle ใช้ geometry ไม่ขึ้นกับ font baseline; tooltip เก็บ decimal price เดิม
- จำกัด 60 columns / 3000 newest recorded boxes; missing transitions แสดง unavailable พร้อมราคา column แทนการสร้างข้อมูลสมมติ
- S/R ยังคงวางที่ราคาจริง; mobile ใช้ horizontal scroll ที่ focus ด้วย keyboard ได้
- Checks (Windows, Node 24.19.0): `npm --prefix apps/web test`: PASS, 8 tests; `npm --prefix apps/web run lint`: PASS; `npm --prefix apps/web run typecheck`: PASS; `npm --prefix apps/web run build`: PASS; `git diff --check`: PASS.
- Browser QA: local scratch harness `node --experimental-strip-types browser-qa.mjs` renders the actual React component using `tests/render-chart.mjs` and real CSS in headless Microsoft Edge (Playwright 1.63). Synthetic 6 columns / 24 X/O boxes; bounding rectangles confirm each shape is strictly inside and centered in its cell at 1200px and 390px, CSS zoom 100/150/200%. Desktop/mobile screenshots visually inspected. No custom chart zoom control exists.
- Limitations: isolated synthetic component QA, not a live MT5 session. Existing dense-history compression remains; browser zoom/scroll supported. Node emits a module-type detection warning for TS tests; Next warns about an unrelated parent-directory lockfile. Neither blocks checks.
- Backend/core tests not run locally: no API/engine/persistence files changed; CI retains existing Python suite. No requirement, architecture, historical task, engine, Matrix or signal changes.
- Review: self-review; independent review pending. Task stays in_review until required review/merge evidence; no merge authorized.
- Decisions: render-only geometry; no engine/config/formula ADR or migration needed.
- Blockers: none for this UX scope.
- Next action: independent PR review.
