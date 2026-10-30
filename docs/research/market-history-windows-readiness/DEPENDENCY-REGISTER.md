# Cross-track dependency register

Owned by the PR #95 remediation orchestrator. Recorded on 2026-10-22 against the SHAs below. A
dependency is never marked RESOLVED because its CI is green; it needs the named evidence.

Extractor stack: #89 -> #91 -> #92 -> #93 -> #96. The pinned #96 candidate head is `d2e8ee9ef4f6eb438dc79c5e04ad43ed46de764d`
(single source: [dependency-manifest.json](dependency-manifest.json)). **The candidate head is not a gate-approved baseline**; `gate_approved_baseline_sha` is `null`. None of these is merged. This package reads them
with `git show` only and never modifies them.

## Dependencies

| ID | Owner | Exact referenced SHA | Required contract | Verification status | Blocking impact | Evidence | Resolution / next action |
|---|---|---|---|---|---|---|---|
| DEP-01 Custody interface | PR #96 (Track A of V4) | the pinned candidate | TRUSTED requires a verifiable, un-revoked custodian approval in an append-only store, checked by `verify_market_history.py --store --registry --pin --max-pin-age-seconds --require-trusted` on a proven platform | **BLOCKED.** (1) `fsutil.PROVEN_PLATFORMS = {"linux"}`, so a dataset extracted or verified on Windows cannot be TRUSTED. (2) PR #96 Reviewer A round 2 found 4 MAJOR (F1 forgeable approvals, F2 revocation replay, F3 verify/trust TOCTOU, N1 operator self-approval by declared name), G1 NO-GO. The pinned #99 candidate **claims** them fixed; that claim is **not independently verified** (no Reviewer A verdict on `d2e8ee9`), so DEP-01 stays BLOCKED. | Checklist D4 and matrix case 7 steps 3-4 cannot pass. `custody_gate.V4_BASELINE_APPROVED = False` and `V4_PRINCIPAL_AUTHENTICATION_AVAILABLE = False`. Machine-enforced since round 4 (N2): `evidence_validator.dependency_status()` derives DEP-01 BLOCKED from those flags, so a REAL record whose evidence is otherwise consistent derives `BLOCKED`, and one that claims `PASS` is rejected. | [V4-CUSTODY-COMPATIBILITY.md](V4-CUSTODY-COMPATIBILITY.md); `test_v4_custody_compat.py`; PR #96 Reviewer A report | An independent Reviewer A review of the pinned candidate confirms the claimed F1-F3 and N1 fixes (authenticated operator and custodian identities) and approves custody. An owner decision is needed on how a Windows-extracted dataset can ever become TRUSTED (prove the Windows platform, or verify on Linux from a transferred copy). Then re-point this package and re-review. |
| DEP-02 Writer lock | PR #96 | the pinned candidate (`lock.py`) | OS advisory lock on a held fd, never reaped, `writer_lock_held` fail closed; Windows `msvcrt.locking` path | **PARTIAL.** Matches V4 semantics in this package (no reaping, no manual removal while a run may be active). Linux ext4 tested by #96 and Reviewer A. Windows path UNVERIFIED. Reviewer A F6 (external unlink admits a second writer) is claimed fixed at the pinned candidate, not independently verified. | Case 6 lock evidence can be recorded but cannot claim Windows exclusivity (`windows_msvcrt_lock_exclusivity` stays in `not_verified`). | UNSUPPORTED-GUARANTEES.md "File locking"; checklist C5/C6 | Windows lock exclusivity needs an authorized real-half run. F6 hardening (lock the directory fd) is owned by #96. |
| DEP-03 Extractor execution interface | Extractor stack (#93/#96 line), separate PR | the pinned candidate (`scripts/extract_market_history.py`) | A reviewed, authorized entry point for the real half: source/module injection or a mock source, a fault hook, `dtype.descr` exposure, vector replay, and argument recording around the real module | **BLOCKED.** The CLI constructs `Mt5ReadOnlyHistorySource()` itself and offers none of these (`execution_contract.EXTRACTOR_CAPABILITIES = frozenset()`). | Eight real-half steps are BLOCKED (checklist register). Cases 1, 2, 4, 5, 6 and 7 cannot reach PASS. Real-half readiness is not declared, and since round 4 the validator derives DEP-03 BLOCKED from `REAL_HALF_STEPS`, so no record can derive `PASS`. The offline planning step (checklist B4) has a reviewed CLI and is not blocked. | `execution_contract.py` `REAL_HALF_STEPS`; `test_execution_contract.py` | Add reviewed injection points or a driver in the extractor stack (a new, separately reviewed PR), then flip the steps here. Any real Windows execution needs separate Rin authorization. |
| DEP-04 Duplicate detection | PR #96 (extractor `quality.py`) | the pinned candidate | Exact duplicates are found wherever they occur; a flags-only difference is not an exact duplicate (ORACLE-SPEC.md) | **RESOLVED at the candidate (EXT-DEF-1), differential evidence only.** A run of the real `QualityAccumulator` against `duplicate_oracle.classify` on golden vectors D1-D9 gave equal exact-duplicate, same-timestamp and flags counts. Not a statement about other heads. The ordering metric differs by definition (see below). | None for duplicate counts at the candidate. Case 5 still compares counts to the oracle at the SHA that runs. | `test_v9_pr96_live_export.py::test_duplicate_and_ordering_counts_against_the_real_quality_code` (skips explicitly if the commit is absent); `test_duplicate_oracle.py` | Re-run at any re-pin. |
| DEP-05 Dataset quality policy | PR #94 (MD-6) | PR #94 HEAD `dd3504e...` as re-reviewed 2026-10-22 | Evidence requirements here must not assume thresholds MD-6 has not approved (gap limits, spread classes, duplicate tolerances) | **OPEN.** PR #94 re-review: REQUEST CHANGES (new MAJOR N1), G3 NO-GO. This package invents no threshold: extreme spreads are unclassified (Reviewer B I2), `--max-gap-ms` is the operator's declared value, and duplicate counts are reports, not pass/fail limits. | None on the offline package. A real-half record cannot claim dataset acceptability. | ORACLE-SPEC.md section 1; `classify_quote` ledger row | Re-check after MD-6 is approved. |

## PR #96 cross-track compatibility (X1-X6, round 4)

Source: PR #95 at the round 4 HEAD (this branch). Target: the pinned PR #96 candidate (`dependency-manifest.json`)
(the #99 head `d2e8ee9`). The findings cited below are from Reviewer A round 2 on #96
(`pr96-reviewer-a-round2-report.md`); the candidate claims them fixed, and that claim is **not
independently verified** until Reviewer A reviews `d2e8ee9`. If the pinned HEAD moves, every
row below is **STALE** until a compatibility re-review; nothing here becomes permissive because #96
changed.

| ID | Interface contract (PR #95 side) | Test evidence | Current blocker | Owner | Required next action |
|---|---|---|---|---|---|
| X1 G1 baseline approval | `custody_gate.V4_BASELINE_APPROVED = False` and `V4_PRINCIPAL_AUTHENTICATION_AVAILABLE = False`; `evidence_validator.dependency_status()` derives DEP-01 BLOCKED from them | `test_r4_custody_gate.py::test_b12_*`, `test_v4_custody_compat.py::test_quoted_v4_interface_constants`, `test_r4_evidence_validator.py` (A4) | PR #96 Reviewer A: REQUEST CHANGES, G1 NO-GO | PR #96 owner, then an independent Reviewer A | New Reviewer A verdict approving custody; then a separate PR #95 change to flip the flags, re-reviewed |
| X2 Revocation lineage | A revocation after the first approval is final unless a later approval acknowledges it (`acknowledged_revocations`); replay of a revoked approval, a reused candidate without lineage and an approval id seen twice are refused | `test_r4_custody_gate.py::test_b9_revoked_approval_replay` | PR #96 F2 (byte-identical replay restores TRUSTED): claimed fixed at the candidate, not independently verified | PR #96 owner, then Reviewer A | Independent verification of the F2 fix; re-check that the candidate's lineage rule is at least as strict |
| X3 Signed pin | The recorded invocation carries `--pin` and `--max-pin-age-seconds`; the signed pin (pin id, pinned_by, key id, 128-hex signature, seq, entry digest, genesis digest, capture time) equals the head at verification in seq **and** digest, is fresh and not skewed beyond 300 s, and is signed by an authority distinct from operator and custodian. `--store-head` is only an unsigned truncation check | `test_r4_custody_gate.py::test_b5_*`, `test_b7_*`, `test_b10_*` | A pin is still a declared value; `LocalAppendOnlyStore` can be re-sealed (#96 N4) | PR #96 owner (store), checklist operator (channel) | #96 to provide an attestable external head; until then the channel kind is declared |
| X4 Duplicate oracle mismatch | `duplicate_oracle.py` is independent of `quality.py`; EXT-DEF-1 recorded below | `test_duplicate_oracle.py::test_known_shared_blind_spot_is_detected` | Resolved at the candidate (differential evidence); ordering metric differs by definition | PR #96 owner / Rin | Re-run the differential at any re-pin; decide the ordering metric |
| X5 Identity grammar | `custody_gate.identity_problem` applies the strict raw-string grammar `[a-z0-9][a-z0-9._@-]{0,127}` of the candidate (no normalisation first, no `+`, no uppercase, non-ASCII refused); it never accepts what #96 refuses (checked by `test_v9_pr96_live_export.py`) | `test_r4_custody_gate.py::test_b3_*` | None on the PR #95 side; the stricter rule may refuse names #96 accepts | PR #95 (this package) | Re-align if #96 changes `normalize_identity` |
| X6 Operator/custodian independence | Self-approval compared over the approver's id and every alias; every bundle BLOCKED on `operator_identity_not_authenticated` / `dep01_principal_authentication_unavailable` | `test_r4_custody_gate.py::test_b1_*`, `test_b2_*`, `test_b4_*` | PR #96 N1: identities are declared strings on both sides; claimed fixed at the candidate (signed principals), not independently verified | PR #96 owner, then Reviewer A | Candidate signs operator, approver and pin with Ed25519 keys (key ids recorded); principal authentication is still not independently approved, so the bundle stays BLOCKED (DEP-01, gate reason `dep01_principal_authentication_unavailable`); independent re-review. V10: the record's people are principal ids and `approver_identities` must be exactly the custodian (the candidate registry has no aliases) |

## Re-pin to PR #99 candidate `d2e8ee9e...` (phase 2)

The pin moved from the phase 1 candidate (recorded as a prior, superseded reference in the manifest) to
the PR #99 head (branch `claude/market-history-fix-v5`, stacked on #96). It is still a candidate:
not gate-approved, not independently reviewed. Executed against a `git archive` of the new head:
schemas, extractor/manifest/quality versions, flag sets, identity grammar, pin skew and the MT5
allow-list are unchanged (X2/X3/X5/X6 fixtures regenerated from the new export); the verifier now has
an exit-code contract (0 = TRUSTED only, 1 untrusted, 2 invalid input, 3 operational) and
`--require-trusted` is a no-op. Incompatibilities found in this package and fixed: the gate accepted
a `--store-head` or `--max-pin-age-seconds` the candidate refuses (trailing newline, more than 18
digits, more than 10 years), and the validator accepted a trailing newline on hex ids, signatures,
principal ids and pins. See [V4-CUSTODY-COMPATIBILITY.md](V4-CUSTODY-COMPATIBILITY.md).

## Open contract decision: out-of-order metric (escalation, no choice made here)

| Item | Detail |
|---|---|
| Definitions | #96 `non_monotonic_count` counts **adjacent descents** (a tick whose time is lower than the previous tick). The #95 oracle `out_of_order` counts ticks whose time is **below the running maximum**. |
| Evidence | Golden vector D6 `(T3,A) (T1,B) (T2,C)`: #96 = 1, oracle = 2. D3 and D9 agree (2 and 2). Pinned by `test_v9_out_of_order_metric.py` (both numbers, and a comparative table). |
| Why it is open | Neither definition is specified by an accepted ADR for this package. This package does not invent trading or data-quality semantics, so it neither switches the oracle nor treats the values as equal. |
| Owner / decision | Rin and the #96 owner. Until decided, case 5 records both numbers and marks ordering as `definition_differs`. |

## DEP-03 proposed interface (architecture only, NOT implemented)

The real half needs a reviewed way to run the extractor that this package can drive without
operator-written code. Proposal for a separate, separately reviewed PR in the extractor stack; it is
out of scope here and nothing below exists yet:

1. **Source injection.** `extract(...)` takes a `HistorySource` protocol argument (default: the
   production `Mt5ReadOnlyHistorySource`); the CLI gains no flag for it. A reviewed driver module
   (for example `scripts/readiness_driver.py`) constructs either the production source or a
   recording proxy around it, never a credential-bearing object.
2. **Recording proxy.** Wraps the real module, records call names and argument shapes (never
   values of credential keywords), and refuses every name outside the read-only allowlist
   (`order_send`, `order_check`, `positions_get`, ... raise before reaching the terminal).
3. **Diagnostics.** A read-only `--report-raw-layout` mode prints `dtype.descr`, raw row counts and
   derived fold ranges without writing a dataset.
4. **Fault hook.** A test-only hook (fail at chunk N, simulate crash after temp write) that the
   production entry point cannot enable.

Acceptance criteria before any DEP-03 step flips from BLOCKED: the interface is merged in the
extractor stack under independent review; `execution_contract.EXTRACTOR_CAPABILITIES` lists exactly
what it offers and `REAL_HALF_STEPS` is updated with a test; the driver needs no operator-written
code; mock evidence still cannot be promoted to REAL (C9/C10 tests stay green); and Rin/the Owner
authorizes any real Windows execution separately.

## Extractor defects requiring separate ownership (PR #96 not modified)

| ID | Defect | Where (as read at the pinned candidate) | Evidence | Severity (proposed, for the owner to decide) |
|---|---|---|---|---|
| EXT-DEF-1 | `quality_report` counts exact duplicates and same-timestamp quotes only against the previous tick, so `(t,A) (t,B) (t,A)` gives 0 exact and 2 same-ts. It also ignores `flags`, so a flags-only difference counts as an exact duplicate. | `packages/nexora/market_history/quality.py` (`quality_report`) | `test_duplicate_oracle.py` (D2, D3, D9 and the flags case); Reviewer B `adv_diff.py` | MAJOR candidate (quality evidence understates duplicates) |
| EXT-DEF-2 | `TimeSegment` accepts any integer offset (1e9 s accepted). | `convert.py` `TimeSegment.__post_init__` | Reviewer B B3; `test_case3_offset_beyond_a_day_refused` (package-side bound) | MINOR candidate |
| EXT-DEF-3 | The provider reads `time_msc` only and never cross-checks `time`; a conflicting row is not detected. | `provider.py` (`time_msc=int(r["time_msc"])`) | Reviewer B B3; `test_case3_time_and_time_msc_must_agree` | MINOR candidate |
| EXT-DEF-4 | `preflight` refuses a window starting before the first segment with `time_conversion_does_not_cover_window`; PR #95 previously documented `no_declared_offset_for_time`. | `extract.py` `preflight` | Reviewer B F5 | Not a defect: documentation drift, fixed here (matrix case 3) |

## Provisional interfaces of this package

Everything quoted from the stack is provisional until the stack is reviewed and merged:
`execution_contract.EXTRACT_*` (CLI flags), `EXTRACTOR_VERSION`, `MANIFEST_SCHEMA`,
`custody_gate.V4_*`, the refusal codes in INTEGRATION-MATRIX.md and the reference model, and the
custody evidence fields of the evidence record. Checklist A3 re-verifies all of them at the SHA that
actually runs.
