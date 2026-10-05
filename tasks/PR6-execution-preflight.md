---
id: PR6-execution-preflight
status: in_review
owner: DEV (developer role; self-review; independent review pending)
base_sha: d4678992307727a6fe9cc63bb0e013e7ded9fefa
branch: claude/pr6-execution-preflight-v1
---

# PR-6 - ExecutionPreflight + total validate_volume

Requirements: [requirements](../docs/requirements.md). Architecture: [architecture](../docs/architecture.md).
Contracts: [ADR-035](../docs/decisions/ADR-035-execution-integration-safety-amendment.md) s3.1/3.2 step 4, s3.5, s4.5, INV-17/18, OPEN-1/8/11, Rin M-1.

## Delivered
- `execution/preflight.py`: `PreflightDecision`, `ExecutionPreflight.evaluate(request, capabilities, market_refs, *, now, max_capabilities_age=None)`. Pure; no I/O, clock, adapter.
- `validate_volume` is total (exact Fraction step test; non-Decimal/non-capabilities/any failure => `volume_not_multiple_of_step`).
- Freshness bound is caller-injected (`max_capabilities_age`); none/invalid => deny.

## Reason codes - APPROVED V1 diagnostic vocabulary (Rin, 2026-10-05); this approval does NOT resolve any OPEN policy item (OPEN-1, OPEN-11 etc.)
Exactly ten `preflight_*` codes: `preflight_volume_validation_error`, `preflight_policy_undecided`, `preflight_clock_requires_timezone`,
`preflight_capabilities_missing`, `preflight_instrument_mismatch`, `preflight_quantity_missing`,
`preflight_capabilities_freshness_bound_missing`, `preflight_capabilities_stale`, `preflight_stops_freeze_check_unavailable`,
`preflight_policy_evaluation_unavailable`.

## ADR gaps (fail closed)
1. `market_refs` shape undefined => MODIFY_PROTECTION stops/freeze distance check unperformable => always denied.
2. No capability health source (BrokerCapabilities has no health field) => health not checked in preflight (guard/authority covers broker health, ADR-033 s11); needs a definition.
3. OPEN policy evaluation (ADR silent) => denied `preflight_policy_evaluation_unavailable`. Net effect: `evaluate` can never ALLOW yet.
4. PreflightDecision datetimes: ADR says aware; denies for naive `now`/missing capabilities cannot satisfy that, so `evaluated_at` is raw and `capabilities_observed_at` is Optional on deny; allow requires both aware.
5. Future-dated capabilities (observed_at > now) treated as stale.
6. (superseded by the corrective delta below) Technical safeguard in `validate_volume`: |adjusted exponent| > 10_000 fails the step check closed (avoids huge-int allocation); not a broker/trading limit.
7. CLOSE with absent quantity (allowed by ExecutionRequest) is denied `preflight_quantity_missing` per Rin instruction.

## Corrective delta (Rin-authorized, after Codex adversarial findings)
- Removed the 10,000 adjusted-exponent safeguard and the Fraction path. `validate_volume` now uses an exact integer algorithm on
  `Decimal.as_tuple()` (coefficient/exponent): divisibility of `q - anchor` by the step via `m | T` plus 2- and 5-adic valuations,
  `pow(10, gap, m)` for huge exponent gaps, no `10**gap` expansion, no context-dependent Decimal arithmetic. Correctness and
  cost argument are in the `_on_step_grid` docstring. No arbitrary numeric cap, timeout or iteration limit.
- Representation-independent (0.1 == 0.10 == 1E-1 == 10E-2; all zeros equal).
- Tests: `tests/test_execution_validate_volume_adversarial.py` (extreme precision/exponents, equivalent representations,
  context-precision independence incl. traps, malformed inputs, anchors, seeded 6000-case oracle agreement, structured oracle cases).
- Existing test changed: `test_validate_volume_absurd_exponent_fails_closed` (tests/test_execution_preflight.py) asserted the removed
  safeguard; replaced by `test_validate_volume_absurd_exponent_is_exact` (exact answer `None`). No other existing test changed.
- Preflight is documented and tested NON-OPERATIONAL / DENY-ONLY (`test_preflight_is_non_operational_deny_only`); a manually
  constructed `PreflightDecision(allowed=True, ...)` is not proof of authorization (docstring + test). No unforgeable-token design.
- The PreflightDecision deviation (raw `evaluated_at`, Optional `capabilities_observed_at` on deny, caller-injected
  `max_capabilities_age`) is approved ONLY for the current deny-only / incomplete Preflight V1, not as a general contract change.
- No new reason codes in this delta (set pinned by `test_preflight_reason_code_vocabulary_is_exactly_ten`).
- Status: self-review; independent delta review + Codex adversarial delta review pending.
