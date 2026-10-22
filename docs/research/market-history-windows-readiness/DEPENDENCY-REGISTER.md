# Cross-track dependency register

Owned by the PR #95 remediation orchestrator. Recorded on 2026-10-22 against the SHAs below. A
dependency is never marked RESOLVED because its CI is green; it needs the named evidence.

Extractor stack: #89 -> #91 -> #92 -> #93 (`e74ad035e7f21aa07528965da6ebe190c0512793`) ->
#96 (`d6a7cda72a88600e2ccf6554605f1c50478893ea`). None of these is merged. This package reads them
with `git show` only and never modifies them.

## Dependencies

| ID | Owner | Exact referenced SHA | Required contract | Verification status | Blocking impact | Evidence | Resolution / next action |
|---|---|---|---|---|---|---|---|
| DEP-01 Custody interface | PR #96 (Track A of V4) | `d6a7cda7...` | TRUSTED requires a verifiable, un-revoked custodian approval in an append-only store, checked by `verify_market_history.py --store --registry --store-head --require-trusted` on a proven platform | **BLOCKED.** (1) `fsutil.PROVEN_PLATFORMS = {"linux"}`, so a dataset extracted or verified on Windows cannot be TRUSTED. (2) PR #96 Reviewer A: REQUEST CHANGES, 3 MAJOR (F1 forgeable approvals, F2 revocation replay, F3 integrity/custody TOCTOU), G1 NOT MET. | Checklist D4 and matrix case 7 steps 3-4 cannot pass. `custody_gate.V4_BASELINE_APPROVED = False`. No evidence record can derive `overall_result = PASS`. | [V4-CUSTODY-COMPATIBILITY.md](V4-CUSTODY-COMPATIBILITY.md); `test_v4_custody_compat.py`; PR #96 Reviewer A report | PR #96 owner fixes F1-F3 and an independent re-review approves custody. An owner decision is needed on how a Windows-extracted dataset can ever become TRUSTED (prove the Windows platform, or verify on Linux from a transferred copy). Then re-point this package and re-review. |
| DEP-02 Writer lock | PR #96 | `d6a7cda7...` (`lock.py`) | OS advisory lock on a held fd, never reaped, `writer_lock_held` fail closed; Windows `msvcrt.locking` path | **PARTIAL.** Matches V4 semantics in this package (no reaping, no manual removal while a run may be active). Linux ext4 tested by #96 and Reviewer A. Windows path UNVERIFIED. Reviewer A F6 (external unlink admits a second writer) is open. | Case 6 lock evidence can be recorded but cannot claim Windows exclusivity (`windows_msvcrt_lock_exclusivity` stays in `not_verified`). | UNSUPPORTED-GUARANTEES.md "File locking"; checklist C5/C6 | Windows lock exclusivity needs an authorized real-half run. F6 hardening (lock the directory fd) is owned by #96. |
| DEP-03 Extractor execution interface | Extractor stack (#93/#96 line), separate PR | `d6a7cda7...` (`scripts/extract_market_history.py`) | A reviewed, authorized entry point for the real half: source/module injection or a mock source, a fault hook, `dtype.descr` exposure, vector replay, and argument recording around the real module | **BLOCKED.** The CLI constructs `Mt5ReadOnlyHistorySource()` itself and offers none of these (`execution_contract.EXTRACTOR_CAPABILITIES = frozenset()`). | Eight real-half steps are BLOCKED (checklist register). Cases 1, 2, 4, 5, 6 and 7 cannot reach PASS. Real-half readiness is not declared. | `execution_contract.py` `REAL_HALF_STEPS`; `test_execution_contract.py` | Add reviewed injection points or a driver in the extractor stack (a new, separately reviewed PR), then flip the steps here. Any real Windows execution needs separate Rin authorization. |
| DEP-04 Duplicate detection | PR #96 (extractor `quality.py`) | `d6a7cda7...` (unchanged since `e74ad03`) | Exact duplicates are found wherever they occur; a flags-only difference is not an exact duplicate (ORACLE-SPEC.md) | **DEFECT REPORTED (EXT-DEF-1).** Not fixed here. | Case 5 "counts equal the oracle" will fail against the current extractor on non-adjacent duplicates; the record must show it as a finding. | `test_duplicate_oracle.py::test_known_shared_blind_spot_is_detected`; Reviewer B `adv_diff.py` | PR #96 owner decides the fix. Reported to the PR #96 review line (Reviewer A) via the orchestrator report. |
| DEP-05 Dataset quality policy | PR #94 (MD-6) | PR #94 HEAD `dd3504e...` as re-reviewed 2026-10-22 | Evidence requirements here must not assume thresholds MD-6 has not approved (gap limits, spread classes, duplicate tolerances) | **OPEN.** PR #94 re-review: REQUEST CHANGES (new MAJOR N1), G3 NO-GO. This package invents no threshold: extreme spreads are unclassified (Reviewer B I2), `--max-gap-ms` is the operator's declared value, and duplicate counts are reports, not pass/fail limits. | None on the offline package. A real-half record cannot claim dataset acceptability. | ORACLE-SPEC.md section 1; `classify_quote` ledger row | Re-check after MD-6 is approved. |

## Extractor defects requiring separate ownership (PR #96 not modified)

| ID | Defect | Where (as read at `d6a7cda7...`) | Evidence | Severity (proposed, for the owner to decide) |
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
