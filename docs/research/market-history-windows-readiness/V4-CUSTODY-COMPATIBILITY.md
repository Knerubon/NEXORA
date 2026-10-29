# V4 custody and writer-lock compatibility (finding B-M2)

| Field | Value |
|---|---|
| Reference | PR #96 line, candidate PR #99 `claude/market-history-fix-v5` (stacked on #96) at the **candidate** head `d2e8ee9ef4f6eb438dc79c5e04ad43ed46de764d` (single source: [dependency-manifest.json](dependency-manifest.json)), exported read-only. PR #96 was not checked out, changed, commented on or reviewed here. |
| Status of the reference | **Candidate head, not a gate-approved baseline.** The candidate head is not the gate-approved baseline: `gate_approved_baseline_sha` is `null`, `custody_gate.V4_BASELINE_APPROVED` is `False`. A pin match proves only that a reviewed contract was read at that commit. |
| Consequence | Every TRUSTED step in this package is **BLOCKED** (DEP-01, DEP-03). The procedure below describes the candidate contract so the package is ready when the dependency resolves; it does not make TRUSTED reachable. |
| Re-pin | The pin is one parameter (`pr96_candidate.head_sha`). A new #96 head is adopted only by changing the manifest, re-running the live-export contract test and updating the compatibility tests; an abbreviated, mismatched, stale or unapproved head is refused (`extractor_sha_not_pinned_pr96_candidate:<class>`). |

Everything quoted here (flags, codes, constants) was read at the candidate head and must be
re-verified at the SHA that actually runs (checklist A3). Nothing here invents a CLI option, an API
or a Windows filesystem guarantee. Where an interface is missing, the dependency is recorded as
BLOCKED. Earlier #96 heads named in the manifest are listed under "Historical (stale) references"
below and are not a contract for anything.

## What changed from V3 (the assumption Reviewer B found invalid)

PR #95 at `382065b` required `verify_market_history.py --anchor ... --require-trusted` to exit 0
with `TRUSTED`. At V4 that is unreachable by design. The module docstring of
`scripts/verify_market_history.py` says a V3 `--anchor` "is cross-checked but never grants trust",
and `extract._dataset_status` adds `legacy_anchor_is_not_custody_evidence` whenever an anchor is
given without `--store`/`--registry`. The anchor-only step is removed from the matrix and the
checklist.

## The stages at the candidate head

| # | Stage | Existing V4 interface (as read) | Who | Output / failure semantics |
|---|---|---|---|---|
| 1 | Candidate extraction | `scripts/extract_market_history.py ... --operator <id> --operator-key <private key outside checkout and dataset> --candidate-out <file outside --out>` (extractor `nexora.market-history-extractor/6`, manifest `nexora.market-history/4`) | Operator | Dataset is born `UNTRUSTED`. The candidate carries an operator Ed25519 signature and grants nothing. Operator id must match the strict grammar `[a-z0-9][a-z0-9._@-]{0,127}`; the planner refuses anything else. |
| 2 | Content and manifest verification | `verify_market_history.py --dataset <dir>` (also re-run inside stages 3 and 5) | Anyone | `integrity:*` and `hard_defect:*` reasons. Without `--require-trusted` a cleanly verified but UNTRUSTED dataset exits 0 (Reviewer A I5), so automation must always pass `--require-trusted`. |
| 3 | Independent custody approval | `scripts/custody_market_history.py approve --dataset --store --registry --signing-key --evidence-ref --candidate` | Authorized custodian holding its own signing key, distinct from operator and candidate creator | Appends a hash-chained, signed approval entry (`approval_id`, `key_id`, signature, `acknowledged_revocations`). A candidate is reusable across approvals only with the revocation lineage acknowledged; replay is refused. |
| 4 | Signed pin | `custody_market_history.py pin --store <dir> ...` (pin authority key; `keygen` creates throwaway keys) | Pin authority, independent of operator and custodian | Emits a signed pin (`pin_id`, `pinned_by`, `key_id`, `signature`, `seq`, `entry_sha256`, `store_genesis_sha256`, `captured_at`). `head` / `--store-head` remains an unsigned truncation check only. `recover-store` is a custodian recovery tool and not an evidence step. |
| 5 | TRUSTED eligibility | `verify_market_history.py --dataset --store --registry --pin <pin.json> --max-pin-age-seconds <N> --require-trusted` | Second person, clean checkout, different session | Exit 0 only when `TRUSTED`. The pin must be signed by a registry principal, fresh within `--max-pin-age-seconds` (skew limit 300 s) and consistent with the store. |
| 6 | Revocation lineage | `custody_market_history.py revoke ...`; approvals list `acknowledged_revocations` | Custodian; verifier | A later revoke wins whoever wrote it. A re-approval must acknowledge every revocation id; replay of a revoked approval is refused. |
| 7 | Writer-lock ownership | Inside `extract`; lock file `.writer.lock`, format `nexora.writer-lock/2` | Extractor | OS advisory lock on a held descriptor (`flock` on POSIX, one-byte `msvcrt.locking` on Windows). **Nothing is ever reaped.** A second writer, or an unverifiable or foreign record, gives `writer_lock_held` (fail closed). The Windows path is "implemented, UNVERIFIED". An external `rm .writer.lock` admits a second writer (Reviewer A F6), so this package never instructs removing a lock while any run could be active. |

## Verifier exit-code contract (candidate PR #99 head)

| Exit | Meaning | Gate reading |
|---|---|---|
| 0 | TRUSTED only | Still not approval: the gate also needs status `TRUSTED`, no reasons and every other check; the validator rejects exit 0 with any other status (`verifier_exit_zero_without_trusted_status`). |
| 1 | UNTRUSTED (includes a cleanly verified dataset with no custody evidence) | not trusted |
| 2 | invalid input (arguments, anchor, registry, pin, store head, max pin age) | not trusted |
| 3 | operational error | not trusted, fail closed |
| other | outside the contract | `verifier_exit_code_outside_contract` |

`--require-trusted` is accepted and is a no-op at the candidate. This package still requires it in
the recorded invocation (over-blocking, never weakening) so a record cannot be read against the
older behaviour in which exit 0 without it meant "cleanly verified, UNTRUSTED". `--max-pin-age-seconds`
is bounded at 10 years (an overflow guard, not a policy). `custody revoke` prints
`repin_required`: a revocation newer than the latest pin must be covered by a fresh signed pin
(`revocation_outside_pinned_chain` otherwise). Hostile input yields typed reason codes
(`invalid_timestamp`, `json_too_deep`, `platform_unproven:<x>`) instead of `*_internal_error`.

## Why TRUSTED is unreachable today (DEP-01)

1. **Platform allowlist.** `fsutil.PROVEN_PLATFORMS = frozenset({"linux"})`. `capability_blockers`
   turns a `win32` capability record into `platform_unproven:win32`, and `verifier_blockers` does the
   same for a verifier running on Windows. A dataset extracted on the Windows MT5 node **cannot be
   TRUSTED at V4**, whoever approves it (Reviewer A I1). This is the correct fail-closed direction.
   It is also a programme blocker that needs an owner decision. It is not something this package
   can work around.
2. **Candidate not approved; principals unauthenticated.** The candidate is not a gate-approved
   baseline (`V4_BASELINE_APPROVED = False`) and principal authentication is not independently
   approved (`V4_PRINCIPAL_AUTHENTICATION_AVAILABLE = False`, DEP-03). A TRUSTED verdict at the
   candidate is not evidence-grade.

The checklist therefore ends at "dataset stays UNTRUSTED (development use only); TRUSTED BLOCKED
(DEP-01)". `custody_gate.V4_BASELINE_APPROVED` is `False` and may be flipped only by an independent
review that approves the V4 custody baseline.

## Evidence mapping (record field <- V4 source)

| Evidence record field | Source at V4 |
|---|---|
| `dataset.dataset_id`, `content_sha256`, `manifest_sha256` | extractor stdout JSON (stage 1) |
| `dataset.candidate_sha256` | the `candidate_sha256` field of the `--candidate-out` file |
| `dataset.development_status` | `status` printed by the stage-5 verifier, never by the extractor |
| `custody.store_ref`, `registry_ref` | the store directory and registry file the **verifier** supplied (outside the dataset) |
| `custody.approval_seq`, `approval_entry_sha256` | stdout of `custody_market_history.py approve` (`seq`, `entry_sha256`) |
| `custody.store_head`, `store_head_channel_ref` | `custody_market_history.py head` at verification time, and where the pin was published |
| `custody.pin_*`, `max_pin_age_seconds`, `verified_at_utc` | the signed pin file and the verifier invocation (`--pin`, `--max-pin-age-seconds`) |
| `custody.operator_key_id`, `operator_signature`, `approver_key_id`, `approval_signature`, `approval_id`, `approved_at_utc`, `candidate_sha256` | the candidate file and the `approve` output |
| `custody.revocation_ids`, `acknowledged_revocations` | revoke entries and the approval's acknowledged list |
| `custody.revocations_after_approval`, `custody.revocation_seqs` | revoke entries for this dataset (`seq` list) and the count with `seq` > `approval_seq` |
| `custody.candidate_approval_seqs` | `seq` of every approval entry for this candidate (round 4, X2) |
| `custody.store_head_at_verification`, `custody.store_head_channel_kind` | `head` output at verification time; whether the pin came from an independent channel before verification (round 4, X3) |
| `custody.verifier_argv`, `verifier_exit_code`, `verifier_status`, `verifier_status_reasons` | stage-5 invocation as an argv list (not free text), exit code and printed JSON |
| `people.custodian`, `custody.approver_identities`, `custody.registry_identities` | the approver's registry id and every alias (stage 3), and every id and alias in the verifier's registry |
| `custody.candidate_creator`, `custody.extraction_platform`, `custody.writer_lock`, `custody.store_chain` | candidate `created_by`, manifest `filesystem_capabilities.platform`, lock outcome, `head` `chain` |
| `node.filesystem_type` and case 6 | the manifest's `filesystem_capabilities` record plus the package probes |

## Adversarial coverage (B1-B10, round 4 B1-B12)

`tests/windows_integration_readiness/custody_gate.py`, `test_v4_custody_compat.py` and
`test_r4_custody_gate.py` model the checklist gate, not PR #96's code. Each case must add its own
refusal reason:

| Case | Refusal |
|---|---|
| B1 anchor-only verification | `anchor_only_or_no_custody_inputs_never_trusted` |
| B2 no approval | `no_custody_approval` |
| B3 unauthorized or self approval | `approver_not_in_verifier_registry`, `self_approval_by_operator` |
| B4 revoked | `approval_revoked` |
| B5 chain tampering | `evidence_store_chain_invalid:<problem>` |
| B6 stale or missing head pin | `stale_pinned_head`, `store_head_not_pinned` |
| B7 V3 anchor as V4 custody | `legacy_v3_anchor_is_not_custody_evidence`, `legacy_or_unknown_manifest_schema` |
| B8 writer lock held | STOP `writer_lock_not_owned:writer_lock_held` |
| B9 Windows guarantees | `windows_guarantee_unverified:<g>`, `dep01_platform_unproven_at_v4:win32` |
| B10 extractor changed after checklist | STOP `extractor_sha_changed_since_checklist`, `extractor_version_changed:<v>` |

Round 4 (findings N3 and R3-2; cross-track X2, X3, X5, X6):

| Rule | Refusal |
|---|---|
| The verifier invocation is an argv list: optional launcher (`python`, `uv run [--no-sync] python`), then `scripts/verify_market_history.py`, then only reviewed flags in `--flag value` form. `--dataset`, `--store`, `--registry`, `--store-head` and `--require-trusted` are all required. | `verifier_argv_not_v4_verifier` (for example `echo TRUSTED`), `verifier_argv_missing:<flag>`, `verifier_argv_malformed:<tok>`, `verifier_argv_duplicate:<flag>`, `verifier_argv_store_head_malformed`; unknown flag is STOP |
| The recorded pin is the `--pin` file of that invocation (its `seq` and `entry_sha256`), and it equals the store head at verification in sequence **and** digest | `recorded_pin_absent_from_verifier_invocation`, `recorded_pin_differs_from_verifier_invocation`, `stale_pinned_head`, `pinned_head_digest_mismatch` |
| The approval lies inside the pinned chain; at the head sequence it is the head entry; no revocation beyond the pin | `approval_outside_pinned_chain`, `approval_entry_differs_from_pinned_head`, `approval_entry_sha256_not_recorded`, `revocation_outside_pinned_chain` |
| Self-approval is compared over the approver's id **and every alias** (as V4 `_independence_problem` does); the alias set is mandatory and must be in the registry | `self_approval_by_operator`, `self_approval_by_candidate_creator`, `approver_alias_set_not_recorded`, `approver_alias_not_in_verifier_registry` |
| Identities mirror PR #96 `normalize_identity` (no control or format characters, not empty) and are stricter: after NFKC and case-folding only `[a-z0-9._@+-]` is accepted, so confusable letters from other scripts are refused, not compared | `identity_ambiguous:<role>`, `identity_control_or_format_character:<role>`, `identity_empty:<role>` |
| Identities are declared, not authenticated. Every bundle is blocked until PR #96 authenticates principals and that is independently approved (`V4_PRINCIPAL_AUTHENTICATION_AVAILABLE = False`) | `operator_identity_not_authenticated`, `approver_identity_not_authenticated`, `dep01_principal_authentication_unavailable` |
| X2: a revocation after the **first** approval of the candidate wins, so a re-appended revoked approval stays revoked. A legitimate re-approval needs a new candidate. | `approval_replayed_after_revocation`, `approval_not_among_candidate_approvals` |
| X3: the pin must come from an independent channel recorded before verification; a pin read from the same store at verification time cannot reveal a re-sealed store | `store_head_pin_not_from_independent_channel`, `same_store_pin_presented_as_independent`, `store_head_pin_channel_unknown` |
| Guarantees use a fail-closed **allow-list** (`VERIFIABLE_GUARANTEES`, empty while DEP-02 is unresolved): any claimed guarantee, listed or not, is blocked | `guarantee_not_on_allow_list:<g>`, `dep02_windows_guarantees_unresolved` |

With `V4_BASELINE_APPROVED` and `V4_PRINCIPAL_AUTHENTICATION_AVAILABLE` both `False` (shipped), every
bundle is BLOCKED. Tests flip them only inside `monkeypatch`, never through a gate parameter.

Limitation: these tests prove the **gate** refuses each case. They cannot prove that PR #96's code
does. B4 replay (Reviewer A F2), the integrity/custody TOCTOU (F3) and declared-name self-approval
(N1) are #96 defects, owned there. Requiring an alias set and an independent pin channel does not
authenticate anything: the values are still declared by the people being checked.

## Cross-track dependencies

See [DEPENDENCY-REGISTER.md](DEPENDENCY-REGISTER.md): DEP-01 (custody interface) and DEP-02 (writer
lock) are owned by PR #96. PR #96 remains untouched by this package.

## Historical (stale) references

These earlier #96 heads appear only here and in the manifest's `stale_references`. They are
**stale, obsolete and superseded** and must never be used as a contract or a pin; the gate and the
planner classify them `STALE`.

| Head | Status | Why it is not compatible |
|---|---|---|
| `1c536d5acdef2c9fe47ef5a6ecfb127595ef42b8` | prior candidate, superseded by the current pin (PR #96 head, phase 1) | verifier exit 0 could mean "cleanly verified but UNTRUSTED" when `--require-trusted` was absent; no exit-code contract (1 / 2 / 3); hostile input produced `*_internal_error`; `revoke` printed no `repin_required` |
| `d6a7cda72a88600e2ccf6554605f1c50478893ea` | stale, obsolete, superseded by the candidate | extractor `/5`; approvals took `--custodian` (declared name, no signature); verifier had only an unsigned `--store-head`; looser identity grammar |
| `e74ad035e7f21aa07528965da6ebe190c0512793` | stale, obsolete, superseded (stack tip #93) | V3 anchor era; no custody store, signing or pin |

## Candidate contract compatibility evidence

- Gate/planner/validator constants are checked against the manifest by `test_v9_dependency_pin.py`.
- `test_v9_pr96_live_export.py` exports the pinned head with `git archive` and compares CLI flags,
  versions, schemas, the strict identity grammar (this package never accepts what #96 refuses) and
  the MT5 allow-list against the real code; it skips explicitly (not a pass) when the object is absent.
- `test_v9_custody_signed_pin.py` maps fixtures generated by running the real #96 `custody` module
  (throwaway keys, public material only: single approval, revoke then re-approve, replay after
  revocation) onto the bundle and asserts the gate decisions.
