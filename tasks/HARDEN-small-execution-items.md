# HARDEN-small - small isolated execution hardening items

- Status: in_review (self-review; independent review pending)
- Branch: claude/exec-small-hardening-v1; base 41c03866807c006643007357d804d0060398f67a
- Authority: ADR-035 s4.1 (new_position_ref), s4.5 (validate_volume), PR5 task note on record_result; AGENTS.md s9/s14
- Scope: `execution/models.py`, `autonomous/broker_capabilities.py`, `tests/test_execution_small_hardening.py`. dedup_store/idempotency/guard/preflight/reconciliation untouched.

## Item 1 - new_position_ref text type: DONE (partial, by design)
ADR-035 s4.1 types the field `str | None`; `models.py` accepted `bytes`/`bytearray` because they also have `.strip()`.
`ExecutionRequest.__post_init__` now rejects any non-`str` with `ExecutionContractError("invalid_new_position_ref_type")`
(the guard already denied bytes as `execution_request_invalid` before construction and is unchanged).
Positive (`pos:<proposal_id>`, None) and blank-rejection cases are tested.

FINDING FOR RIN (DEFERRED): canonical-form validation (control characters, `pos:` prefix, max length, charset) is NOT
implemented. ADR-035 s4.1 calls the format "a PR-1 detail" and `derive_new_position_ref` is marked "FORMAT PENDING ARCHITECT
APPROVAL". Rejecting non-canonical text would invent specification. Needs a decision: freeze the format (and whether the
type layer or the guard enforces it) before any text-level validation is added.

## Item 2 - record_result without expected_count: EVALUATION ONLY (no store change)
Scenario (reproduced as characterization test `test_characterization_record_result_late_writer_quarantines_on_next_read`):
writer A calls `record_result`, reads (generation 0, claimed, only a clean REJECTED); before its append, another store
instance on the same journal runs `release_for_retry`; A then appends an UNSAFE result (FILLED) to the already released
generation. The append is accepted (no `expected_count`, `_lock` is process-local only), and the next read raises/reports
QUARANTINED (`dedup_released_generation_not_safe`). The key is never re-claimable.
Assessment: the failure mode is fail-closed and the unsafe evidence is preserved, which is the PR5 frozen behaviour
(monotonic unsafe evidence). The cost is a quarantined key requiring manual resolution (no clearing mechanism, OPEN-2)
instead of an error at write time. Single-process use is protected by the lock; the window exists only for multiple
store instances/processes on one journal.
Recommendation (for Rin / dedup_store owner): optional - pass `expected_count=len(events)` in `record_result` so the
loser gets `_ConcurrentWrite` and can retry through `record_late_result` (which already preserves the evidence with an
explicit generation). Trade-off: extra retry path for ordinary concurrent result writers. Not implemented here
(file owned by another track; semantics decision).

## Item 3 - volume-grid oracle regression: DONE
The existing seeded oracle (`test_execution_validate_volume_adversarial.py`, 6000 cases through `validate_volume`) is kept.
Added `test_on_step_grid_seeded_exact_oracle`: seed 20261006, 6000 cases directly on `_on_step_grid` against an
independent exact `Fraction` oracle; shapes: zero, 2^k, 5^k, 10^N, 2^a5^b*m, random; exponents -25..25; negative
coefficients; half the cases are step-compatible multiples, a quarter of those re-expressed with equivalent Decimal
representations. Runtime ~0.2 s, 0 disagreements required.

## Item 4 - zero-input helper hardening: DONE
`_divisible_by_prime_power(0, p, need)` now returns True (0 is divisible by every power; previously False).
`_strip_prime(0, p)` returns `(0, 0)` instead of looping forever (no finite valuation; convention documented).
`validate_volume` never reaches either with zero (`cs != 0` is checked first), so results are unchanged; existing
adversarial/oracle tests pass.
