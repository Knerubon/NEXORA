# PR2 - ADR-035 Execution Guard G4-G7

Status: in_review (self-review; independent review pending)
Role: developer (W2). Base: c5f00f243dfdf685456509c67d5894242c34b122. Branch: claude/pr2-guard-g4-g7-v1.
Authority: docs/decisions/ADR-035-execution-integration-safety-amendment.md s3.3, s3.4, s7, Rin D3/D4.

## Allowed files
- packages/nexora/execution/guard.py
- tests/test_execution_guard.py
- tasks/PR2-adr035-guard-g4-g7.md

## Rules implemented
G4 (REDUCE, CLOSE), G5 (MODIFY_PROTECTION): reconciliation != SYNCHRONIZED => `reconciliation_blocks_position_action:<status>`.
G6: MODIFY_PROTECTION `protection_change` not exactly "TIGHTEN"/"WIDEN" => `protection_change_unclassified`.
G7: WIDEN + kill switch => `kill_switch_blocks_protection_widen`. G1/G2/G3/G8 unchanged.

## Not implemented / blocked
`new_position_ref` passthrough (needs ExecutionRequest.new_position_ref from PR-1), pipeline, resolver,
classify_protection_change, step 2b, preflight.

## Execution record
See PR body. Supersedes F2 (docstring + test migrated).
