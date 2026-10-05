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
`RIN_MERGE_AUTHORIZED PR #<n> <full 40-char SHA>` naming the exact reviewed full SHA;
`RIN_WAVE_MERGE_AUTHORIZED — <Wave>` never covers it. Wave authority is scoped to the named
Wave, ends on Rin's acceptance of the Final Wave Report or revocation/supersession, and never
implies AUTO/broker/paper-demo/PROD/release/tag/go-live unlock or Security boundary changes (AGENTS.md 20.2, 20.7).

## Validation / handoff
- Application tests: not_run (docs-only).
- git diff --check: PASS.
- Self-review; independent review pending.

Base staleness checked against origin/main 41c03866807c006643007357d804d0060398f67a (docs-only; three-dot diff is exactly the 2 files).
