# PR2b - ExecutionGuard new_position_ref enforcement and passthrough

- Status: in_review (self-review; independent review pending)
- Authority: ADR-035 s3.3/s3.4, s4.1 (new_position_ref table), s7 INV-17; AGENTS.md s9/s14
- Branch: claude/pr2-new-position-ref-v1 ; Base SHA: d4678992307727a6fe9cc63bb0e013e7ded9fefa
- Owner: developer (W2B-2)

## Scope
- `evaluate_execution_guard(..., new_position_ref: str | None = None)` passes the value to `build_execution_request`.
- OPEN with `new_position_ref is None` -> deny `new_position_ref_missing` (guard is the single enforcement point; not duplicated in Preflight/Adapter).
- Reason order for OPEN: mode, authority, G3 reconciliation, `kill_switch_armed`, `new_position_ref_missing`.
- Blank / wrongly typed value -> `execution_request_invalid` (type layer; guard also rejects non-str fail-closed).
- REDUCE/CLOSE/MODIFY_PROTECTION: no new policy; a supplied value is rejected by the type layer.
- Type-level contract (models.py) unchanged: field stays optional for migration.

## Excluded
G1-G8 semantics unchanged; no protection_change/OPEN-10 resolution; no models/idempotency/ADR edits.

## Execution record
Tests: tests/test_execution_guard.py, tests/test_execution_*.py, autonomous contract/core suites pass (see PR body).
