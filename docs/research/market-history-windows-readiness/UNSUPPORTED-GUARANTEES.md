# Unsupported and unproven guarantees

Nothing below is promised by the extractor, this package, or the cloud tests. "Cloud-tested only"
means exercised on Linux in the CI/cloud container (probes and mocks) and therefore **not** evidence
about Windows. Each item is carried into the evidence record's `not_verified` list until a real-half
record verifies it.

## Windows / filesystem

| Mechanism | What cannot be promised | Status |
|---|---|---|
| Atomic no-overwrite create (temp file + fsync + `os.link`) | Hard links need NTFS on a local volume. FAT/exFAT, many network shares, some ReFS/dedup/cloud-synced setups refuse or emulate them. Atomicity of the link step on Windows is unproven. | Linux-cloud-tested only |
| `O_NOFOLLOW` | Does not exist on Windows (`os.O_NOFOLLOW` absent). Refusal of symlinks must rely on `lstat`/`fstat` checks, which leave a check-to-open window. | Linux-cloud-tested only; Windows NOT VERIFIED |
| Symlinks | Creation needs privilege or Developer Mode; behaviour of the extractor when it is unavailable is untested on Windows. | NOT VERIFIED |
| Reparse points / junctions | A junction is a reparse point that `lstat` may not report as a symlink on all Python builds; it could redirect `--out`, chunk or lock paths outside the folder. No automated check exists in the cloud. | NOT VERIFIED (manual probe only) |
| `os.rename` onto existing file | Raises on Windows, replaces on POSIX. Stale-lock reaping and any rename-based claim differ by platform. | Linux-cloud-tested only |
| Deleting/renaming a file with an open handle | Refused on Windows unless opened with delete-sharing; POSIX allows it. Cleanup and lock release can fail or be deferred. Antivirus/indexer handles make this intermittent. | NOT VERIFIED |
| File locking | The writer lock is a cooperative `O_EXCL` file, not an OS lock. It does not stop other programs, other users, or a process that ignores it. Pid-liveness on Windows without `psutil` is undecidable and is treated as alive (fail closed), so a stale lock may need manual removal. | Linux-cloud-tested only |
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
| Independent store | Integrity and independence of the anchor store is the operator's responsibility and is not checked. |

## What the cloud harness does and does not establish

Established (Linux, mocks, vectors): the vectors and oracle are self-consistent; the probes run and
record this platform's answers; mock terminal access can be recorded and shown to stay inside the
allowlist; harness code imports neither the extractor nor `MetaTrader5`.

Not established: anything about `nexora.market_history` itself (not importable from `main`), any real
terminal behaviour, any Windows/NTFS behaviour, any real data property.
