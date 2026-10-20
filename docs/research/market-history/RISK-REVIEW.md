# market_history — data-integrity fix wave: risk review (self-authored; independent review pending)

| Area | Before (#89 069894e) | After (this branch) | Residual risk / owner |
|---|---|---|---|
| Decimal text | `normalize()` rounded to ambient precision: 2345.67 -> 2346 at prec 4; dataset_id changed; verify blind to it | `decimal_str`/`exact_sub` use the Decimal's tuple and integers only; proven under precisions 1-50 and all 8 rounding modes, 200+ random values each | none known; `Decimal(repr(float))` in the MT5 source is exact |
| Memory | all ticks resident in extract and verify (~344 B/tick) | one chunk resident; quality and verify stream; the provider receives `max_ticks` and refuses BEFORE converting rows (a 400k-tick window with limit 1000: peak < 1 MB, was 141 MB); extract keeps a backstop check | the terminal's own array is allocated before we can look at it (~44 B/tick); operator chooses `chunk_ms`; real-terminal sizes untested |
| Atomicity | `open("xb")` direct: crash leaves truncated final file and blocks restart | temp + fsync + hard-link (no overwrite) + read-only; stale temps cleaned/reported; hard-kill tests (os._exit) before link and mid-fetch resume to the same dataset_id as a clean run | hard link needs a filesystem that supports it (NTFS and ext4 do; FAT/exFAT/some network shares do not: run fails closed with OSError) |
| Crash recovery | manual deletion needed | automatic; a final chunk that differs from a fresh fetch still stops the run (source changed or tampering) | by design |
| Identity | `dataset_id` hashed retries and chunk layout | `dataset_id` = content identity (ticks, mapping, attestation, symbol, window); retries, layout, source version, terminal state and instrument metadata live in `manifest_sha256` only; `content_sha256` is chunk-independent | a changed instrument/swap value does not change the dataset id by design: compare `manifest_sha256` when metadata matters |
| Verification | raised on malformed manifest/chunks | `verify_dataset_report` never raises: structured `Problem(code, subject, detail)`; name, shape, symlink, row-type and encoding checks; 300-case random-corruption test | residual: catch-all `verify_internal_error` is defense-in-depth and not exercised by tests |
| Manifest provenance | self-hash only; forged self-consistent manifest verified clean | optional external anchor `expected_manifest_sha256` detects it (test); CLI prints the anchor; runbook requires recording it outside the folder | anchor custody is a process control; no signature scheme |
| Hard defects | manifest + clean verify even with crossed/non-monotonic data; NaN aborted the run | non-finite quotes preserved and counted; `usability_blockers()` returns verification problems plus hard defects; CLI exit code reflects it | gap threshold stays a Quant decision (MD-6) |
| MT5 gate | allowlist had `initialize(any args)` and `symbol_select` | `initialize()` takes no arguments; `symbol_select` removed | in-process bypass via the private registry is inherent to Python (not a sandbox); review code, run unmodified |
| DST / clock | raw-keyed segments; repeated raw hour only visible as non_monotonic, and NOT visible at all if the provider returns ticks ascending (review M1) | Detection by ordering is no longer relied on. Extraction requires a recorded `ClockAttestation` (evidence, attester, window, known ambiguous ranges), refuses fall-back offset boundaries and windows touching ambiguous ranges, stores it in the identity, and `verify_dataset` re-checks it | an attestation is a claim, not proof: a false one cannot be detected from the data. Real fix still needs a source-provided UTC/server-time signal; **OPEN (Q-D01)** |
| Holdout | extractor not holdout-aware; then fail-open on malformed ranges (review M2) | `protected_utc_ranges` is a REQUIRED argument (`()` = explicit none); strict validation (exact ints, lo < hi, no empty/inverted/float/bool/str) runs before any provider access and before the CLI attaches to the terminal; optional margin; recorded in the manifest and re-checked by `verify_dataset` | the registry (PR #79) is not on main: caller supplies ranges; protection depends on the declared offset (use the margin); reading outside the tool is not prevented |
| Files | writable | chunks and manifest read-only | root/admin can still change them; anchor + verify cover that |

Not covered (needs the Windows node): `copy_ticks_range` window semantics and real field names on a live
terminal; large real-history chunk sizes; filesystem behaviour of the node.

Dukascopy: retrieval stays **BLOCKED** (PR #90): lawful access for automated retrieval and a verified
`.bi5` layout/time base are still missing. This branch does not touch it.


## V3 corrective wave (PR #92 review findings F1-F7; self-authored, independent review pending)
| Finding | Fix | Residual risk |
|---|---|---|
| F1 `verify` skipped a directory/FIFO at a chunk path (returned no problem) | `_kind()` via `lstat`; `chunk_not_regular_file`, `chunk_is_symlink`, `manifest_is_symlink`, `manifest_not_regular_file`; reads use `O_NOFOLLOW`+`O_NONBLOCK`+`fstat` | none known on POSIX; Windows has no `O_NOFOLLOW` (lstat pre-check only, small TOCTOU window) |
| F2 repeated hour not enforced unless declared | `TimeConversion.derived_fold_ranges()` = `[prev_end - drop, next_start + drop)` per falling offset; refused by `check_attestation`, recorded in the manifest, recomputed by `verify` (`time_conversion_not_canonical`); attestation cannot waive it | **open:** one-segment conversion + false attestation is undetectable (Q-D01); a fold the operator never put into the conversion is invisible |
| F3 `dataset_id` ignored interpretation metadata | `dataset_id` binds source, `units` (declared time/price units + unit-defining instrument fields) in addition to content/time/attestation; `units_binding_mismatch` if tampered; `content_sha256` stays pure | swap/volume/stop-level metadata still excluded on purpose (covered by `manifest_sha256` and the anchor) |
| F7 anchor optional | `development_status` is always `UNTRUSTED`; `manifest_claims_trust` rejects self-granted trust; `dataset_status`/`require_trusted` need a valid, matching, externally stored anchor and no hard defects | "independently stored" is a process control: the tool only checks the anchor file is outside the dataset folder and names its `storage_ref` |
| F4 no writer ownership | O_EXCL `.writer.lock` (pid/host/token), dead-owner reaping by atomic rename, token-checked release, unique temp names, `writer_lock_present`/`unexpected_entry` in verify; two-process race test | foreign-host or unreadable locks need an operator; PID reuse can look alive (fails closed) |
| F5 symlinks | `out_dir_is_symlink`, `out_dir_not_a_directory`, dangling links at chunk/manifest names -> `HistoryError` | symlinked parent directories above `--out` are not inspected |
| F6 stress counts | injected defects built non-interacting; dup / same-ts / non-monotonic / crossed / gap / non-positive asserted `==` for 3 seeds | gap threshold remains Quant MD-6 |
| Fuzz | re-sealed structure-aware mutants (1500): never `verify_internal_error`, never TRUSTED against an anchor; stale-id edits to identity fields always detected. Found and fixed: unvalidated window order/coverage, non-int segment values, unvalidated `quality`, non-mapping anchors | a competent forger who recomputes every self-hash passes internal checks by construction: the anchor is the control (asserted in the test) |


## V4 corrective wave (PR #93 follow-up; self-authored by the same lineage as earlier reviewers; independent review pending)
Full threat model, platform matrix and residual risks: `THREAT-MODEL-V4.md`.

| Area | V3 weakness (reproduced on e74ad03) | V4 change | Residual risk |
|---|---|---|---|
| Lock: stale read, then live owner's NEW lock | reaper read "dead", live owner replaced the file, reaper's rename-by-name removed the live lock (R1); A and B then both owned the folder (R2) | no reaping at all: ownership = OS advisory lock on a held descriptor; unlink only while holding the lock and only the inode held; waiter re-checks the inode after locking | network/FUSE filesystems and Windows are not proven; V3 and V4 writers must not share a folder |
| Lock: empty file (crash between create and write) | wedged forever (R3) | empty = free (the OS proves no holder); partial/garbage/foreign-host/V3 record = fail closed (`writer_lock_held`, operator acts) | an operator must clear a V3-format or corrupt record |
| Lock: PID reuse / foreign host | pid liveness heuristic | pid/host are informational only; foreign-host record fails closed | none known on POSIX |
| Filesystem: symlinked parents | only the final component was checked | every ancestor opened `O_NOFOLLOW` from `/`; later I/O via `dir_fd`; verify pins the dataset directory the same way | no `dir_fd`/`O_NOFOLLOW` on Windows: lstat + reparse-point checks with a check-then-use window, so TRUSTED is blocked |
| Filesystem: hard links / immutability | not checked | chunks/manifest/store entries must have link count 1; read-only mode; write-once via no-overwrite link | file mode is advisory (root/admin; copies lose bits): the manifest hash + custody approval are the control |
| Filesystem capabilities | assumed | probe (dir_fd, no-follow, hard link no-overwrite, link count, lock exclusion, dir fsync, fs class) recorded in the manifest; `dataset_status` re-derives the verdict and refuses TRUSTED when unproven; verifier platform also checked | the probe is an extractor claim; a hostile extractor can lie (custody approval is the control) |
| Anchor custody | the extractor could store its own anchor; a matching hash granted TRUSTED | candidate (extractor) -> approval by an independent authorized custodian in an append-only hash-chained store; registry from the verifier; self-approval refused; binding to dataset/content/manifest/symbol/window; revocation; head pin | authenticity of declared identities, registry upkeep, store immutability and head-pin custody are process controls; no signatures |
| V3 compatibility | n/a | V3 datasets verify (integrity) but are never TRUSTED; V3 anchors never grant trust; schema `/4` | re-extract to migrate |

Test changes to existing suites (reviewed for meaning, not weakened): the V3 tests that encoded the
reaper and "matching anchor => TRUSTED" were rewritten to the V4 semantics (V3-style/legacy locks fail
closed; V4 records of dead owners are free by OS state; hash-only anchor => UNTRUSTED); the two
`os.link` fault-injection tests now ignore the filesystem probe's own links; the sample manifest was
regenerated.
