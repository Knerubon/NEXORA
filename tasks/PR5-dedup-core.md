# PR5 - ADR-035 Dedup core (attempt / abort / W3 states / quarantine)

Status: in_review (self-review; independent security review pending)
Role: developer (W2B-1). Base: d4678992307727a6fe9cc63bb0e013e7ded9fefa. Branch: claude/pr5-dedup-core-v1.
Authority: docs/decisions/ADR-035-execution-integration-safety-amendment.md s3.6-3.10, s4.7, s7 (INV-07/08/11/21), W1-W5.

## Allowed files
- packages/nexora/execution/dedup_store.py
- tests/test_execution_dedup_store.py (extended only)
- tests/test_execution_dedup_attempt_abort.py (new)
- tasks/PR5-dedup-core.md

## Implemented (fail-closed subset only)
- `attempt#g` (record_attempt) and `abort#g` (record_abort), appended with `expected_count`; a loser or an
  unconfirmed append raises (caller must not submit). No timeout.
- W3 state accessor (`inspect`/`state`), explicit-generation `record_late_result`, `unresolved_among(keys)`.
- `_events` recognises only `attempt` and `abort` in addition to claim/result/release; everything else is malformed.
- Integrity violation => QUARANTINED (`DedupStoreCorruptError`); I/O failure => `DedupStoreIOError` (plain deny).

## Not implemented / blocked
- Release after abort (OPEN-14), `quarantine_resolved` / clearing (OPEN-3), `reconciled#g` / OPEN-2, any UNKNOWN release.
- Global unresolved-key enumeration: `Journal` Protocol has no stream-listing method and storage.py is not owned
  by this PR; needs a storage-layer capability (see PR body). Only the caller-supplied-set helper exists.
- PR-4 pipeline, PR-6 preflight, key derivation (OPEN-15).

## Execution record
See PR body.
