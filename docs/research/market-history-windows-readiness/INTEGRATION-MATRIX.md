# Integration matrix

Conventions. "Mock half" = test(s) in `tests/windows_integration_readiness/` (cloud). "Real half" =
the same steps run by the operator on the Windows node against the real extractor/terminal at a
**named SHA** (placeholder `<SHA>`), recorded with [EVIDENCE-TEMPLATE.md](EVIDENCE-TEMPLATE.md).
API names are as read at `e74ad035e7f21aa07528965da6ebe190c0512793` and must be re-verified at `<SHA>`.
Everything marked NOT VERIFIED stays NOT VERIFIED until a real-half record says otherwise.

A case PASSES only if every pass criterion holds **and** the evidence fields are filled. Any ambiguous
or missing evidence is FAIL, never PASS.

---

## Case 1 - `copy_ticks_range` return structure and numpy dtypes

Mock half: `test_case1_tick_layout.py` (layout checker, fake rows exposing `row["field"]` and
`row.dtype.names`, `None`-return model; numpy dtype test skips without numpy).

Real-half steps (read-only; operator-controlled, checklist phase C):
1. Through the read-only gate, call `copy_ticks_range(<SYMBOL>, from, to, COPY_TICKS_ALL)` for a
   **tiny pre-registered, non-protected, non-fold** window (at most a few seconds).
2. Record `type(rows)`, `len(rows)`, `rows.dtype.descr`, `rows.dtype.names`, and the first and last
   `time_msc`. Do not store the rows themselves in evidence.
3. Run `check_tick_layout(rows.dtype.descr, ("time_msc","bid","ask"))` (harness
   `reference_model.py`) and attach its output.
4. Repeat with a window that returns `None`/empty (e.g. a weekend second) and record the behaviour and
   `last_error()`.

Expected: documented layout `time:i8, bid:f8, ask:f8, last:f8, volume:u8, time_msc:i8, flags:u4,
volume_real:f8` (vectors.py, **NOT VERIFIED**: taken from public documentation, not observed).

Pass: required fields present; `time_msc` 64-bit int; `bid`/`ask` float64; empty/`None` behaviour
recorded; any difference from the documented layout is written down as a finding, not silently
tolerated. Fail: missing required field, non-float64 quotes, extractor reads a field it did not
verify.

Evidence fields: `case1.descr`, `case1.names`, `case1.len`, `case1.empty_behaviour`,
`case1.last_error`, `case1.numpy_version`, `case1.terminal_build`.

Does NOT prove: layout is stable across builds/symbols/servers; that `flags` is always present;
behaviour for large windows; anything about tick completeness or correctness of prices.

## Case 2 - Decimal-preserving Bid/Ask normalization and quote validity

Mock half: `test_case2_decimal_quotes.py` (vectors, round-trip, ambient-context independence,
non-finite preserved, quote classification).

Real-half steps:
1. Feed the **synthetic** `DECIMAL_TEXT_VECTORS` and `QUOTE_VECTORS` (vectors.py) to the extractor's
   normalization at `<SHA>` (as read: `Decimal(repr(float(x)))` then canonical text; spread by exact
   decimal subtraction) and compare outputs to the expected text.
2. Repeat inside a deliberately hostile decimal context (`prec=3`, narrow `Emin/Emax`, `ROUND_DOWN`);
   outputs must be byte-identical.
3. On the tiny case-1 window, count nonfinite / nonpositive / crossed / zero-spread quotes reported by
   the extractor's quality report and compare to an independent count made by the second person.

Expected: text equals vectors; no exponent; no trailing zeros; `-0.0 -> "0"`; NaN/Inf **preserved**
(counted as a hard defect, not dropped, not an abort); crossed (`ask < bid`) and nonpositive counted
as hard defects.

Pass: all vectors identical in default and hostile context; counts equal between extractor and
independent count. Fail: any exponent form, context-dependent output, silent drop, or count mismatch.

Evidence fields: `case2.vector_results`, `case2.hostile_context_identical`,
`case2.quality_counts`, `case2.independent_counts`.

Does NOT prove: that the broker's quotes are economically sensible; that float64 -> shortest repr
recovers the broker's original decimal for every symbol (digits/point not verified per symbol);
spread/price statistics (none are produced or endorsed).

## Case 3 - raw timestamp to UTC needs an independently documented broker clock rule

Mock half: `test_case3_time_conversion.py` (no default offset, mandatory `evidence_ref`, half-open
segment edges, offset not inferable from ticks, overlap/gap refusal).

Real-half steps:
1. Obtain the broker clock rule **independently of the tick data** (broker documentation, server
   notice, written confirmation, or a paired-sample measurement report: raw `time_msc` of a live tick
   vs an NTP-synced UTC reference). Assign an `evidence_ref` (report/ticket id). **Never** infer it
   from the ticks being converted; **never** use a default such as +10800 s.
2. Write `time_conversion.json` with explicit raw-keyed segments covering the whole window.
3. Run the extractor's preflight with (a) the file, (b) the `evidence_ref` blanked, (c) a window
   starting before the first segment. (b) and (c) must refuse **before** any call into the terminal
   (record that `initialize` was not called, using the recording proxy).

Expected: refusal codes `offset_evidence_required` and `no_declared_offset_for_time` (as read; verify
names at `<SHA>`); conversion `utc = raw - offset*1000` only inside a declared segment.

Pass: (b) and (c) refuse with no terminal contact; (a) converts a synthetic raw tick to the value the
second person computes by hand. Fail: any conversion without evidence, any default, any terminal
contact before validation.

Evidence fields: `case3.evidence_ref`, `case3.evidence_kind`, `case3.segments`,
`case3.refusals_observed`, `case3.terminal_contacted_before_validation` (must be false).

Does NOT prove: that the declared rule is **true**. The extractor can only make the claim explicit and
auditable. A false but well-formed declaration is undetectable from the data. Truth rests on the
independent evidence and its reviewer.

## Case 4 - DST fold and ambiguous-window rejection

Mock half: `test_case4_dst_fold.py` (derived fold ranges from a falling offset, both sides ambiguous,
rising offset derives none, refusal for touching windows, empty attestation list does not waive the
derived fold, split-around-fold windows, ordering cannot detect a fold).

Real-half steps:
1. From the broker's documented DST rule, list every fall-back transition inside the candidate window.
   Express each as a raw-keyed boundary with its offset drop.
2. Build a conversion containing the falling segment boundary; read `derived_fold_ranges` from the
   extractor and compare with `EXPECTED_FOLD_FALLBACK` logic (`[boundary_end - drop, boundary_start +
   drop)`).
3. Ask the extractor to preflight windows that (a) end inside, (b) start inside, (c) span, (d) avoid
   the range. (a)-(c) must refuse (`window_touches_derived_fold_range` as read); (d) must pass.
4. Produce the split windows (before/after) and record them. Extract **only** the split windows, in a
   later, separately authorized step.

Pass: (a)-(c) refuse with no terminal contact, (d) accepted, derived ranges equal the independently
computed ranges. Fail: any touching window accepted, or an empty `ambiguous_raw_ranges` treated as
"no fold" when a falling offset exists.

Evidence fields: `case4.dst_rule_evidence_ref`, `case4.transitions`, `case4.derived_fold_ranges`,
`case4.independent_fold_ranges`, `case4.refusals_observed`, `case4.split_windows`.

Does NOT prove: that the broker's real fold matches the derived range (it is derived from the declared
segments only); spring-forward gaps (a rising offset leaves a gap, not a fold: gap handling is a
data-quality finding, not covered); that a single-segment conversion has no fold (nothing is derivable;
the attestation is a claim, not a proof).

## Case 5 - chunk boundaries, duplicates, ordering, deterministic hashes

Mock half: `test_case5_chunks_hashes.py` (half-open contiguous windows, short tail, boundary tick in
exactly one chunk, out-of-window ticks dropped, same input -> same bytes -> same hash, chunking
invariance of the stream hash, per-chunk bytes differ, mutation changes hash, duplicates kept and
reported, disorder reported not repaired).

Real-half steps (on the **synthetic** source only first, then the real tiny window):
1. Run the extractor twice on a mock source with `TICKS_SYNTH` into two empty folders; compare chunk
   files byte-for-byte and `content_sha256`.
2. Re-run with a different `--chunk-ms` (1 h vs 30 min vs one chunk). `content_sha256` is defined as
   SHA-256 over the concatenated canonical tick bytes in order (as read), so it should be equal;
   per-chunk file hashes should differ. **This invariance is a harness expectation that must be
   confirmed at `<SHA>`; do not assume it.**
3. Confirm exact duplicates and same-timestamp-different-quote ticks are counted, not dropped, and
   disorder is counted, not reordered.
4. Re-run into the same folder: expect idempotent success; tamper one chunk byte on a copy and expect
   `chunk_exists_with_different_content`.

Pass: byte-identical across runs; hash relations as stated; counts equal the harness `order_report`.
Fail: any nondeterminism (dict order, locale, timestamp in chunk bytes), boundary tick duplicated or
lost, duplicates dropped silently.

Evidence fields: `case5.run_a_content_sha256`, `case5.run_b_content_sha256`,
`case5.chunk_ms_variants`, `case5.variant_content_sha256`, `case5.order_counts`,
`case5.tamper_refusal`.

Does NOT prove: determinism of the **terminal** (two real fetches of the same window can differ if
the terminal's history changes or syncs between calls); that terminal timestamps are strictly
ordered; completeness of ticks.

## Case 6 - Windows file lock, hard link, symlink/reparse point, crash recovery

Mock half: `test_case6_filesystem.py` plus `filesystem_probes.py` (runs on the **current** platform:
Linux in the cloud). Ten probes record what the platform does: hard link, symlink, `O_NOFOLLOW`
presence, `O_EXCL`, rename-over-existing, unlink of an open file, read-only attribute, directory
`fsync`, case sensitivity, reparse-point/junction (manual on Windows).

Real-half steps (target volume = the exact volume that will hold the dataset, local NTFS; then
repeat on any share/synced folder the operator proposes, which is **not** recommended):
1. `python -m tests.windows_integration_readiness.filesystem_probes <empty dir on target volume>`;
   attach the JSON.
2. Confirm filesystem type and volume (`fsutil fsinfo volumeinfo <drive>:`), that 8.3 and
   short-name behaviour are noted, and that real-time antivirus/indexer exclusions (or lack of them)
   are recorded.
3. Manual reparse probe: create a junction (`mklink /J`) and a symlink (needs privilege) pointing
   outside the output folder; ask the extractor to use each as `--out`, as a chunk path and as the
   lock path. Each must be refused or the finding recorded as UNSUPPORTED.
4. Crash probe: start a run against a **mock** source with a hook that raises after the temp file is
   written but before the link; confirm no final file, leftover `*.tmp-*`, clean re-run succeeds. Then
   kill the process hard (Task Manager) mid-write; repeat.
5. Lock probe: start two runs on one folder; second must fail `writer_lock_held`. Kill the first;
   confirm the stale lock is only reaped when the owner is provably dead on the same host, and that on
   Windows an undecidable pid liveness is treated as alive (fail closed).

Expected / Windows facts to confirm, not assume: `os.O_NOFOLLOW` is absent so link refusal must come
from `lstat`/`fstat`; hard links require NTFS (fail on FAT/exFAT/ReFS variants and many shares);
`os.rename` onto an existing file raises on Windows (POSIX replaces); deleting a file with an open
handle is refused unless opened with delete-sharing; directory `fsync` is a no-op/unsupported;
symlinks need privilege; junctions are reparse points that `lstat` may not flag as symlinks.

Pass: every probe has a recorded outcome; unsupported outcomes map to an explicit refusal or a listed
unsupported guarantee; crash/lock scenarios behave as expected with the evidence attached. Fail: any
unrecorded probe, a link/junction followed silently, a final file left partial, or a lock reaped for a
live/foreign owner.

Evidence fields: `case6.probe_json_ref`, `case6.fs_type`, `case6.volume_info_ref`,
`case6.av_indexer_state`, `case6.reparse_results`, `case6.crash_results`, `case6.lock_results`.

Does NOT prove: durability after power loss (no hardware test); behaviour on network shares, OneDrive/
cloud-synced folders, BitLocker/dedup volumes, or with any specific antivirus product; atomicity on
filesystems other than the one probed. See [UNSUPPORTED-GUARANTEES.md](UNSUPPORTED-GUARANTEES.md).

## Case 7 - read-only API enforcement, cleanup, provenance, independent verification

Mock half: `test_case7_read_only_enforcement.py` (forbidden names refused and never reach the
module, allowlist has no trade/account word, full read path touches only allowed names,
`initialize` with no arguments, `shutdown` on success/exception/failed init, immutable gate,
recording proxy, tripwires, harness imports neither the extractor nor `MetaTrader5`).

Allowlist (as read): `initialize` (no arguments), `shutdown`, `last_error`, `version`,
`terminal_info`, `symbol_info`, `copy_ticks_range`, `COPY_TICKS_ALL`. Everything else, notably
`login`, `order_send`, `order_check`, `order_calc_*`, `positions_*`, `orders_*`, `history_orders_get`,
`history_deals_get`, `account_info`, `market_book_*`, `symbol_select`, is refused.

Real-half steps:
1. Wrap the real `MetaTrader5` module in `RecordingModuleProxy` (fakes.py) **outside** the extractor
   gate, run the tiny pre-registered window, and attach `proxy.names`. It must be a subset of the
   allowlist; `initialize` must show no arguments (attach to the running, operator-logged-in terminal).
2. Confirm `shutdown` is called on success, on a forced exception, and on a failed `initialize`
   (documented expectation: record what the extractor does; do not assume).
3. Provenance: record exactly the fields in [EVIDENCE-TEMPLATE.md](EVIDENCE-TEMPLATE.md)
   (`dataset_id`, `content_sha256`, `manifest_sha256`, anchor, source descriptor, terminal build, SHA,
   operator, pre-registration reference) and store the anchor **independently** of the data.
4. Independent verification: a **second person**, on a **clean checkout of the same SHA** on a
   different session, runs the verifier with the anchor fetched from the independent store and
   `--require-trusted`. Record their identity, SHA, command, exit code and result. The first operator
   must not run this step.

Pass: observed names subset of allowlist; zero trade/account names; shutdown observed on all three
paths; verifier exit 0 with `TRUSTED` from the second person; provenance complete. Fail: any
non-allowlisted name observed, any credential prompt, missing anchor, same person verifying, or
`UNTRUSTED` presented as trusted.

Evidence fields: `case7.observed_mt5_names`, `case7.initialize_args`, `case7.shutdown_paths`,
`case7.anchor_ref`, `case7.verifier_person`, `case7.verifier_sha`, `case7.verifier_exit_code`,
`case7.verifier_status`.

Does NOT prove: that no other process on the node uses the terminal/account for trading (the gate
constrains only this tool); that the account is investor/demo (operator attests; not machine-checked);
that the `MetaTrader5` package itself makes no hidden calls; integrity of the independent store.

---

## Cross-case: provenance requirements for the future real run

A real-data record is rejected unless it contains **all** of: exact full 40-character SHA (and
`git status --short` empty, `git rev-parse HEAD` equal); node hostname class, OS build and filesystem
type; terminal company/name/build and server name as shown in the terminal (no account number or
balance); package versions (`MetaTrader5`, `numpy`, Python, `uv`); operator and second-person
identities; window raw bounds, UTC bounds, **pre-registration reference timestamped before any data
was read**; protected-range declaration (or an explicit `--no-protected-ranges` with reason);
time-conversion file hash and `evidence_ref`; clock attestation file hash; `dataset_id`,
`content_sha256`, `manifest_sha256`; anchor location; commands run verbatim; raw output references
(file path + SHA-256), not contents; result; reviewer; timestamps (UTC, with node clock source).
