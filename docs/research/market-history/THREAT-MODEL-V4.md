# Market-history threat model (V4)

Status: self-authored by the same lineage as the earlier reviewers; **independent review pending**.
Scope: `nexora.market_history` datasets and their trust decision. Not covered: MT5/terminal behaviour
(WINDOWS-INTEGRATION), broker data terms, Dukascopy (BLOCKED), anything touching PROD.

## 1. Assets
| Asset | Why it matters |
|---|---|
| Dataset content (chunks, `content_sha256`) | the evidence for validation gates G1-G5 and any baseline freeze |
| Manifest (`manifest_sha256`, `dataset_id`) | binds meaning (time mapping, units, attestation, source) to content |
| Trust decision (`TRUSTED`) | consumers (`require_trusted`) treat the data as evidence |
| Custody evidence (candidate, approvals, registry, store head) | the only thing that can lift `UNTRUSTED` |
| Writer ownership of an output folder | prevents two writers interleaving into one dataset |
| Protected (holdout) ranges | reading them is itself an access |

## 2. Actors
| Actor | Assumed capability |
|---|---|
| Honest operator on a mis-configured machine | runs on exFAT/network share/Windows; two terminals by accident |
| Rogue or careless extractor/operator | controls the extraction host, the dataset folder, can forge a manifest and recompute every self-hash, can claim any identity string |
| Local attacker with write access to the dataset folder | rename/replace/symlink/hard-link entries, swap directories mid-run, edit lock files |
| Rogue writer of the evidence store | can append arbitrary entries; cannot (by the store contract) rewrite or delete existing ones |
| Attacker who can rewrite the whole store | delete the tail or replace the chain (only possible if the store is not truly immutable) |
| Authorized custodian | trusted by definition: a malicious custodian can approve bad data (process control) |
| Root/administrator on the verifier | out of scope (can alter any check) |

## 3. Trust boundaries
1. **Dataset folder** is untrusted input: everything in it, including its manifest, probe record,
   operator string and any "anchor", is a claim.
2. **Verifier-supplied evidence** (store handle, registry, pinned head) is outside the dataset and is the
   only source of trust. The dataset can never carry its own registry, store or anchor
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
| C2 | Unauthorized approver | registry allowlist (with validity window); refused by the tool and re-checked by the verifier | M | `test_unauthorized_approver...`, `test_custodian_validity_window...` |
| C3 | Self-approval (operator or candidate creator, any spelling or registry alias) | normalized identity comparison (NFKC, casefold, whitespace) over id + aliases, against the candidate creator **and** the manifest's recorded operator; missing operator = untrusted | M | `test_self_approval...`, `test_alias_of_the_approver...`, `test_candidate_creator...`, `test_a_dataset_without_a_recorded_operator...` |
| C4 | A *different* spelling or a second account of the same human | not detectable beyond registry aliases | P | registry maintenance |
| C5 | Forged / edited approval entry | entry hash + prev-link chain; any edit or reseal breaks the next link | M | `test_editing_a_stored_entry...`, `test_resealing_a_replaced_first_entry...` |
| C6 | Replay of a valid approval for another dataset or manifest | approval binds dataset_id + content + manifest hash + symbol + window; candidate bindings re-checked | M | `test_approval_for_one_dataset_cannot_be_replayed...`, `test_approval_is_bound_to_the_exact_manifest...`, `test_a_candidate_for_another_dataset...` |
| C7 | Forged + re-sealed manifest (all self-hashes recomputed) | manifest hash no longer equals the approved one (same id) or no approval exists (new id) | M | same as C6, V3 fuzz tests |
| C8 | Approval timing games (before retrieval, before candidate, future) | ordering and skew checks | M | `test_approval_time...`, `test_approval_before_the_candidate...` |
| C9 | Revocation | any later `revoke` entry (even by an unauthorized writer) denies trust; a later valid approval supersedes | M (fail-closed direction) | `test_revocation_withdraws_trust...` |
| C10 | Tail truncation / rollback / whole-chain replacement of the store | detectable **only** with a head pinned from an independent channel (`--store-head`); otherwise invisible | M with pin, P without | `test_whole_chain_replacement_and_tail_truncation...` (documents the gap) |
| C11 | Store or registry shipped inside the dataset | refused (`store_inside_dataset`, `registry_inside_dataset`) | M | tests in the custody file |
| C12 | Malicious custodian | by definition trusted | P | separation of duties, review |
| C13 | Store not actually append-only | `LocalAppendOnlyStore` refuses overwrite of an existing sequence number but cannot stop a user with directory write access | P | use a store with real immutability |
| C14 | Unavailable/corrupt evidence | `require_trusted` raises `dataset_untrusted`; `dataset_status` never raises | F | `test_fail_closed_when_custody_cannot...` |
| V3 | V3 datasets/anchors | still verify (integrity) but never TRUSTED; V3 anchor never grants trust | M | `test_v3_datasets_still_verify...`, V3 suite |

## 5. What is machine-enforced vs process control
Machine-enforced: everything marked M/E/F above, specifically the hash chain, bindings, registry
membership and validity window, identity independence by normalized declared identity, timestamp
ordering, capability re-derivation, fail-closed behaviour on any unreadable or malformed evidence, and
all locking/filesystem guarantees on a proven configuration.

Process control (not provable by this code): truthfulness of identity strings; registry membership and
upkeep; independence of the custodian as a *person*; immutability of the real evidence store; the
independent channel for the head pin; honesty of the extractor's probe record; operator discipline
about mixing V3 and V4 writers.

There is **no signature scheme and no secret**. Adding asymmetric signatures would need a locked
dependency (none is present) and a key-custody decision; HMAC with a shared secret would not give
separation of duties. The trust root is therefore (a) the store's access control and immutability and
(b) the registry file the verifier obtains outside the dataset.

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
2. Store immutability, registry upkeep, head-pin channel and custodian independence are process
   controls; a compromised custodian or store defeats the design.
3. Declared identities are strings; two spellings of one person are only caught through registry aliases.
4. An attestation, the DST/offset evidence and the probe record are claims (unchanged from V3, Q-D01).
5. V3 and V4 writers on the same folder can collide (V3 takes no OS lock).
6. Mode bits are advisory; `root`/admin can alter files, which the hashes and the custody approval then expose.
7. Verification of a dataset *during* extraction can report transient `chunk_multiple_links` (the link
   count is 2 between link and temp unlink); the lock-present problem is reported at the same time.
8. Native-library or kernel bugs in `flock`/`openat` semantics are assumed absent.
