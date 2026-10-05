# PROTOCOL-V2 — Wave-based orchestration and Rin merge authority

Status: in_review
Branch: claude/agents-protocol-v2-wave-merge-v1
Base: d1a4b1cc69cfcad597507404d895ef8c97934704
Type: docs-only governance change (role: ARCHITECT/INTEGRATOR drafting)

## Scope and inputs
Requirements: docs/requirements.md (Phase 1 research/paper boundary unchanged).
Architecture: AGENTS.md sections 0, 5, 10, 11, 12, 14, 16, 17 (workflow/governance only).
Input: Rin's "Master Orchestration Protocol V2 - Wave-based execution + Rin merge authority".
No implementation, ADR, task-cleanup, runtime, broker, AUTO, PROD or release/tag change.

## Changes
- AGENTS.md: new section 20 (MASTER responsibility, authority sources, 10 merge gates,
  escalation list, review model, merge attribution, hard boundaries, governance rule,
  Final Wave Report and RIN_DECISION_REQUIRED templates).
- AGENTS.md amended minimally: section 1 Architect/Integrator "Must not", section 10
  (merge prohibition split by role; "human instruction" -> Rin-authorized merge evidence),
  section 12 review-flow pointer, section 17 "human merge evidence" wording.
- Unchanged: section 0, section 9, section 11, section 14, section 16 handoff
  (workers still report MERGE: NOT PERFORMED).

## Governance note
This PR changes the authority model itself. It may be merged only on an explicit per-PR
`RIN_MERGE_AUTHORIZED PR #<n> <full 40-char SHA>`; no Wave authority covers it.

## Validation / handoff
- Application tests: not_run (docs-only).
- git diff --check: PASS.
- Self-review; independent review pending.
