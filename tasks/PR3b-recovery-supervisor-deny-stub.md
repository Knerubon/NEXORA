---
id: PR3b-recovery-supervisor-deny-stub
status: in_review
owner: DEV (developer role; self-review; independent + Security review pending)
base_sha: 38933959283db329abc55b30ecfd97f9d66fbfae
branch: claude/recovery-supervisor-deny-stub-v1
---

# PR-3b - `recover_from_emergency` HARD-DENY stub in `position/supervisor.py`

Requirements: [requirements](../docs/requirements.md). Architecture: [architecture](../docs/architecture.md).
Contract: [ADR-035](../docs/decisions/ADR-035-execution-integration-safety-amendment.md) section 6, OPEN-20, OPEN-1,
INV-22/INV-30. Follows [PR3](PR3-emergency-recovery.md).

## Ownership freeze (Rin)
- `execution/recovery.py` = pure planning / verified-evidence derivation ONLY (unchanged by this PR).
- `position/supervisor.py` = the authorization-enabled recovery transition OWNER.
- Plan recovery != authorize recovery != apply recovery.

## What is hard-denied
`recover_from_emergency(position, evidence, *, operator_ref, recovered_at) -> NoReturn` ALWAYS raises
`RecoveryAuthorizationNotResolvedError` (a `PositionInputError`, code `recovery_authorization_not_resolved`, same code
as `recovery.py`). It never returns a `PositionRecord`; the ADR's `-> PositionRecord` is realised as `NoReturn`
(signature ambiguity: ADR parameter `evidence: EmergencyRecoveryEvidence` is typed `object` here so any plan/evidence
or garbage is denied identically and no field is ever read). All arguments are ignored: `operator_ref` is audit only,
`RecoveryPlan.authorized` / `derived_target` are non-authorizing, no flag/override/default/env/timeout/test backdoor,
no I/O, no clock, no broker/dedup import, no mutation. `TRADE_STATE_TRANSITIONS[EMERGENCY]` stays `frozenset()`.
Not exported from `nexora.position`.

## Enabling later (not in this PR)
Requires an explicit Rin decision resolving OPEN-20 (authorization model, Security-reviewed) and OPEN-1 (freshness
bound from approved policy), then a NEW PR replacing the stub, with Security review and updated INV-30 tests.

## Security-review recommendations (PR #69) left for the future enabling PR
- Validate `operator_ref` and `planned_at` (non-blank, bounded length, aware datetime) BEFORE any journal write.
- `derived_target` is non-authorizing: never treat it as permission to recover.

## Tests
`tests/test_position_recovery_deny.py` covers INV-30 names `test_recovery_denied_at_authorization_boundary_until_open20`,
`test_nonblank_operator_ref_alone_does_not_authorize_recovery`, `test_recovery_has_no_timeout_reset`, plus INV-22
`test_emergency_transition_table_unchanged`, hostile-plan, no-return, no-override-parameter and import-boundary tests.
Merge: NOT PERFORMED.
