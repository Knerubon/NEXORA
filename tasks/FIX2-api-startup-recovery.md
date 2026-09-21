# FIX2 API startup recovery

status: in_progress
translation_needed: false
base_commit: 81854be

User requests urgent restoration of local web/feed. Root AGENTS.md applies.
Inputs: docs/requirements.md, docs/architecture.md, docs/research-runtime.md;
packages/nexora/{storage,artifacts}.py, research/{runtime,pipeline}.py;
apps/api/nexora_api/{main,research,quotes}.py; tests/test_readiness_regressions.py,
tests/test_postgres_journal.py; skills/{testing,postgres}/SKILL.md.

Observed: API blocked in SQLiteJournal.read -> _verified -> canonical_hash before
runtime replay. Existing journal is 3.12 GB with 14,796 active-scope events.
Use bounded verified iteration during recovery, preserving all events, order,
identity/hash verification and engine/paper semantics. No reset, pruning, migration,
feed-parameter changes, orders, merge, tags or push. Work isolated from main.
Validate corruption rejection, iterator boundaries, restart equivalence and suite.
Self-review; independent review pending.
