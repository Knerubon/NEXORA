# EXC1 — Experience journal backward compatibility V1

Status: in_review
Role: developer (Experience journal compatibility owner)
Requirements: [requirements](../docs/requirements.md); Architecture: [architecture](../docs/architecture.md), [EX1](EX1-experience-engine-v1.md), [ADR-020](../docs/decisions/ADR-020-pnf-trendline-v1.md), [ADR-021](../docs/decisions/ADR-021-entry-readiness-v1.md), [ADR-022](../docs/decisions/ADR-022-startup-recovery-checkpoint-v1.md), proposed [ADR-028](../docs/decisions/ADR-028-experience-snapshot-additive-fields.md)

## Goal

Resolve PROD migration blocker B1. A journal written by 9016004 must recover under current code without rewriting history.

## Scope

- `packages/nexora/experience/engine.py`: additive context fields are frozen only when the recorded output carries them.
- Golden old-writer fixture, and compatibility tests C1–C12.
- No change to journal rows, identities, fingerprint, policy, API, UI or trading semantics.

## Execution record

- Base: `origin/main` 4f69e9c. Branch `claude/experience-journal-compat-v1`, worktree `D:\NEXORA\NEXORA-EXPERIENCE-COMPAT`.
- The golden fixture `tests/fixtures/experience_journal_9016004.json` was generated from synthetic events by genuine 9016004 code, in a scratch `git archive` checkout (see `tests/experience_compat_fixture.py`). No PROD data was read or copied.
- Before the fix: the B1 tests (C1, C2, C11, C12) failed with `journal_identity_conflict` or a missing constant. C3, C4, C7 and C9 already passed.
- After the fix: see the handoff for exact commands and results.
- Offline rollback matrix (scratch, not committed):
  - OLD→NEW (fixed): PASS
  - NEW→OLD (9016004): FAIL, `journal_identity_conflict`, as expected
  - pre-fix 4f69e9c recovering a journal written only by the fixed code: PASS, so new records are unchanged
- Self-review; independent review pending. No push, PR or merge.
