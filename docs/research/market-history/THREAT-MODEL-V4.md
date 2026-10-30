# Market-history threat model (V4, round 3)

Status: self-authored by the implementer; **independent review pending** (Reviewer A round 3).
Round 3 replaced string-based custody with Ed25519-signed statements, revocation lineage and an
independently signed head pin, and closed the verify/trust TOCTOU (rows R3-* below). Rows C2, C3,
C4, C5, C9 and C10 are superseded where an R3 row says so.
Wave V9 (stacked delta over PR #96 at 1c536d5; self-authored, **independent review pending**) added
hostile-input hardening (N-01), a stable verifier exit-code contract (N-03), mutation-driven
regressions (N-04), a characterisation of the revocation-latency window (R-1) and the writer-lock
create/write window (N-05); see rows N01-N05 and sections 8-10. Nothing in V9 is gate-approved and
no governance decision was made.
Scope: `nexora.market_history` datasets and their trust decision. Not covered: MT5/terminal behaviour
(WINDOWS-INTEGRATION), broker data terms, Dukascopy (BLOCKED), anything touching PROD.

## 1. Assets
| Asset | Why it matters |
|---|---|
| Dataset content (chunks, `content_sha256`) | the evidence for validation gates G1-G5 and any baseline freeze |
| Manifest (`manifest_sha256`, `dataset_id`) | binds meaning (time mapping, units, attestation, source) to content |
| Trust decision (`TRUSTED`) | consumers (`require_trusted`) treat the data as evidence |
| Custody evidence (signed candidate, signed approvals/revocations, principal registry, signed head pin) | the only thing that can lift `UNTRUSTED` |
| Private signing keys (operator, custodian, pin authority) | possession of a key IS the identity; never in Git, fixtures, the dataset or the store |
| Writer ownership of an output folder | prevents two writers interleaving into one dataset |
| Protected (holdout) ranges | reading them is itself an access |

## 2. Actors
| Actor | Assumed capability |
|---|---|
| Honest operator on a mis-configured machine | runs on exFAT/network share/Windows; two terminals by accident |
| Rogue or careless extractor/operator | controls the extraction host, the dataset folder, can forge a manifest and recompute every self-hash, can claim any identity string |
| Local attacker with write access to the dataset folder | rename/replace/symlink/hard-link entries, swap directories mid-run, edit lock files |
| Rogue writer of the evidence store | can append arbitrary entries (any approver string, re-appended old approvals); holds no registered private key; cannot (by the store contract) rewrite or delete existing ones |
| Attacker who can rewrite the whole store | delete the tail or replace the chain (only possible if the store is not truly immutable) |
| Authorized custodian | trusted by definition: a malicious custodian can approve bad data (process control) |
| Pin authority | signs the store head it reads through its own channel; must be a principal independent of the operator and the approving custodian |
| Root/administrator on the verifier | out of scope (can alter any check) |

## 3. Trust boundaries
1. **Dataset folder** is untrusted input: everything in it, including its manifest, probe record,
   operator string and any "anchor", is a claim.
2. **Verifier-supplied evidence** (store handle, principal registry with public keys, signed head pin,
   `max_pin_age`) is outside the dataset and is the only source of trust. The registry's public keys
   are the trust root; a hash chain is never treated as proof of identity. The dataset can never carry its own registry, store or anchor
   (`*_inside_dataset` refusals).
3. **Process boundary** between extractor and custodian: different identities, different machines.
4. **OS boundary**: lock exclusion, link atomicity and no-follow semantics are kernel properties of a
   specific platform/filesystem, proven per configuration by a probe plus an allowlist.

## 4. Attack and mitigation table
Legend: E = eliminated by construction, F = fails closed, M = machine-enforced check, P = process control only.

| # | Attack / failure | Mitigation | Class | Test |
|---|---|---|---|---|
| L1 | Reaper reads stale lock, live owner installs a NEW lock, reaper's rename-by-name deletes it (V3 R1) | no reaper exists; ownership is an OS lock on a held fd; nothing is removed by name after a read | E | `test_live_owner_appearing_after_we_looked_is_never_displaced`, structural test |
| L2 | Two acquirers on one stale file / simultaneous reaping | exactly one `flock` wins; loser `writer_lock_held` | E | `test_simultaneous_acquirers...` (8 threads), `test_two_real_processes...` (V3 suite) |
| L3 | Release racing acquisition (waiter locks an unlinked inode) | unlink only while holding the lock and only the held inode; waiter re-checks inode after locking and retries | E | `test_acquirer_that_locked_a_just_released_inode...`, `test_release_while_another_process_probes...` |
| L4 | Release removes someone else's file | inode comparison before unlink | E | `test_release_never_unlinks_a_file_it_does_not_hold` |
| L5 | PID reuse / forged pid or host in the record | record is informational; liveness is the kernel's | E | `test_editing_the_record_of_a_live_owner...`, `test_pid_of_a_live_unrelated_process...` |
| L6 | Empty/partial/garbage/zero-length record | empty = free (nobody holds the lock); anything else unverifiable = `writer_lock_held`, operator acts | E / F | `test_zero_length...`, `test_unverifiable_records_fail_closed...` |
| L7 | Crash between create and write, or after write | kernel releases the lock; empty or complete record of a dead owner is free | E | `test_crash_between_create_and_write...`, `test_crash_after_the_record...` |
| L8 | Foreign host record / shared network folder | foreign-host record fails closed; network/FUSE class blocks TRUSTED | F | parametrized cases, `test_capability_blockers...` |
| L9 | Symlink/directory/FIFO at the lock name | `O_NOFOLLOW`, regular-file check | F | `test_symlink_directory_and_fifo_at_the_lock_name...` |
| L10 | V3 writer running during a V4 run | V3 never takes an OS lock: **not detectable** | P | documented: never mix |
| F1 | Symlinked parent of `--out` | ancestors opened one by one `O_NOFOLLOW` from `/` | E (POSIX) | `test_symlinked_parent_of_out...`, `test_symlinked_grandparent...` |
| F2 | Path swap after validation (TOCTOU) | directory pinned by fd; all I/O `dir_fd`-relative | E (POSIX) | `test_swapping_the_out_path_after_validation...`, `test_pinned_reads_follow...` |
| F3 | Verify through a symlinked dataset path | same pinning on the read side (`dataset_dir_unsafe`) | E (POSIX) | `test_verify_refuses_a_dataset_reached_through_a_symlink` |
| F4 | Symlink/FIFO/dir at chunk or manifest name | lstat classification + `O_NOFOLLOW|O_NONBLOCK` + fstat (V3 F1) | F | V3 suite |
| F5 | Chunk/manifest is a hard link to a file someone else can change | link count must be 1 | M | `test_hard_linked_chunk_or_manifest_is_a_problem` |
| F6 | Torn/overwritten final file | temp + fsync + no-overwrite hard link + read-only, relative to the pinned dir | E | integrity suite, `test_write_once_never_overwrites...` |
| F7 | Filesystem cannot hard-link (FAT/exFAT/some shares) | probe -> `filesystem_cannot_hardlink`, nothing written | F | `test_filesystem_without_hard_links...` |
| F8 | Unproven platform/filesystem produces a TRUSTED dataset | probe recorded in the manifest, verdict **re-derived** from raw probes at verification (stored verdict ignored); verifier platform also checked | M | `test_capability_blockers...`, `test_verifier_platform...`, `test_capability_record_in_the_manifest_gates_trust...` |
| F9 | Dishonest probe record | not detectable by the verifier | P | custodian approval is the control |
| C1 | Extractor self-approves / stores its own anchor | extractor can only create a candidate; trust needs the store + registry supplied by the verifier | M | `test_candidate_alone_or_a_hash_only_anchor_never_trusts` |
| C2 | Unauthorized approver | registry allowlist (with validity window); refused by the tool and re-checked by the verifier | M | `test_unregistered_key_is_refused...`, `test_a_principal_without_the_custodian_role...`, `test_key_validity_is_checked...` |
| C3 | Self-approval (operator or candidate creator, any spelling or registry alias) | normalized identity comparison (NFKC, casefold, whitespace) over id + aliases, against the candidate creator **and** the manifest's recorded operator; missing operator = untrusted | M (superseded by R3-N1: authenticated principals, no normalization) | `test_A4_*`, `test_A5_*`, `test_a_dataset_without_a_recorded_operator...` |
| C4 | A *different* spelling or a second account of the same human | not detectable beyond registry aliases | P | registry maintenance |
| C5 | Forged / edited approval entry | entry hash + prev-link chain; any edit or reseal breaks the next link | M | `test_editing_a_stored_entry...`, `test_resealing_a_replaced_first_entry...` |
| C6 | Replay of a valid approval for another dataset or manifest | approval binds dataset_id + content + manifest hash + symbol + window; candidate bindings re-checked | M | `test_approval_for_one_dataset_cannot_be_replayed...`, `test_approval_is_bound_to_the_exact_manifest...`, `test_a_candidate_for_another_dataset...` |
| C7 | Forged + re-sealed manifest (all self-hashes recomputed) | manifest hash no longer equals the approved one (same id) or no approval exists (new id) | M | same as C6, V3 fuzz tests |
| C8 | Approval timing games (before retrieval, before candidate, future) | ordering and skew checks | M | `test_approval_time...`, `test_approval_before_the_candidate...` |
| C9 | Revocation | any later `revoke` entry (even by an unauthorized writer) denies trust; a later valid approval supersedes only with correct lineage (R3-F2) | M (fail-closed direction) | `test_revocation_withdraws_trust...` |
| C10 | Tail truncation / rollback / whole-chain replacement of the store | superseded by R3-F5: a signed independent pin is REQUIRED for TRUSTED | M | `test_B6_*`, `test_B10_*`, `test_B11_*` |
| C11 | Store or registry shipped inside the dataset | refused (`store_inside_dataset`, `registry_inside_dataset`) | M | tests in the custody file |
| C12 | Malicious custodian | by definition trusted | P | separation of duties, review |
| C13 | Store not actually append-only | `LocalAppendOnlyStore` refuses overwrite of an existing sequence number but cannot stop a user with directory write access | P | use a store with real immutability |
| C14 | Unavailable/corrupt evidence | `require_trusted` raises `dataset_untrusted`; `dataset_status` never raises | F | `test_fail_closed_when_custody_cannot...` |
| R3-F1 | Raw appender writes `approver: "carol"` (any registered custodian name) | approval is accepted only with a valid Ed25519 signature (domain-separated, purpose `custody-approval`) by an unrevoked, in-window key that the registry assigns to that principal with role `custodian` | M | `test_A1_*`, `test_A2_*`, `test_A3_*`, `test_A7_*`, `test_A9_*`, `test_A12_*` |
| R3-N1 | Self-approval through a spoofed/confusable operator string | the operator is the principal whose key verified the signed attestation (`operator-attestation`), must equal the manifest's operator, and must be a different principal from the authenticated approver; ids are strict ASCII (`[a-z0-9][a-z0-9._@-]{0,127}`), other spellings are refused, never normalized (X5); a principal is listed once, so one key cannot act under two names | M | `test_A4_*`, `test_A4b_*`, `test_A5_*`, `test_A6_*`, `test_A10_*` |
| R3-F4 | Back-dated or future-dated approval | key validity is checked at the signed time AND at verification; approvals dated after the pin capture or appended after the pinned sequence do not count | M | `test_A12_*`, `test_B_approval_appended_after_the_pin_is_not_covered` |
| R3-F2 | Re-append of a revoked approval (byte-identical or re-signed) | `approval_id` is unique over the chain (repeat = `replayed_approval_id`); a candidate backs one approval only (`candidate_reused`); every approval must list exactly the prior `revocation_id`s of its dataset (`acknowledged_revocations`), so an old approval cannot be replayed after a revocation and changing the list breaks the signature | M | `test_B1_*`, `test_B2_*`, `test_B2b_*`, `test_B3_*`, `test_B4_*`, `test_B4b_*` |
| R3-F2b | Revocation deleted from the store | an approval acknowledging a revocation that is no longer present sets `custody:revocation_lineage_broken` (never TRUSTED); whole-chain rewrite is caught by the pin | M | `test_B5_*`, `test_B6_*` |
| R3-F5 | Truncation/rollback/rechaining with no pin, or a pin from the same store | TRUSTED requires a head pin (`nexora.market-history-head-pin/1`) signed by a `pin_authority` principal, matching the store genesis and the entry at its sequence, no older than the verifier-supplied `max_pin_age` (no default); an unsigned `--store-head` is never sufficient | M | `test_B6_*`..`test_B12_*`, `test_B11_*` |
| R3-N4 | Pin signed by the operator or the approving custodian | `custody:pin_not_independent` | M | `test_B7_*` |
| R3-F3 | Directory or file swapped between verify, status and use | verify, custody decision and approval all run on ONE pinned directory fd; each file is read once (`_snapshot`: nlink and identity on the open fd, change-during-read refused); `dataset_path_replaced_during_check` / `_during_approval`; consumers read via `open_trusted`, which re-hashes every chunk | M | `test_C1_*`..`test_C9_*`, `test_C12_*` |
| R3-N2 | Approval binds a manifest other than the one verified | the approved manifest is the verified snapshot | M | `test_C2_*` |
| R3-N3 | Hashed bytes differ from parsed bytes | single read; the same buffer is hashed, parsed and quality-recomputed | M | `test_C3_*` |
| R3-F6 | Lock file unlinked externally; second writer takes a new one | an additional `flock` on the pinned DIRECTORY fd (cannot be unlinked); the writer re-checks its record before each chunk and the manifest (`writer_lock_lost`); new required probe `directory_lock_exclusion` | M | `test_D1_*`, `test_D1b_*`, `test_D2_*`, `test_D12_*` |
| R3-N5 | Crash between link and temp unlink wedges the store or the dataset | readers tolerate same-inode temp twins; `recover-store` and `recover_market_history.py` remove only same-inode twins; extract re-runs recover first; no evidence is deleted | M | `test_D3_*`..`test_D5_*` |
| R3-N6 | Duplicate count disagrees with PR #95's oracle | tick-quality/2 identity = (raw_msc, canonical bid, canonical ask, flags or ABSENT) per segment; legacy v1 still verifies; duplicates are reported, not hard defects (Quant decision OPEN) | M | `test_D0_*`, `test_D6_D7_D8_*`, `test_D9_*`..`test_D11_*` |
| N01 | Hostile timestamp at a calendar edge (`9999-12-31T23:59:59-12:00`, `0001-01-01T00:00:00+14:00`) in a signed approval, a signed pin, a registry key window or an anchor: `astimezone` raises `OverflowError`, which escaped as `status_internal_error` / a CLI traceback | one parser (`jsonio.aware_utc`) turns `ValueError` and `OverflowError` into `invalid_timestamp`; callers map it to typed reasons (`approval_N_rejected:invalid:invalid_timestamp`, `pin_malformed:pinned_by_or_captured_at`, `registry_malformed`, `anchor_malformed`) | F | `tests/test_market_history_v9_hostile_input.py` (timestamp group) |
| N01b | Deep JSON nesting (100000 levels) in a store entry, manifest, chunk row, registry, pin, anchor, candidate or lock record: `RecursionError` | `jsonio.loads` rejects nesting above `MAX_JSON_DEPTH` (32; legitimate files nest about 5) by a linear scan that ignores brackets inside strings, and also catches `RecursionError` for row streams. Bytes are decoded strictly as UTF-8 before both the scan and the parser (UTF-16/32 or a BOM is `json_invalid`; PR #99 review M-1: wide encodings desynchronised the byte scan); result is `json_too_deep` / `json_invalid`, mapped to `manifest_unreadable`, `store_unreadable`, `chunk_malformed`, `registry_unreadable`, `anchor_unreadable`, `pin_unreadable` | F | same file (nesting group); `tests/test_market_history_v9_review_minors.py` (encodings, exact bound 32, deep and non-UTF-8 lock records) |
| N01c | `filesystem_capabilities.platform` is a list/dict in a resealed manifest: `TypeError` from `not in PROVEN_PLATFORMS` | type check before membership; any non-string is `capability:platform_unproven` | F | same file (platform group) |
| N01d | Arabic-Indic / fullwidth digits accepted by `\d` (chunk names, store entry names, `--store-head`, `--max-pin-age-seconds`) | ASCII-only patterns (`re.ASCII`, `[0-9]`); CLI numeric arguments parsed with an ASCII regex | F | same file (digits group) |
| N01e | `--max-pin-age-seconds 99999999999999999999`: `OverflowError` traceback | accepted range is 1..`MAX_AGE_SECONDS_SANITY` (10 years): a mechanical overflow guard, NOT a statement of acceptable pin age (governance still owns the real value); out of range is exit 2 `invalid_max_pin_age` | F | same file (CLI group) |
| N01f | Unknown field-type mutations anywhere in manifest, store entry, pin or registry | seeded type-mutation fuzz (seed 20261029 + 0..3, 300 rounds each for manifest/entry/pin, 600 for registry): no `*_internal_error` reason, registry errors only `registry_malformed` | F | `test_fuzz_*` |
| N03 | Verifier exit 0 for a cleanly verified but UNTRUSTED dataset unless the caller added `--require-trusted`; a CI `&&` chain read that as approval | exit-code contract (section 8): **0 only for TRUSTED**; `--require-trusted` kept as a no-op | M | `tests/test_market_history_v9_cli_contract.py` |
| N04 | Seven valid mutants outside what the V4 suite killed (gap boundary, key `valid_from`, chunk contiguity, approval-before-retrieval, `orders_submitted`, revocation acknowledgement, candidate/operator mismatch) | one specific regression per mutant | M | `tests/test_market_history_v9_mutation_regressions.py` (see section 9) |
| R1 | A revocation newer than the latest pin is hidden by truncating the store tail: TRUSTED until the pin goes stale | NOT closed by code; characterised exactly, bounded by `max_pin_age`; mitigated by the process control in section 9 | P | `tests/test_market_history_v9_revocation_latency.py` |
| N05 | "Create-then-write" window of `.writer.lock` (file exists and is empty before the record is written) | not an exclusion gap in V4: ownership is the OS lock held before the write; empty file without a holder is free; only a V3 writer (no OS lock) can collide (row L10) | E / P | `tests/test_market_history_v9_writer_window.py` |
| X1-X4 | Round-2 exploits F1 forged approval, F2 revocation replay, F3 verify/approve TOCTOU, N1 confusable/pseudonymous self-approval | replayed against BOTH `d6a7cda` (exploit works, tests fail) and this head (rejected, tests pass) | M | `tests/test_market_history_v9_exploit_replay.py` |
| V3 | V3 datasets/anchors | still verify (integrity) but never TRUSTED; V3 anchor never grants trust | M | `test_v3_datasets_still_verify...`, V3 suite |

## 5. What is machine-enforced vs process control
Machine-enforced: everything marked M/E/F above, specifically Ed25519 signatures on every statement
that can grant trust (operator attestation, approval, revocation, head pin) with per-purpose domain
separation; registry roles, key validity windows and key revocation; independence of the
AUTHENTICATED operator, approver and pin authority; replay and lineage rules; the hash chain and
bindings; pin freshness against a verifier-supplied age; one pinned object for verify and trust;
capability re-derivation; fail-closed behaviour on any unreadable or malformed evidence; and all
locking/filesystem guarantees on a proven configuration.

Trust root: the public keys in the principal registry the verifier supplies from outside the dataset.
A hash chain proves only that entries were not edited after the fact, never who wrote them.

Process control (not provable by this code), all **UNVERIFIED**:
- registry upkeep: who may add principals and keys, and that one person holds one principal;
- private-key custody: keys generated with `custody_market_history.py keygen` (0600, never in Git,
  never next to the data), rotation and revocation of compromised keys;
- existence of a pin authority and its channel to the store: **no pin authority is designated today**,
  so no dataset can be TRUSTED until governance designates one and sets `max_pin_age`;
- immutability of the real evidence store; independence of the custodian as a person; honesty of the
  extractor's probe record; operator discipline about mixing V3 and V4 writers.

No private key, signature-bearing fixture key or external pin service is part of the repository.
Tests generate keys per run.

## 6. Platform / filesystem matrix
| Configuration | Writer lock | No-follow / dir_fd | Atomic no-overwrite write | Can produce TRUSTED | Verification status |
|---|---|---|---|---|---|
| Linux, local fs (ext4 tested; xfs/btrfs/tmpfs/overlay/f2fs/zfs allowlisted) | `flock` | yes | hard link | **yes** | **cloud-tested (Linux ext4)** |
| Linux, NFS/CIFS/SMB/FUSE/9p/virtiofs | `flock` may be local-only or emulated | yes | depends | no (`filesystem_unproven:network`) | untested |
| Linux, unknown filesystem type | probed | yes | probed | no (`filesystem_unproven:unknown`) | untested |
| macOS | `flock` | yes | yes | no (`platform_unproven:darwin`) | untested |
| Windows NTFS | `msvcrt.locking` (implemented) | no `O_NOFOLLOW`/`dir_fd`: lstat + reparse-point check, check-then-use window | `os.link` | no (`platform_unproven:win32`, `probe_not_proven:dir_fd`) | **UNVERIFIED** (code path simulated on Linux only) |
| FAT / exFAT / many shares | n/a | n/a | no hard links | no | extraction refused (`filesystem_cannot_hardlink`) |

Flipping Windows to supported requires a recorded WINDOWS-INTEGRATION run of the probe and the lock
tests on the node, then adding `win32` (and an NTFS class) to the allowlist in `fsutil.py` plus a
no-follow solution (the pure `lstat` fallback cannot close the check-then-use window).

## 7. Residual risks
1. Windows/NTFS unverified and, as designed, never TRUSTED: the primary extraction host cannot yet
   produce evidence-grade datasets. Needs a Rin/Architect decision on how to prove or accept it.
2. No pin authority exists yet: TRUSTED is BLOCKED by design until one is designated with a
   `max_pin_age` policy (governance decision, not invented here).
3. Key custody, registry upkeep, store immutability and custodian/pin-authority independence are
   process controls; a stolen custodian key or a colluding custodian and pin authority defeats the design.
4. A principal is one registry entry; one human with two registered principals is not detectable.
5. An attestation, the DST/offset evidence and the probe record are claims (unchanged from V3, Q-D01).
6. V3 and V4 writers on the same folder can collide (V3 takes no OS lock).
7. Mode bits are advisory; `root`/admin can alter files, which the hashes and the custody approval then expose.
8. Native-library or kernel bugs in `flock`/`openat` semantics and in the `cryptography` package are
   assumed absent.
9. Duplicate counting uses per-chunk state, but a duplicate cannot be split across chunks: identity
   contains the raw timestamp and chunks partition raw time into `[from, to)` windows, so every
   occurrence of one identity lands in the same chunk (pinned by
   `test_a_duplicate_pair_can_never_be_split_across_a_chunk_boundary`; the earlier wording here and in
   RISK-REVIEW.md claimed otherwise and was wrong). PR #95's oracle agrees on the synthetic vectors
   (D0-D10), not on real data.
10. Revocation latency (R-1): a revocation appended after the newest pin and then removed from the
    store tail is undetected until that pin exceeds `max_pin_age` or a newer pin covers it (section 9).
11. `non_monotonic_count` has two defensible definitions that give different numbers (section 10).
12. Hostile-input handling is by construction for the parsers named in rows N01-N01f, plus a seeded
    fuzz; it is not a proof that no other input can crash another code path.

## 8. Verifier exit-code contract (`scripts/verify_market_history.py`)
| Exit | Meaning | When |
|---|---|---|
| 0 | TRUSTED | and only then: every required condition held (integrity, no hard defects, capabilities, signed un-revoked approval, fresh covering pin) and store, registry, pin and max age were all supplied |
| 1 | UNTRUSTED | a verdict was reached and it is not trusted: no custody evidence (even if the data verifies cleanly), failed integrity or custody, stale or missing pin, revoked, missing dataset directory |
| 2 | invalid input | usage error, unreadable or malformed anchor, registry, pin, store head or max age (including out of range); no verdict was reached |
| 3 | operational error | an internal error prevented a verdict; fail closed, treat as not trusted |

Any non-zero exit means "do not use as evidence". The JSON line repeats the verdict
(`status`, `trusted`, `exit_code`, `exit_meaning`, `status_reasons`) and `trusted` is true only for
exit 0. `--require-trusted` is accepted and ignored: it used to be the only way to get exit 1 for an
untrusted dataset, and the safe behaviour is now unconditional. The custody tool exits 0 done, 1 chain
problem reported, 2 refused or invalid input, 3 internal error (nothing appended). A shell gate such
as `verify_market_history.py ... && use_dataset` is therefore safe.

## 9. Revocation latency (R-1) and the re-pin procedure
A hash chain proves consistency of what is present, never that nothing newer was removed; only a
signed pin proves the store held at least N entries when the pin authority looked. Exact behaviour,
each case pinned by a test:

| Situation | Result |
|---|---|
| revocation in the store, newest pin older than it, store intact | UNTRUSTED (`custody:revoked_at_seq_N`) |
| same, then the revocation entry is deleted from the tail | **TRUSTED** while `now - pin.captured_at <= max_pin_age` (inclusive) |
| same, `now - pin.captured_at > max_pin_age` | UNTRUSTED (`custody:pin_stale`) |
| pin authority pins a head that includes the revocation, store then truncated | UNTRUSTED (`custody:pin_ahead_of_store_truncated`) |
| pin created after the revocation, store intact | UNTRUSTED (revoked) |

**Maximum undetected latency = the verifier's own `max_pin_age`, measured from the newest pin's
capture time** (worst case: the revocation is appended just after that pin was captured). There is no
code default for `max_pin_age`; the CLI now refuses values above 10 years (`MAX_AGE_SECONDS_SANITY`),
which is an overflow guard and not a policy: with a very large value the latency is correspondingly
large. This code does **not** provide immediate revocation detection and must not be described as
doing so.

Mitigation (process control; no policy invented here): after every `revoke`, the pin authority reads
the store through its own channel and issues a new pin (`custody_market_history.py pin --previous
<last-pin>`) that covers the revocation, and verifiers use only the newest pin. `revoke` prints
`repin_required: true` and `repin_covers_seq` as a reminder. Choosing the real `max_pin_age`, the
re-pin deadline after a revocation and who may waive them are governance decisions (section 10).

## 10. Open decisions (escalated; not decided here)
| # | Question | Decision owner | Why it matters |
|---|---|---|---|
| E1 | Designate the pin authority and the `max_pin_age` policy; set the re-pin deadline after a revocation | Rin / governance | no dataset can be TRUSTED without it; sets the R-1 latency |
| E2 | Which `non_monotonic_count` is the contract: adjacent descent (this code, in manifests since quality/1) or running maximum (PR #95 oracle `out_of_order`)? | Rin / Quant-engineering | the number differs on streams with an early high value (e.g. `1000,3000,2000,2500`: 1 vs 2); the yes/no that drives `hard_defects` is identical (proved exhaustively for streams of up to 6 ticks). Changing it means `tick-quality/3`, new manifest hashes and re-verification of existing datasets |
| E3 | Whether duplicates / gaps are hard defects (MD-6 and the duplicate question) | Quant | unchanged from earlier rounds |
| E4 | Whether to enforce a clock-based grace period for an empty lock file | Architect | would reintroduce the V3 wedge; not needed for V4 writers (N05) |
