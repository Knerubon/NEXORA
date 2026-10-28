# Unsupported and unproven guarantees

Nothing below is promised by the extractor, this package, or the cloud tests. "Cloud-tested only"
means exercised on Linux in the CI/cloud container (probes and mocks) and therefore **not** evidence
about Windows. Each item is carried into the evidence record's `not_verified` list until a real-half
record verifies it.

Round 4: both the custody gate and the evidence validator treat this list as illustrative, not as a
deny-list. They accept a verified guarantee only from an explicit allow-list
(`custody_gate.VERIFIABLE_GUARANTEES`), which is empty while DEP-02 is unresolved, so a guarantee
that is missing from this page is refused like one that is on it.

## Windows / filesystem

| Mechanism | What cannot be promised | Status |
|---|---|---|
| Atomic no-overwrite create (temp file + fsync + `os.link`) | Hard links need NTFS on a local volume. FAT/exFAT, many network shares, some ReFS/dedup/cloud-synced setups refuse or emulate them. Atomicity of the link step on Windows is unproven. | Linux-cloud-tested only |
| `O_NOFOLLOW` | Does not exist on Windows (`os.O_NOFOLLOW` absent). Refusal of symlinks must rely on `lstat`/`fstat` checks, which leave a check-to-open window. | Linux-cloud-tested only; Windows NOT VERIFIED |
| Symlinks | Creation needs privilege or Developer Mode; behaviour of the extractor when it is unavailable is untested on Windows. | NOT VERIFIED |
| Reparse points / junctions | A junction is a reparse point that `lstat` may not report as a symlink on all Python builds; it could redirect `--out`, chunk or lock paths outside the folder. No automated check exists in the cloud. | NOT VERIFIED (manual probe only) |
| `os.rename` onto existing file | Raises on Windows, replaces on POSIX. Any rename-based claim differs by platform. (The V3 rename-based lock reaper is gone at V4.) | Linux-cloud-tested only |
| Deleting/renaming a file with an open handle | Refused on Windows unless opened with delete-sharing; POSIX allows it. Cleanup and lock release can fail or be deferred. Antivirus/indexer handles make this intermittent. | NOT VERIFIED |
| File locking (V4, PR #96 `lock.py` at `d6a7cda7...`) | An OS advisory lock on a held descriptor: `flock` on POSIX, a one-byte `msvcrt.locking` range on Windows. Advisory only: it does not stop programs that do not take it. Nothing is ever reaped; an unverifiable or foreign owner is `writer_lock_held` (fail closed). Deleting `.writer.lock` from outside while a run holds it admits a second writer (PR #96 Reviewer A F6), so no procedure here removes a lock while a run could be active. The Windows `msvcrt` path is implemented but has never been executed. | Linux ext4 cloud-tested (PR #96); Windows NOT VERIFIED |
| Directory `fsync` | A no-op or unsupported on Windows; directory-entry durability after a crash is unproven. | NOT VERIFIED |
| Power-loss durability | No test removes power or kills the volume; fsync semantics of the storage stack and drive caches are not verified. | NOT VERIFIED |
| Read-only attribute / `chmod 0o444` | On Windows only toggles the read-only attribute; ACLs and administrators can still modify. Immutability is detection-by-hash, not enforcement. | NOT VERIFIED |
| Network shares, OneDrive/Dropbox/synced folders, BitLocker/dedup volumes | No guarantee of any atomicity, link, lock or rename semantics. Not recommended and not supported as a dataset location. | NOT SUPPORTED |
| Antivirus / indexer / backup agents | May open files between temp write and link, delay deletes, quarantine files, or add latency causing spurious `temp_file_collision`/delete failures. Product-specific; no matrix exists. | NOT VERIFIED |
| Path length, reserved names, case-insensitivity, 8.3 short names, trailing dots/spaces | Symbols or dataset names containing such patterns can alias. Case-insensitive volumes can collide `Chunk-*` and `chunk-*`. | NOT VERIFIED |
| Time of check vs time of use | A privileged local actor can swap files after a check. The design detects by hashes and the independent anchor, not by prevention. | By design; NOT a security boundary |

## Terminal / data

| Item | What cannot be promised |
|---|---|
| Tick dtype and fields | The layout in `vectors.py` is documentation, not observation; it may vary by build/server. |
| Determinism of the source | Two fetches of one window can differ (history sync, terminal updates). The extractor makes the **written file** deterministic, not the terminal. |
| Tick completeness | No claim that all ticks are present; gap counts are reports, not proof. |
| Broker clock truth | The offset/DST rule is a declared, evidence-referenced claim; a false but well-formed claim is not detectable from the data. |
| DST for single-segment conversions | No fold is derivable; the attestation is an unprovable claim. |
| Spring-forward gaps | Not modelled as a defect class by this package. |
| Read-only account | Operator attestation; the tool cannot see account permissions. |
| Other processes | The allowlist gate constrains this tool only, not other software using the terminal or account. |
| `MetaTrader5` package internals | Not audited; the gate limits what this code calls, not what the package does internally. |
| Independent store | V4 hash-chains the evidence store, which detects edits but not who appended an entry. Integrity, append-only behaviour and access control of the store are process controls, not checked here. |
| Custodian identity | Approvals carry a declared identity string; there is no signature (PR #96 Reviewer A F1). A store appender can forge an approval. Distinctness checks catch disguised duplicates of one declared name, not impersonation. |
| TRUSTED on Windows | At V4 `fsutil.PROVEN_PLATFORMS = {"linux"}`: a dataset extracted on Windows, or verified on Windows, cannot be TRUSTED. BLOCKED (DEP-01), not a defect of this package. |
| Request-bound clock interpretation | The extractor passes raw broker-clock milliseconds to `copy_ticks_range` as datetimes (Reviewer B I1). If the terminal interprets the bounds differently, every chunk is shifted and the half-open filter silently drops ticks. Case 1 now requires first/last `time_msc` inside the requested raw window. |

## What the cloud harness does and does not establish

Established (Linux, mocks, vectors): the vectors and oracle are self-consistent; the probes run and
record this platform's answers; mock terminal access can be recorded and shown to stay inside the
allowlist; harness code imports neither the extractor nor `MetaTrader5`.

Not established: anything about `nexora.market_history` itself (not importable from `main`), any real
terminal behaviour, any Windows/NTFS behaviour, any real data property.
