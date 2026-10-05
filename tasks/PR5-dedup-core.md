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

## Corrective delta (Rin-authorized, after Codex adversarial findings)
Frozen invariant: unsafe execution evidence is MONOTONIC within a generation. A generation that ever observed
UNKNOWN/ACCEPTED/PARTIALLY_FILLED/FILLED is RESULT_UNSAFE; a later REJECTED never makes it clean, releasable or
re-claimable (supersedes the inherited latest-result-wins). Release requires ALL results clean REJECTED.
A released generation with an unsafe result anywhere (before/after a clean one, before/after the release) is an
integrity violation => QUARANTINED. `unresolved_among`/`inspect.unknown_observed` treat ever-UNKNOWN in the current
generation as unresolved. Does not resolve OPEN-2; no clearing mechanism, no resolved marker added.
Interpretation to confirm: "unresolved" = ATTEMPTED_NO_RESULT or ever-UNKNOWN (FILLED/ACCEPTED/PARTIAL stay RESULT_UNSAFE but
are not "unresolved", per ADR s3.8).
Note: `record_result` has no expected_count, so a stale writer can still land a result after a concurrent release; that is
detected on the next read as a late unsafe result (QUARANTINED, fail closed).

## Recorded gates (not implemented here)
- PR-4 is the ENFORCEMENT OWNER for result-without-attempt: a result may only be recorded when the state is
  ATTEMPTED_NO_RESULT (never in CLAIMED_NOT_ATTEMPTED). PR-4 ACCEPTANCE GATE. Store keeps legacy compatibility here.
- `unresolved_among` is a TEMPORARY CALLER-SCOPED accessor, NOT global safety proof (unnamed keys invisible).
  Global enumeration = DEDUP-ENUM-1 (storage-layer capability, owned elsewhere; storage.py untouched).
- Exception hierarchy: target = sibling IO / integrity errors under DedupStoreError. NOT changed now (existing
  `test_closed_storage_fails_closed` and `except DedupStoreCorruptError` consumers depend on subclassing).
  CONTROLLED FOLLOW-UP GATE before PR-4 integration: sibling separation with all catches/tests migrated and
  independently reviewed. Until then consumers MUST test DedupStoreIOError before DedupStoreCorruptError, or use
  inspect()/violation_code.
- PR-4 contract-test gate: caller-supplied evidence refs (request_digest, reconciliation_evidence_ref,
  preflight_decision_ref) are not validated by the store for secrets/PII.
