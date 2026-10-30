# Executable P&F Baseline V1 — specification preparation

Date: 2026-10-30 (Asia/Bangkok). Issue #77; follow-up to draft PR #104.

**Preparation complete; executable baseline BLOCKED / DRAFT, NOT FROZEN.** Source evidence is pinned; missing strategy/data decisions are explicit. No strategy implementation, historical payload fetch, backtest, optimization or profitability claim.

| Deliverable | Review purpose |
|---|---|
| [A — Actual Strategy Decision Table](A-ACTUAL-STRATEGY.md) | Implemented rules, configuration behavior and execution boundaries, with pinned source lines |
| [B — Baseline Candidate](B-BASELINE-DRAFT.md) | Source/lock identity, complete configuration inventory, accepted decisions and freeze blockers |
| [C — Owner Decision Sheet](C-OWNER-DECISIONS.md) | Six grouped decisions with options/recommendations; all PROPOSED |
| [D — Fidelity and Implementation Plan](D-FIDELITY-AND-IMPLEMENTATION.md) | PR #104 gap verification, smallest change scopes and ordered harness/test plan |
| [E — Development Dataset Readiness Plan](E-DEVELOPMENT-DATA.md) | Existing acquisition leads, exact missing bounds/protected registry, safe next data steps |
| [Source/config inventory](SOURCE-AND-CONFIG-INVENTORY.json) | 125 core source hashes, four dependency-file hashes, 78 constructor fields/default expressions; approved values null |
| [Workstreams](WORKSTREAMS.json) | Exact full-SHA metadata snapshot; selected-source versus metadata-only inspection distinguished |

This directory supplements existing #79/#81/#82–87/#104 artifacts, without rewriting their reports, registers or owned paths. It consolidates the missing executable decision surface. Existing ADR-037 separate-contract direction is retained; formal acceptance remains pending. No governance or ADR status changed.

## Validation and self-review

Checks run in this isolated checkout:

- `python docs/research/edge-validation/executable-baseline-v1/evidence/verify_documents.py`: PASS — pinned Git source paths and line bounds, local links, source/dependency hashes and combined lock fingerprint, constructor source lines/null approval fields, existing remote-branch owned-path collisions. Full result recorded below.
- `git diff --check` and staged equivalent: PASS. New documentation paths only; existing strategy/source/locks/ADRs untouched.
- `git fetch origin main` / `git rev-parse origin/main`: main remains `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`.
- GitHub PR #104 recheck: HEAD `1d6b47ea213b5e681647b60d9e78fa5fdf45fa99`, open, draft, unmerged. Discussion API returned no comments. Issue #77 and its pilot addendum read.
- GitHub PR #103 API HEAD remains `e60dbd53b8c4d7d214727f46947ded1be1604bf8`, while description claims `70c1b49d5206f83954b6762f5ca76559cad9e41e`; discrepancy recorded, no claim of review at the description's SHA.
- Manual source/contract self-review: signal weights/precedence, geometry vs fills, exact Readiness table, optional PatternEngine boundary, Risk units/replay scaffolding, retained ADR-014/030 contracts and unknown costs. No independent reviewer or approval claimed.
- Application `pytest`, Ruff/mypy, recovery drill and web checks: **not_run** — documents plus a documentation verifier only; no application/runtime/dependency changes. No current application/CI pass inferred from older PR reports. No historical or synthetic trading simulation run in this task.

The verifier checks existence/line bounds, not semantic truth; source statements were also read manually. It requires the referenced Git objects (clone/fetch branches), operates on source/docs only and does not decode datasets. It does not certify data quality, causality or profitability.

## Execution record / handoff (AGENTS section 16)

```text
ROLE: specification compiler / Architect analysis; QA self-review
OWNER SESSION: Codex cloud 5e7f30c1a6ab
WORKSTREAM: executable-baseline specification preparation for Issue #77
WORKTREE: /workspace/scratch/5e7f30c1a6ab/nexora
BRANCH: codex/pnf-executable-spec-v1
BASE SHA: 36c90c59e2aa4f0d221c9c468a8379c5f57cbb87
STATUS: READY FOR REVIEW (documents); BLOCKED (executable freeze/data fetch)
COMMITS: publication commit recorded in PR metadata; all changes confined to this directory
CHANGED FILES: A/B/C/D/E Markdown, README.md, SOURCE-AND-CONFIG-INVENTORY.json,
               WORKSTREAMS.json, evidence/verify_documents.py
ACCEPTANCE: all unblocked source/spec preparation complete; no executable baseline asserted
DEPENDENCIES: actual config/cutoff; Owner D1-D6; formal contract acceptance;
              custody/quality/cost/protected-range records and independent review
BLOCKERS: approved config, scheduler/exits/account/cost semantics; development UTC
          bounds and complete protected registry not established
ARCHITECTURE: existing accepted contracts retained, no formulas/defaults selected or amended
RUNTIME / DATA IMPACT: none; source/docs cloned, no historical payload opened/fetched
PR: dedicated draft; link in final report / GitHub metadata
MERGE: NOT PERFORMED
NEXT: resolve D1-D6 with signed explicit values, then implement approved separate
      historical harness and synthetic mechanics tests; fetch only bounded development
      data after instrument/source/window/protected ranges and quality preflight established
```

NO HOLDOUT ACCESS/SUMMARIES; NO MT5; NO ORDERS; NO PAPER/DEMO/REAL/AUTO UNLOCK;
NO PROD/MERGE/RELEASE/TAG; existing owner branches untouched. G1/G2/G3 are not advanced.
