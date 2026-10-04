---
task: PR8
status: in_review
depends_on: ["ADR-035 PR-1 contracts merged", "ADR-035 section 12 #7 RESOLVED_BY_RIN (Option 1)"]
agents: []
skills: []
docs: ["docs/requirements.md", "docs/architecture.md", "docs/decisions/ADR-035-execution-integration-safety-amendment.md"]
translation_needed: false
---

# PR-8 — Pure result-driven lifecycle helper (ADR-035 s3.11)

base_commit: d4678992307727a6fe9cc63bb0e013e7ded9fefa
branch: claude/pr8-result-lifecycle-v1
worktree: D:\NEXORA\NEXORA-PR8-LIFECYCLE

## Scope

New pure module `packages/nexora/position/result_lifecycle.py` with
`apply_execution_result(position, request, result) -> PositionRecord`. No durable store
mutation, no I/O, no call site, no `position/__init__` export, no `ExitDecision` parameter.

Applied (position OPEN/MANAGING): CLOSE+FILLED -> CLOSED (via `mark_closed`); REDUCE+FILLED ->
quantity reduced, MANAGING. No-op (position returned unchanged): PARTIALLY_FILLED/ACCEPTED/UNKNOWN,
MODIFY_PROTECTION any status, zero-fill REJECTED (ADR-silent choice). Everything else raises
`PositionInputError` with a sanitized code.

## Open items left open

OPEN-7 (partial/unknown/accepted handling, protection updates) and OPEN-19 (idempotent re-application:
a second FILLED REDUCE reduces again) are NOT resolved.

## Execution record

Self-review; independent review pending. Tests: tests/test_position_result_lifecycle.py.
