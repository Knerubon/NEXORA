# EXC1 — Experience journal backward compatibility V1

Status: in_review
Role: developer (Experience journal compatibility owner)
Requirements: [requirements](../docs/requirements.md); Architecture: [architecture](../docs/architecture.md), [EX1](EX1-experience-engine-v1.md), [ADR-020](../docs/decisions/ADR-020-pnf-trendline-v1.md), [ADR-021](../docs/decisions/ADR-021-entry-readiness-v1.md), [ADR-022](../docs/decisions/ADR-022-startup-recovery-checkpoint-v1.md), accepted [ADR-028](../docs/decisions/ADR-028-experience-snapshot-additive-fields.md)

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

### Resume and sync onto `f2d51ad` (2026-09-25)

- **Found state:** HEAD `4f69e9c`, no commits. The six files were intent-to-add (`git add -N`), not staged: the index held placeholders and the content was unstaged. Before any change, the exact patch and file copies were preserved outside the repository.
- **Committed unchanged at `4f69e9c`, then rebased onto `origin/main` `f2d51ad`:** `8d7957e` (fix + fixture + tests) and this docs commit.
  - The patch is identical before and after the rebase, apart from `index` lines.
  - File content equals the preserved copies after CRLF normalization (`core.autocrlf=true`).
  - There were no conflicts.
- **Main since `4f69e9c`** (M30, VALID-1, PERF-1) changed nothing in `experience/`, `artifacts.py`, `storage.py`, `checkpoint.py` or `checkpoint_state.py`. This branch touches none of M30, VALID-1, research/runtime, apps or scripts.
- **History check:** `trendline` entered `freeze()` and the pipeline output in the same commit (`f832dcb`, ADR-020). So did `entry_readiness` (`988c542`, ADR-021). No writer ever froze an explicit `null` for a field its recorded output lacked, so the presence rule reproduces every historical writer.
- **Negative control:** against main's unfixed `engine.py`, 6 of 15 compatibility tests fail (B1 recovery, re-freeze, presence, future-field and mixed-journal cases). With the fix, all 15 pass.
- **Validation** (absolute `PYTHONPATH` to this worktree):

  | Suite | Result |
  |---|---|
  | EXC1 compatibility | 15 passed |
  | Experience | 62 passed, 1 skipped (PostgreSQL DSN) |
  | Recovery / checkpoint | 92 passed |
  | VALID-1 | 56 passed |
  | M30 | 96 passed |
  | Full suite | 570 passed, 3 skipped |
  | `ruff check .` | clean |
  | `ruff format --check`, changed files | clean |
  | `mypy` (strict), changed files | clean |
  | `mypy`, whole repo | 3 errors that predate this work: missing `psutil` stubs |
  | `git diff --check` | clean |

- **Operational note:** the `engine.py` change alters `code_fingerprint()`, so every existing checkpoint is rejected once and recovery falls back to a full replay (ADR-022 by design). Mixed old/new journals converge exactly between cold replay and checkpoint + delta (C12).

### Governance closure (2026-09-25 19:51 +07:00)

This is the actual sequence; nothing is backdated:

1. The implementation (`engine.py` change, golden fixture, C1–C12 tests) existed before any formal architecture acceptance. It was first held as intent-to-add changes at `4f69e9c`.
2. It was inspected against ADR-028 on resume, including the history check that each field entered `freeze()` and the pipeline output in the same commit.
3. **Negative control:** without the fix (main's `engine.py`), 6 of 15 compatibility tests fail.
4. **With the fix:** 15 of 15 pass, and the suites recorded above pass at `ab97e62`.
5. Rin then reviewed the completion handoff (`ab97e62` on `f2d51ad`) and **accepted ADR-028**, including its deployment consequences: no reverse compatibility with `9016004` (rollback needs the preserved pre-cutover store), and one intentional full replay after deployment because of the checkpoint fingerprint change, which must be scheduled.
6. The acceptance is prospective, from 2026-09-25 19:51 +07:00.

The governance-closure commit changes only ADR-028 and this task. Production code and tests are unchanged. Status moves from `in_review` to PR review; merge requires explicit human approval. **PERF-2 C1/C2/C3 must not start until the EXC1 PR is merged.**
