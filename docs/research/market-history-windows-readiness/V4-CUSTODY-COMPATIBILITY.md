# V4 custody and writer-lock compatibility (finding B-M2)

| Field | Value |
|---|---|
| Reference | PR #96 `claude/market-history-fix-v4` at `d6a7cda72a88600e2ccf6554605f1c50478893ea`, read with `git show` only. PR #96 was not checked out, changed, commented on or reviewed here. |
| Stack | #89 -> #91 -> #92 -> #93 (`e74ad035e7f21aa07528965da6ebe190c0512793`) -> #96 (`d6a7cda7...`) |
| Status of the reference | **Not an approved baseline.** PR #96 Reviewer A: REQUEST CHANGES, G1 NOT MET, 3 MAJOR (F1 forgeable approvals, F2 revocation replay, F3 trust not bound to the verified bytes). |
| Consequence | Every TRUSTED step in this package is **BLOCKED** (DEP-01). The procedure below describes the V4 contract so the package is ready when the dependency resolves; it does not make TRUSTED reachable. |

Everything quoted here (flags, codes, constants) was read at `d6a7cda7...` and must be re-verified at
the SHA that actually runs (checklist A3). Nothing here invents a CLI option, an API or a Windows
filesystem guarantee. Where an interface is missing, the dependency is recorded as BLOCKED.

## What changed from V3 (the assumption Reviewer B found invalid)

PR #95 at `382065b` required `verify_market_history.py --anchor ... --require-trusted` to exit 0
with `TRUSTED`. At V4 that is unreachable by design. The module docstring of
`scripts/verify_market_history.py` says a V3 `--anchor` "is cross-checked but never grants trust",
and `extract._dataset_status` adds `legacy_anchor_is_not_custody_evidence` whenever an anchor is
given without `--store`/`--registry`. The anchor-only step is removed from the matrix and the
checklist.

## The seven stages

| # | Stage | Existing V4 interface (as read) | Who | Output / failure semantics |
|---|---|---|---|---|
| 1 | Candidate extraction | `scripts/extract_market_history.py ... --operator <id> --candidate-out <file outside --out>` | Operator | Dataset is born `UNTRUSTED`. The candidate (`nexora.market-history-anchor-candidate/1`) "grants nothing". `--candidate-out` without `--operator` is an argparse error. A candidate or anchor path inside the dataset gives `candidate_inside_dataset` / `anchor_inside_dataset`. An existing file gives `*_file_exists`. Exit 1 when verification problems or usability blockers exist. |
| 2 | Content and manifest verification | `verify_market_history.py --dataset <dir>` (also re-run inside stages 3 and 5) | Anyone | `integrity:*` and `hard_defect:*` reasons. Without `--require-trusted` a cleanly verified but UNTRUSTED dataset exits 0 (Reviewer A I5), so automation must always pass `--require-trusted`. |
| 3 | Independent custody approval | `scripts/custody_market_history.py approve --dataset --store --registry --custodian --evidence-ref --candidate` | Authorized custodian, distinct from operator and candidate creator | Appends a hash-chained approval entry. Refusals: `dataset_not_approvable`, `approval_refused:<why>` (for example `approver_not_authorized`, `self_approval_by_operator`), `store_inside_dataset`. Identity is a **declared** string compared after NFKC normalization and case folding. There is no signature (Reviewer A F1). |
| 4 | EvidenceStore chain verification | `custody_market_history.py head --store <dir>` prints `{"chain": "ok"|<problem>, "head": "SEQ:SHA256"}` | Verifier | Exit 1 on a chain problem. The head must be pinned through an **independent channel** at verification time. |
| 5 | TRUSTED eligibility | `verify_market_history.py --dataset --store --registry --store-head SEQ:SHA256 --require-trusted` | Second person, clean checkout, different session | Exit 0 only when `TRUSTED`. `TRUSTED` needs clean verification, no hard defects, schema `nexora.market-history/4`, a capability record that proves a supported platform and filesystem, a verifier on a proven platform, a recorded operator, and a valid, un-revoked approval bound to `dataset_id`, `content_sha256`, `manifest_sha256`, `symbol` and `window_raw_msc`. |
| 6 | Revocation and pinned head | `custody_market_history.py revoke ...`; `--store-head` on verify | Custodian; verifier | Any later revoke entry wins, whoever wrote it. `--store-head` is optional in the V4 CLI. Without it, or with a stale pin, a truncated store can hide a revocation (Reviewer A F5). This package therefore requires a pin taken at verification time (stricter than V4). Reviewer A F2 (re-appending a revoked approval restores TRUSTED) is open on #96. |
| 7 | Writer-lock ownership | Inside `extract`; lock file `.writer.lock`, format `nexora.writer-lock/2` | Extractor | OS advisory lock on a held descriptor (`flock` on POSIX, one-byte `msvcrt.locking` on Windows). **Nothing is ever reaped.** A second writer, or an unverifiable or foreign record, gives `writer_lock_held` (fail closed). The Windows path is "implemented, UNVERIFIED". An external `rm .writer.lock` admits a second writer (Reviewer A F6), so this package never instructs removing a lock while any run could be active. |

## Why TRUSTED is unreachable today (DEP-01)

1. **Platform allowlist.** `fsutil.PROVEN_PLATFORMS = frozenset({"linux"})`. `capability_blockers`
   turns a `win32` capability record into `platform_unproven:win32`, and `verifier_blockers` does the
   same for a verifier running on Windows. A dataset extracted on the Windows MT5 node **cannot be
   TRUSTED at V4**, whoever approves it (Reviewer A I1). This is the correct fail-closed direction.
   It is also a programme blocker that needs an owner decision. It is not something this package
   can work around.
2. **Custody design not approved.** Even on Linux, Reviewer A's F1, F2 and F3 mean a TRUSTED
   verdict at `d6a7cda7...` is not evidence-grade.

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
| `custody.revocations_after_approval` | count of revoke entries with `seq` > `approval_seq` |
| `custody.verifier_command`, `verifier_exit_code`, `verifier_status`, `verifier_status_reasons` | verbatim stage-5 command, exit code and printed JSON |
| `people.custodian` | the `--custodian` value used in stage 3 |
| `node.filesystem_type` and case 6 | the manifest's `filesystem_capabilities` record plus the package probes |

## Adversarial coverage (B1-B10)

`tests/windows_integration_readiness/custody_gate.py` and `test_v4_custody_compat.py` model the
checklist gate, not PR #96's code. Each case must add its own refusal reason:

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

Limitation: these tests prove the **gate** refuses each case. They cannot prove that PR #96's code
does. B4 replay (Reviewer A F2) and the integrity/custody TOCTOU (F3) are #96 defects, owned there.

## Cross-track dependencies

See [DEPENDENCY-REGISTER.md](DEPENDENCY-REGISTER.md): DEP-01 (custody interface) and DEP-02 (writer
lock) are owned by PR #96. PR #96 remains untouched by this package.
