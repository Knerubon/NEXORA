# PR-4 ExecutionPipeline (ADR-035 s3) - fully fail-closed, NO real transmission

Status: in_review (self-review; independent + Security review pending)
Role: DEV (developer) under MASTER/Rin. Branch: `claude/pr4-execution-pipeline-v1`. Base: `origin/main` 774afe2f2bc9c3aca385ac14e5bfeb3737d7b0b4.
Sources: ADR-035 (s3 order, s4, s5, s6, s7 INV list, s11 OPEN, s12), ADR-033/034, `tasks/PR4-readiness-prerequisites.md`,
`tasks/DEDUP-ENUM-1-durable-enumeration.md`, `tasks/PR5-dedup-core.md`, `tasks/PR6-execution-preflight.md`,
`tasks/PR8-pure-result-lifecycle.md`, `tasks/PR3-emergency-recovery.md`.

## Scope
New module `packages/nexora/execution/pipeline.py` (+ `tests/test_execution_pipeline.py`). No existing production file was modified
(guard/preflight/dedup_store/idempotency/models/reconciler/recovery/supervisor/result_lifecycle/storage/`__init__` untouched; the
pipeline is deliberately NOT exported). No `apps/web`, no PROD, no AUTO, no Track E, no PR-7.

## Hard boundaries
- `ExecutionPreflight` is deny-only: the production composition (no `transmission`) stops at step 4 and can never claim, write an
  attempt marker or reach `adapter.submit`. Even an `allowed` preflight decision denies (`transmission_not_wired`).
- No real adapter exists/is imported/constructed/wired; no default adapter, no env/flag/config enabling one, no MT5 import, no order call.
- `recover_from_emergency` / `plan_emergency_recovery` are never called; EMERGENCY positions are denied at resolution; `operator_ref` unused.
- No retry, reclaim or loop. `establish_index_genesis` / `register_known_keys` / `lookup` / `latest_result` / `unresolved_among` never used.

## Adapter seam decision (for Rin)
The constructor has NO adapter parameter. The only way to attach one is `transmission=non_production_transmission_seam(adapter, preflight=...)`:
- the adapter must satisfy the existing `BrokerExecutionAdapter` Protocol and carry the class attribute
  `NEXORA_NON_PRODUCTION_TEST_ADAPTER = True`, and its class must NOT live under the `nexora.` package (this refuses the shipped
  `SimulatedBrokerAdapter` and any production adapter class);
- the seam object carries a private token; the pipeline refuses a raw adapter or a forged seam (`PipelineWiringError`);
- the seam also supplies the preflight (needed because the real one is deny-only); production passes none;
- `InMemoryExecutionDedupStore` is accepted only with the seam (INV-26/OPEN-18);
- the seam is referenced only by `pipeline.py` and tests (AST-enforced test over `packages/`, `apps/`, `scripts/`).
This is adapter self-attestation (speed bump + audit signal), not protection against a malicious in-process caller; the real barrier is
that no adapter exists and preflight cannot allow. The INV-25 `adapter_mode` Protocol accessor is PR-7 territory and was NOT added
(`broker_adapter.py` untouched), so the marker + namespace check is the smallest safe substitute.

## Wired vs fail-closed-disabled
| Step | Status |
|---|---|
| 1 reconciliation gate | WIRED: OPEN-16 global gate (`enumerate_unresolved`, block on every returned status, deny on any `DedupStoreError`/other error/malformed result); own-key `inspect` (QUARANTINED/not UNCLAIMED/released generation => deny); caller-supplied evidence: `sha256:<64 lowercase hex>` ref, single-snapshot rule, injected freshness bound (absent => deny, OPEN-1), aggregate must be `SYNCHRONIZED` |
| 2 resolution | WIRED: injected `InstrumentResolver` (none/None => `execution_binding_missing`); OPEN-17 payloads explicit inputs (absent => deny); position OPEN/MANAGING, symbol/side, exactly one MATCH record with confirmed quantities; CLOSE qty = local = broker, never capped; REDUCE `0 < q < position`; MODIFY `protection_change` derived (TIGHTEN only, via the supervisor's own tightening rule; everything else denies, OPEN-10) |
| 2b authority | WIRED: `authorize_trade_intent` recomputed from resolved/derived facts; no `AuthorityDecision`/AI input exists |
| 3 guard | WIRED: existing `evaluate_execution_guard` (no duplication) |
| 4 preflight | WIRED, DENY-ONLY: production `ExecutionPreflight` never allows; no allow path implemented |
| 5 claim | WIRED but UNREACHABLE in production; `DUPLICATE` => returns stored state, never transmits |
| 6 last look + attempt marker | WIRED (seam only): re-check freshness (evidence + preflight, injected bounds) and re-run guard; failure => `record_abort`; attempt marker with canonical sha256 refs, then re-`inspect` must be `ATTEMPTED_NO_RESULT` before submit |
| 7 `adapter.submit` | LOCKED: reachable only via the test seam; exception => stays `ATTEMPTED_NO_RESULT`, no retry |
| 8 result persistence | WIRED (seam only): result recorded only if `inspect` says `ATTEMPTED_NO_RESULT` (result-without-attempt rejected, not recorded); inconsistent/legacy/non-result adapter output persisted as `UNKNOWN`; release only for clean zero-fill REJECTED, never resubmit |
| 9 lifecycle + post-execution reconciliation | PARTIAL: `apply_execution_result` once on the persisted result, result returned in `PipelineOutcome.next_position` (NOT persisted, OPEN-19); `RECOVERY_AUTHORIZATION_NOT_RESOLVED` code handled first and distinctly (`RECOVERY_DENIED`); any other `PositionInputError`/exception => `RECONCILIATION_REQUIRED`, no retry. Fresh-evidence classification NOT performed (no BrokerStateQuery port, OPEN-2/OPEN-6); the flag `post_execution_reconciliation_required` is set and the next intent's step 1 needs fresh evidence |

## OPEN items
Touched fail-closed (no policy invented): OPEN-1 (bounds injected, absent => deny), OPEN-2/OPEN-6 (no evidence port; caller supplies
evidence), OPEN-5 (no position-level exclusion), OPEN-8 (no residual REDUCE volume check; relies on deny-only preflight, MUST be added before any
preflight allow path), OPEN-9 (resolver injected; legacy mode cannot be expressed), OPEN-10, OPEN-14/15 (unfresh/released key denied; no same-key
retry), OPEN-16 (global gate), OPEN-17 (explicit inputs or deny), OPEN-18, OPEN-19, OPEN-20 (recovery never applied).
Untouched/unresolved: OPEN-3, OPEN-4, OPEN-7, OPEN-11, OPEN-12, OPEN-13.

## Notes for Rin / reviewers (not RIN_DECISION_REQUIRED unless stated)
1. `RecoveryAuthorizationNotResolvedError` (position/supervisor.py) does NOT exist on this base (PR-3 only added `execution/recovery.py` planning).
   The pipeline therefore branches on `PositionInputError.code == RECOVERY_AUTHORIZATION_NOT_RESOLVED` first, which also covers the future class
   if it subclasses `PositionInputError` with that code. Re-check when PR-3's supervisor function lands.
2. Provisional port shapes introduced locally (no frozen contract exists): `InstrumentResolver` (callable) and `ReconciliationEvidence`
   (records + `sha256:` ref + observed_at). Production has no implementation of either, so production denies at step 1/2. Architect to freeze
   shapes with OPEN-2/OPEN-9 before PR-7.
3. `PipelineOutcome.next_position` is returned, not persisted: there is no position store port and OPEN-19 is unresolved.
4. Step 8 releases a clean REJECTED key (ADR step 8 / 3.10 row 12). Step 1 then denies the released generation (OPEN-15), so no same-key retry is possible.
5. Step-6 limits are exactly the ADR residual window (single input snapshot; no live kill-switch probe).
6. `enumerate_unresolved()` is O(indexed keys) per run.

## Tests (new: `tests/test_execution_pipeline.py`, 91 tests)
Order enforcement with recording fakes; every deny path; production composition never reaches claim/attempt/submit for all four kinds;
OPEN-16 gate (every state, every error class, malformed, missing genesis); evidence-ref allow-list (valid and 19 invalid shapes, no echo);
duplicate claim; marker failure/unverified; last-look abort; submit raise; persist failure; result-without-attempt; inconsistent result => UNKNOWN;
clean REJECTED no resubmit; lifecycle stale vs recovery-denial distinct; AST import/token boundaries; seam refusals; AUTO lock; determinism;
inputs not mutated; no ambient clock.

## Review requirements
- Independent review: REQUIRED (none performed; self-review only).
- Security review: REQUIRED (persistence, paper boundary, evidence-ref handling, transmission seam).
- Codex: RECOMMENDED for steps 5-8 (claim -> attempt marker -> submit -> result ordering, release and the result-without-attempt gate). Honest
  assessment: the TRANSMISSION boundary is not materially changed (no reachable path, preflight deny-only, no adapter), but this is the first
  code that orchestrates the duplicate-execution/idempotency sequence, so a second independent look at that sequence is worthwhile before any
  allow path or adapter (PR-7) is considered.

## Handoff (AGENTS.md s16)
See the PR description and the worker report. MERGE: NOT PERFORMED.
