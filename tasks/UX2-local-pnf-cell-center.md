---
task: UX2-local
status: in_review
depends_on: ["tasks/FIX2-mt5-feed-time.md"]
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/development.md"]
translation_needed: false
---
# UX2 local preview — center glyphs in the running renderer

ผู้ใช้แจ้ง localhost:3000 ยังทับเส้นหลัง PR #15 merge. ตรวจพบ process 19492 ใช้ checkout นี้บน codex/fix-mt5-feed-time พร้อม local uncommitted white/zoom chart ใน apps/web/app/page.tsx. PR #15 แก้ renderer อีกตัวบน main.
Scope: เฉพาะ visual center และ pattern origin ใน local page.tsx; ไม่แก้/commit งาน local อื่น, ไม่เปลี่ยน engine หรือ source prices.
Context: apps/web/app/page.tsx, apps/web/app/globals.css, package.json, requirements/architecture/development. สำรอง page.tsx ก่อนแก้ไว้ใน workspace ของ task ปัจจุบัน.
Validation: lint/type/build; ตรวจ DOM geometry และภาพของ running localhost รวม zoom/resize. Review: self-review; independent review pending. Local application ไม่ใช่ merge หลักฐานใหม่.

## Execution evidence
- Applied only glyph Y offset (-13 SVG units, half row) and grid pattern origin x=75/y=26; tooltip/price/engine values unchanged. Existing uncommitted layout/style/FIX2 changes preserved.
- `npm --prefix apps/web run lint`: PASS; `npm --prefix apps/web run typecheck`: PASS; `npm --prefix apps/web run build`: PASS; `git diff --check`: PASS.
- Restarted only verified web process on 127.0.0.1:3000; API/feed untouched.
- Running browser DOM: 294 glyphs at 100%, 296 at 120%, 300 at 80%/390px mobile; zero off-center glyphs against 30x26 grid (15px horizontal / 13px vertical midpoint). Counts vary with live feed. Actual live screenshots checked on desktop/mobile. Restored 100% and original viewport.
- User subsequently requested commit. Stage only the cell-centering changes against HEAD plus this task; preserve pre-existing zoom/layout/FIX2 edits unstaged. Checks above describe the running working tree including those local edits. No push requested. Independent review pending.
