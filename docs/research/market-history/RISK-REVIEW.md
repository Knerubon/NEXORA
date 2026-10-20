# market_history — data-integrity fix wave: risk review (self-authored; independent review pending)

| Area | Before (#89 069894e) | After (this branch) | Residual risk / owner |
|---|---|---|---|
| Decimal text | `normalize()` rounded to ambient precision: 2345.67 -> 2346 at prec 4; dataset_id changed; verify blind to it | `decimal_str`/`exact_sub` use the Decimal's tuple and integers only; proven under precisions 1-50 and all 8 rounding modes, 200+ random values each | none known; `Decimal(repr(float))` in the MT5 source is exact |
| Memory | all ticks resident in extract and verify (~344 B/tick) | one chunk resident; quality and verify stream; `max_chunk_ticks` refuses oversize chunks; peak flat for 6x data (test) | peak is bounded by chunk size chosen by the operator; real-terminal chunk sizes untested |
| Atomicity | `open("xb")` direct: crash leaves truncated final file and blocks restart | temp + fsync + hard-link (no overwrite) + read-only; stale temps cleaned/reported; hard-kill tests (os._exit) before link and mid-fetch resume to the same dataset_id as a clean run | hard link needs a filesystem that supports it (NTFS and ext4 do; FAT/exFAT/some network shares do not: run fails closed with OSError) |
| Crash recovery | manual deletion needed | automatic; a final chunk that differs from a fresh fetch still stops the run (source changed or tampering) | by design |
| Manifest provenance | self-hash only; forged self-consistent manifest verified clean | optional external anchor `expected_manifest_sha256` detects it (test); CLI prints the anchor; runbook requires recording it outside the folder | anchor custody is a process control; no signature scheme |
| Hard defects | manifest + clean verify even with crossed/non-monotonic data; NaN aborted the run | non-finite quotes preserved and counted; `usability_blockers()` returns verification problems plus hard defects; CLI exit code reflects it | gap threshold stays a Quant decision (MD-6) |
| MT5 gate | allowlist had `initialize(any args)` and `symbol_select` | `initialize()` takes no arguments; `symbol_select` removed | in-process bypass via the private registry is inherent to Python (not a sandbox); review code, run unmodified |
| DST / clock | raw-keyed segments; repeated raw hour only visible as non_monotonic | same keying, now an explicit test: the fold is a blocking hard defect; runbook says split at the change | raw-keyed segments cannot disambiguate a repeated hour. A real fix needs the source to expose UTC or server-time metadata; **OPEN (Q-D01)** |
| Holdout | extractor not holdout-aware | `protected_utc_ranges` refused after offset conversion, segment by segment; CLI flag | the registry (PR #79) is not on main: caller must supply ranges; reading outside the tool is not prevented |
| Files | writable | chunks and manifest read-only | root/admin can still change them; anchor + verify cover that |

Not covered (needs the Windows node): `copy_ticks_range` window semantics and real field names on a live
terminal; large real-history chunk sizes; filesystem behaviour of the node.

Dukascopy: retrieval stays **BLOCKED** (PR #90): lawful access for automated retrieval and a verified
`.bi5` layout/time base are still missing. This branch does not touch it.
