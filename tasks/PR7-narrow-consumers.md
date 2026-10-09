---
id: PR7-narrow-consumers
status: in_review
owner: DEV (developer role; self-review; independent + Security review pending)
base_sha: bc1712f0c8b31f3f67ccf6ae59f190173ab566f3
branch: claude/pr7-narrow-consumers-v1
worktree: D:\NEXORA\NEXORA-PR7
---

# PR-7 (NARROW scope) - resolver port, adapter-mode gate, reconciler resolver, OPEN-8 residual, dedup accessor freeze

Requirements: [requirements](../docs/requirements.md). Architecture: [architecture](../docs/architecture.md).
Contract: [ADR-035](../docs/decisions/ADR-035-execution-integration-safety-amendment.md) s3.1 (steps 2/4), s3.5, s4.5, s4.7
(`broker_adapter` row), s5.1/s5.2 (binding/resolver), INV-18/19/20/25, OPEN-8, OPEN-9, s9 (PR-7).
Related: [PR4](PR4-execution-pipeline.md), [PR6](PR6-execution-preflight.md), [PR1b](PR1b-reconciliation-vocabulary.md).

**PRODUCTION REMAINS DENY-ONLY. No real transmission path exists after this PR.** Self-review; independent + Security review pending.

## Rin decisions used (NARROW scope, frozen by Rin for this task)
1. PR-1 `ExecutionInstrumentResolver` Protocol is the single resolver port (pipeline and reconciler); binding mode only; legacy denied.
2. Adapter Mode Gate = Option B (INV-25 accessor + exact-class allow-list + mode/capabilities captured once); Phase-1 vocabulary = SIMULATION only.
3. Reconciler takes the resolver as a REQUIRED explicit parameter; no identity default.
4. OPEN-8 residual REDUCE rule in Preflight (deny gate; `volume_max` not applied to the residual); ONE new reason code.
5. Dedup discovery accessor frozen as is; no behavior change.

## Delivered
- `execution/instrument_resolution.py` (new, pure): `resolve_binding_instrument_id(resolver, symbol)`; exact type/binding mode/execution_symbol equality; any problem (no resolver, raise, wrong type, wrong symbol, legacy, blank) => `ExecutionBindingMissingError` (`execution_binding_missing`); exception text never propagated. No resolver implementation ships (none is authorized in production).
- `pipeline.py`: provisional `Callable[[str], str | None]` removed; `instrument_resolver: ExecutionInstrumentResolver | None`. Step 2 resolves `intent.symbol`, and for position kinds `position.symbol` too; instrument ids must be equal (`position_symbol_mismatch` otherwise; unresolvable => `execution_binding_missing`).
- `broker_adapter.py`: read-only `adapter_mode` on the `BrokerExecutionAdapter` Protocol; `SimulatedBrokerAdapter.adapter_mode` returns `SIMULATION_MODE`; `PHASE1_EXECUTABLE_ADAPTER_MODES = {"simulation"}` (PAPER/DEMO/REAL are not members and not constructible); `is_shipped_simulated_adapter` (exact `type(...) is SimulatedBrokerAdapter`).
- `pipeline.py` wiring gate (`non_production_transmission_seam`): accepts the exact shipped simulator OR a marked test double outside `nexora.` (marker + namespace refusal RETAINED for doubles, test-only, AST-enforced as before); reads `adapter_mode` and `capabilities()` ONCE and captures them in the (frozen, token-guarded) seam; pipeline constructor re-verifies the captured values. Step 4 uses the captured capabilities; a different caller-supplied `capabilities` denies `preflight_capabilities_adapter_mismatch`; `None` uses the adapter's. Without an injected preflight the REAL deny-only `ExecutionPreflight` is used, so a wired simulator can never submit.
- `reconciler.py`: `symbol == instrument_id` assumption removed; `classify_reconciliation(..., instrument_resolver)` REQUIRED keyword (no default). Local symbol -> instrument id once per local position. An unresolvable local position never pairs and is never `BROKER_FLAT_CONFIRMED` (falls to `LOCAL_OPEN_BROKER_MISSING`, fail closed). PR-1b vocabulary and behavior (CLOSE_PENDING_CONFIRMED, BROKER_FLAT_CONFIRMED, complete/close_pending defaults) otherwise unchanged; unattributed broker positions stay broker-only.
- `preflight.py` + `autonomous/broker_capabilities.py`: optional keyword `position_quantity` on `evaluate` (additive); new `validate_residual_volume(capabilities, position_quantity, reduce_quantity)` (pure, total): residual `r = position - reduce` must satisfy `0 < reduce < position`, `r >= volume_min`, `(r - anchor) / step` integer (same exact `_on_step_grid` machinery; anchor per capabilities); `volume_max` applies to the requested ORDER quantity (validated by `validate_volume`), NOT to the residual. `r` is never materialised and there is NO numeric cap/threshold (see Hardening delta). Exactly ONE new reason `preflight_reduce_residual_volume_invalid` (also for a missing/invalid `position_quantity` on a REDUCE). It is an additional DENY gate: valid REDUCE still ends `preflight_policy_undecided`; OPEN still `preflight_policy_evaluation_unavailable`; no allow path.
- Dedup: NO code change. Frozen accessor `enumerate_unresolved() -> tuple[DedupKeyStatus, ...]` pinned by a Protocol-signature test; pipeline never references `establish_index_genesis` / `register_known_keys` (AST test).

## Wired vs deny-only
| Item | State after PR-7 |
|---|---|
| Resolver port (PR-1 Protocol) | consumed by pipeline + reconciler; NO implementation, none wired in production (pipeline `instrument_resolver=None` => deny) |
| Adapter-mode gate | wiring-time (test seam) only; SIMULATION only; simulator wirable ONLY through the test seam |
| Production composition (`transmission=None`) | DENY-ONLY: stops at step 4 (`preflight_*` / `transmission_not_wired`); no claim/attempt/submit |
| Seam + SimulatedBrokerAdapter + real `ExecutionPreflight` | constructs; still DENY-ONLY (preflight never allows) |
| Seam + test-injected allowing preflight | TEST-ONLY mechanics tests (unchanged) |
| Real adapter / MT5 / network / env or flag adapter switch | none exists |
| PAPER / DEMO / REAL | non-executable, not constructible |

## Exact production-deny proof
- `tests/test_execution_pr7_narrow_consumers.py::test_production_composition_denies_all_kinds_even_with_simulator_available` (OPEN/REDUCE/CLOSE/MODIFY; `submit` patched to fail; no store writes).
- `::test_wired_simulator_with_real_preflight_never_submits` (OPEN/REDUCE/CLOSE/MODIFY; simulator wired through the permitted gate, real preflight; `submit` never called, `simulated_fill_count == 0`, no claim/attempt/abort/result).
- `::test_real_preflight_never_allows_reduce_even_with_valid_residual`; `tests/test_execution_preflight.py::test_preflight_is_non_operational_deny_only`; existing `tests/test_execution_pipeline.py::test_production_composition_*`.

## Tests added / updated
- New `tests/test_execution_pr7_narrow_consumers.py` (resolver exact-case/blank/wrong-type/raising/legacy/forged; pipeline position-vs-intent; same instance in pipeline and reconciler; reconciler requires resolver, no identity, unresolvable local, no ownership inference; adapter gate: accessor on Protocol + simulator, closed vocabulary, forged/non-str/raising modes, exact-class allow-list, capabilities from wired adapter, TOCTOU; AST/import boundary; OPEN-8 table incl. residual above `volume_max`, equivalent Decimal representations, huge exponents, context independence, anchor, Fraction oracle; dedup signature + genesis AST).
- New `tests/execution_resolver_fixtures.py` (TEST-ONLY pure table resolver).
- Updated (call-site/signature only): `tests/test_execution_pipeline.py` (resolver object, doubles gain `adapter_mode` / `position_quantity`, simulator now accepted by the seam, AST import set), `tests/test_execution_pipeline_concurrency.py`, `tests/test_execution_reconciler.py`, `tests/test_execution_reconciliation_vocabulary.py`, `tests/test_execution_recovery.py` (explicit resolver; NOT an identity default), `tests/test_execution_preflight.py` (vocabulary test ten -> eleven, nothing else).

## Files touched
Production: `packages/nexora/execution/{pipeline,preflight,broker_adapter,reconciler}.py`, `packages/nexora/execution/instrument_resolution.py` (new), `packages/nexora/autonomous/broker_capabilities.py` (additive `validate_residual_volume`; `validate_volume` untouched).
Not touched: `guard.py`, `dedup_store.py`, `recovery.py`, `supervisor.py`, `result_lifecycle.py`, `apps/web`, ADRs. Call-site signature edits outside the list above: none.

## Hardening delta (Rin-authorized after independent + Security review APPROVE of 52702fa; no scope change)
1. Seam replacement: `dataclasses.replace(seam, adapter=Evil(), preflight=<allow>)` previously kept the factory token and the pipeline only type-checked the captured mode/capabilities (an unmarked "real" adapter ran in a test). Now (a) one shared `_gate_adapter` (exact class allow-list, SIMULATION-only mode, exact `BrokerCapabilities`) is re-run in the seam `__post_init__` AND in the pipeline constructor, and the captured `adapter_mode`/`capabilities` must equal what THAT adapter reports; (b) the token is no longer an init field (set by the factory with `object.__setattr__`), so `replace`/direct construction/subclass yield a seam the pipeline refuses, and deepcopy/pickle round trips get a different token object. `copy.copy` yields the same adapter only (frozen). No plugin/selection mechanism; PAPER/DEMO/REAL still absent.
2. Residual exact arithmetic: the 100,000-digit guard and the private-context sum are REMOVED. Sign checks use `_sign_of_terms` (exact sum of the top terms whose gaps are bounded by operand digit counts; a lone top term dominates terms 2+ orders lower) and the grid test uses `_terms_on_step_grid` (generalised #63 valuation argument: equal exponents merged, lowest term absorbs the next while gap <= bit_length, else v2/v5 of the lowest term and `pow(10, gap, m)`). Cost scales with operand sizes, not exponent gaps; no threshold, no float/rounding/cap/adjust; `volume_max` still does not constrain the residual.
3. Exact types: `validate_residual_volume` requires `type(x) is Decimal` for position, reduce, `volume_min`, `volume_step`, the anchor, and `type(capabilities) is BrokerCapabilities`; subclasses (incl. lying comparisons) deny with the existing single reason.
- Tests: `tests/test_execution_pr7_hardening.py` (19 of its 25 fail on 52702fa; all pass now): evil `replace`, captured-field `replace`, token, direct/subclass seam, copy/deepcopy/pickle, ctor second layer, vocabulary + production deny-only re-proof; huge-gap exactness (1E+999999 vs 1E+999998; gaps to 10^17), independent modular oracle (400 huge-gap cases), 30k-case seeded Fraction oracle (2^k/5^k/10^N shapes) + 6k near-cancellation cases, 0 mismatches; totality over NaN/sNaN/inf/huge/non-Decimal; hostile Decimal subclasses at every operand. Updated: `test_pipeline_refuses_a_raw_adapter_and_a_forged_seam`, `test_pipeline_constructor_rechecks_the_captured_mode` (replace now fails at once).
- Limits: Decimal exponents are bounded only by Python's `Decimal` itself (|exp| < ~1e18). The lying-resolver finding is NOT addressed here (production-resolver / OPEN-9 follow-up). Seam/mode remain adapter self-attestation.
- Files: `pipeline.py`, `broker_capabilities.py`, the two updated tests and the new hardening test, this record.

## Out of scope (not done)
Preflight ALLOW; BrokerStateQuery / snapshot producer; `ReconciliationEvidence` contract expansion (no `snapshot_complete`, no content-bound ref); dedup global-block-set changes; clearing/retry; `next_position` persistence; recovery authorization; position-level exclusivity; legacy execution; PAPER/DEMO/REAL/AUTO; real broker SDK/`order_send`/MT5; PROD; release/tag; Track E; ADR edits; `apps/web`.

## Documented decisions / limits
- A residual reason is used for a missing/invalid `position_quantity` on REDUCE (the single authorized new code).
- Adapter `mode` and the marker remain self-attestation, not protection against a malicious in-process adapter; the barrier is that no real adapter exists and preflight is deny-only.
- A pre-existing PR-1b nuance is unchanged: a broker position carrying a foreign explicit `nexora_position_ref` does not block `BROKER_FLAT_CONFIRMED` for a local position on the same instrument.
- ADR-035 text still says "PROPOSED"; this PR does not edit it.

## Review requirements
Independent review + Security review (adapter boundary / persistence-adjacent / identity mapping) + CI. Codex only on an execution-permission / identity-mapping / duplicate-submit / broker-boundary disagreement. Merge requires explicit human instruction.

## Handoff (AGENTS.md s16)
See the PR description and the worker report (ROLE DEV; WORKSTREAM PR-7 narrow consumers; BASE SHA bc1712f0c8b31f3f67ccf6ae59f190173ab566f3; MERGE NOT PERFORMED).
