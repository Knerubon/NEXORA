---
id: PR3-emergency-recovery
status: in_review
owner: DEV (developer role; self-review; independent + Security review pending)
base_sha: 999e583c32b6620e60babcde945d1a0994783257
branch: claude/pr3-emergency-recovery-v1
---

# PR-3 - ADR-035 EMERGENCY recovery mechanics and contracts (authorization DISABLED / FAIL-CLOSED)

Requirements: [requirements](../docs/requirements.md). Architecture: [architecture](../docs/architecture.md).
Contract: [ADR-035](../docs/decisions/ADR-035-execution-integration-safety-amendment.md) section 6, OPEN-20, OPEN-1,
OPEN-4, R1/R2, M-2, INV-22/INV-30. Builds on [PR1b](PR1b-reconciliation-vocabulary.md) and
[PR8](PR8-pure-result-lifecycle.md).

## Scope
New pure module `packages/nexora/execution/recovery.py` (+ `tests/test_execution_recovery.py`). Planning only.
Not touched: position package, `TRADE_STATE_TRANSITIONS`, `reconciler.py`, `reconciliation.py`, guard, preflight,
dedup_store, idempotency, broker_capabilities, execution `models.py`, Pipeline, exports in `execution/__init__.py`.

## Delivered
- `plan_emergency_recovery(position, evidence, *, operator_ref, planned_at) -> RecoveryPlan`: pure; never mutates;
  never returns a `PositionRecord`; the target comes only from PR-1b `derive_recovery_target` (not duplicated).
- Contracts: `EmergencyRecoveryEvidence`, `RecoveryPlan`, `EmergencyRecoveryAudit` (audit-record SHAPE of a plan, not
  a recovery record), `RecoveryPlanOutcome {REFUSED, DENIED}` (no APPLIED/AUTHORIZED member),
  `RecoveryRefusalReason`.
- Outcomes: derived target -> `DENIED` with reason `recovery_authorization_not_resolved`; otherwise `REFUSED`
  (`position_not_emergency`, `evidence_position_mismatch`, `evidence_stale_vs_position`, `evidence_not_verified`),
  `derived_target=None`, position unchanged (stays EMERGENCY).
- Stale guard (fail closed, additive): records must share `evidence.observed_at` and any record quantity for the
  position must equal the position quantity.

## What is DISABLED
Authorization. `RecoveryPlan.authorized` / `EmergencyRecoveryAudit.authorized` are constant-`False` properties (not
fields, not constructible, not settable). No apply/enable function exists, no `recover_from_emergency`, no flag,
parameter, env var, default or test backdoor. `operator_ref` is recorded for audit only (blank/any value never changes
the outcome). `planned_at` is a caller-supplied audit timestamp; no clock read, no timeout/age logic.
OPEN-20 is untouched and remains OPEN; no authorization mechanism invented.

## Decisions used / for Rin (V1 diagnostics, not frozen vocabulary)
- Placement: `execution/recovery.py` (ADR places guarded function in `position/supervisor.py` per section 8 table, but
  that would put a `PositionRecord`-returning path next to authorization; Rin: confirm placement for the later,
  authorization-enabled apply step).
- Reason code `recovery_authorization_not_resolved` and the four refusal codes are new V1 diagnostics (ADR has no list).
- ADR function name `recover_from_emergency(...) -> PositionRecord` deliberately NOT implemented (would be a recovered-
  record path while OPEN-20 is unresolved). INV-30 test names in ADR (`test_recovery_denied_at_authorization_boundary_
  until_open20` etc.) are covered under different names; Rin to say whether names must match.
- Blank `operator_ref`: ADR rule 4 says non-blank is required; per Rin brief it never changes the outcome here. The
  non-blank requirement belongs to the future authorization step.
- OPEN-1 freshness: no bound mechanism implemented (no approved source/policy shape); plan lists `OPEN-1` and
  `OPEN-20` in `unresolved_gates`. Evidence age is never used.
- `EmergencyRecoveryRecord` (immutable journal record on actual recovery) is not defined: nothing is recovered.
- OPEN-4 stricter reading, complete-snapshot requirement and single-confirming-finding rule are inherited from
  `derive_recovery_target`.

## Review requirements
Independent review pending. Security review REQUIRED (authorization-boundary code, AGENTS.md section 12 #5). Reviewer
should confirm no bypass of OPEN-20 exists. Operational enablement stays blocked on OPEN-20 (R1/R2) and OPEN-1.

## Execution record / handoff
Tests: `tests/test_execution_recovery.py` (59), all `tests/test_execution_*.py` + autonomous contracts/core phase1
(641), `-k position` (170), `-k "pattern and (isol or import)"` (2), ruff check/format, repo-wide `python -m mypy`
(clean). Merge: NOT PERFORMED.
