# Integration matrix

Conventions. "Mock half" = test(s) in `tests/windows_integration_readiness/` (cloud). "Real half" =
the same steps run by the operator on the Windows node against the real extractor/terminal at a
**named SHA** (placeholder `<SHA>`), recorded with [EVIDENCE-TEMPLATE.md](EVIDENCE-TEMPLATE.md).
API names, flags and refusal codes are as read at PR #96 `d6a7cda72a88600e2ccf6554605f1c50478893ea`
(stacked on `e74ad035e7f21aa07528965da6ebe190c0512793`) and must be re-verified at `<SHA>`.
Everything marked NOT VERIFIED stays NOT VERIFIED until a real-half record says otherwise.

A case PASSES only if every pass criterion holds **and** the evidence fields are filled. Any ambiguous
or missing evidence is FAIL, never PASS; `evidence_validator.py` enforces this for the record.

**Real-half steps marked BLOCKED are not performed.** They need an interface no reviewed code offers
(DEP-03) or a TRUSTED verdict that is unreachable at V4 (DEP-01); see the step register in
[WINDOWS-EXECUTION-CHECKLIST.md](WINDOWS-EXECUTION-CHECKLIST.md) and
[DEPENDENCY-REGISTER.md](DEPENDENCY-REGISTER.md). The operator never writes code. Until those
dependencies resolve, no case can PASS and real-half readiness is not declared.

---

## Case 1 - `copy_ticks_range` return structure and numpy dtypes

Mock half: `test_case1_tick_layout.py` (layout checker, fake rows exposing `row["field"]` and
`row.dtype.names`, `None`-return model; numpy dtype test skips without numpy).

Real-half steps (read-only; operator-controlled, checklist phase C). **Steps 1-4 are BLOCKED
(DEP-03):** the extractor CLI neither exposes `dtype.descr` nor reports raw rows, and calling
`MetaTrader5` directly would be operator-written code. Kept as the specification for a reviewed driver:
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
recorded; the first and last `time_msc` lie inside the requested raw window (Reviewer B I1: the
extractor passes raw broker-clock bounds to `copy_ticks_range`, and a different interpretation by the
terminal would shift every chunk); any difference from the documented layout is written down as a finding, not silently
tolerated. Fail: missing required field, non-float64 quotes, extractor reads a field it did not
verify.

Evidence fields: `case1.descr`, `case1.names`, `case1.len`, `case1.empty_behaviour`,
`case1.last_error`, `case1.numpy_version`, `case1.terminal_build`,
`case1.first_last_time_msc_in_window`.

Does NOT prove: layout is stable across builds/symbols/servers; that `flags` is always present;
behaviour for large windows; anything about tick completeness or correctness of prices; how the
terminal interprets request bounds in general (`terminal_request_bound_clock_interpretation`).

## Case 2 - Decimal-preserving Bid/Ask normalization and quote validity

Mock half: `test_case2_decimal_quotes.py` (vectors, round-trip, ambient-context independence,
non-finite preserved, quote classification).

Real-half steps (**steps 1-2 BLOCKED, DEP-03**: no reviewed entry point replays vectors through the
extractor's normalization; step 3 uses the existing extraction command):
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
independent count. Extreme spreads are not a quality class (Reviewer B I2): no threshold is set here
(DEP-05, MD-6). Fail: any exponent form, context-dependent output, silent drop, or count mismatch.

Evidence fields: `case2.vector_results`, `case2.hostile_context_identical`,
`case2.quality_counts`, `case2.independent_counts`, `case2.counts_equal`.

Does NOT prove: that the broker's quotes are economically sensible; that float64 -> shortest repr
recovers the broker's original decimal for every symbol (digits/point not verified per symbol);
spread/price statistics (none are produced or endorsed).

## Case 3 - raw timestamp to UTC needs an independently documented broker clock rule

Mock half: `test_case3_time_conversion.py` (offset bound, `time`/`time_msc` agreement, no default offset, mandatory `evidence_ref`, half-open
segment edges, offset not inferable from ticks, overlap/gap refusal).

Real-half steps:
1. Obtain the broker clock rule **independently of the tick data** (broker documentation, server
   notice, written confirmation, or a paired-sample measurement report: raw `time_msc` of a live tick
   vs an NTP-synced UTC reference). Assign an `evidence_ref` (report/ticket id). **Never** infer it
   from the ticks being converted; **never** use a default such as +10800 s.
2. Write `time_conversion.json` with explicit raw-keyed segments covering the whole window.
3. Run `scripts/extract_market_history.py` with (a) the file, (b) the `evidence_ref` blanked, (c) a
   window starting before the first segment. (b) and (c) must refuse **before** any call into the
   terminal. At `d6a7cda7...` the script builds the conversion and runs `preflight` before it
   constructs `Mt5ReadOnlyHistorySource()`; that code order, re-verified at `<SHA>`, is the evidence.
   Observing `initialize` with a recording proxy around the real module is BLOCKED (DEP-03).

Expected: refusal codes `offset_evidence_required` for (b) and `time_conversion_does_not_cover_window`
for (c) (as read at `d6a7cda7...`; minor F5: the earlier `no_declared_offset_for_time` is the code
for a single raw time outside every segment, not for a window); conversion
`utc = raw - offset*1000` only inside a declared segment.

Pass: (b) and (c) refuse with no terminal contact; (a) converts a synthetic raw tick to the value the
second person computes by hand. Fail: any conversion without evidence, any default, any terminal
contact before validation.

Evidence fields: `case3.evidence_ref`, `case3.evidence_kind`, `case3.segments`,
`case3.refusals_observed`, `case3.terminal_contacted_before_validation` (must be false).

Does NOT prove: that the declared rule is **true**. The extractor can only make the claim explicit and
auditable. A false but well-formed declaration is undetectable from the data. Truth rests on the
independent evidence and its reviewer.

## Case 4 - DST fold and ambiguous-window rejection

Mock half: `test_case4_dst_fold.py` and `test_oracle_independence.py` (brute-force fold simulation;
empty/reversed/uncovered windows refused; splits honour attested ranges; derived fold ranges from a falling offset, both sides ambiguous,
rising offset derives none, refusal for touching windows, empty attestation list does not waive the
derived fold, split-around-fold windows, ordering cannot detect a fold).

Real-half steps:
1. From the broker's documented DST rule, list every fall-back transition inside the candidate window.
   Express each as a raw-keyed boundary with its offset drop.
2. **BLOCKED (DEP-03):** no CLI prints the extractor's derived fold ranges. Specification: build a
   conversion containing the falling boundary and compare the extractor's ranges with
   `[boundary - drop, boundary + drop)`, which `test_oracle_independence.py` re-derives by
   brute-force clock simulation.
3. With `scripts/extract_market_history.py`, preflight windows that (a) end inside, (b) start inside,
   (c) span, (d) avoid the range. (a)-(c) must refuse (`window_touches_derived_fold_range`, or
   `window_touches_ambiguous_raw_range` for an attested range, as read); (d) passes preflight and
   would then attach to the terminal, so it is run only as part of an authorized extraction.
4. Produce the split windows (before/after) and record them. Extract **only** the split windows, in a
   later, separately authorized step.

Pass: (a)-(c) refuse with no terminal contact, (d) accepted, derived ranges equal the independently
computed ranges. Fail: any touching window accepted, or an empty `ambiguous_raw_ranges` treated as
"no fold" when a falling offset exists.

Evidence fields: `case4.dst_rule_evidence_ref`, `case4.transitions`, `case4.derived_fold_ranges`,
`case4.independent_fold_ranges`, `case4.ranges_equal`, `case4.refusals_observed`,
`case4.split_windows`.

Does NOT prove: that the broker's real fold matches the derived range (it is derived from the declared
segments only); spring-forward gaps (a rising offset leaves a gap, not a fold: gap handling is a
data-quality finding, not covered); that a single-segment conversion has no fold (nothing is derivable;
the attestation is a claim, not a proof).

## Case 5 - chunk boundaries, duplicates, ordering, deterministic hashes

Mock half: `test_case5_chunks_hashes.py`, `test_duplicate_oracle.py` (golden vectors D1-D10) and
`test_execution_contract.py` (mock-only command contract, C1-C10). Covered: half-open contiguous
windows, short tail, boundary tick in exactly one chunk, out-of-window ticks dropped, same input ->
same bytes -> same hash, chunking invariance of the stream hash, per-chunk bytes differ, mutation
changes hash, duplicates kept and reported (including non-adjacent ones), disorder reported not
repaired, partial failure writes no manifest, mock output never becomes real evidence.

Real-half steps. **Steps 1-3 are BLOCKED (DEP-03):** the extractor has no mock-source injection,
and `TICKS_SYNTH` uses decimal text while a fake terminal row carries floats. The cloud half runs the
same contract with `execution_contract.run_mock` (synthetic, never real evidence). Step 4 uses the
existing extraction command on the real tiny window.
1. Run the extractor twice on a mock source with `TICKS_SYNTH` into two empty folders; compare chunk
   files byte-for-byte and `content_sha256`.
2. Re-run with a different `--chunk-ms` (1 h vs 30 min vs one chunk). `content_sha256` is defined as
   SHA-256 over the concatenated canonical tick bytes in order (as read), so it should be equal;
   per-chunk file hashes should differ. **This invariance is a harness expectation that must be
   confirmed at `<SHA>`; do not assume it.**
3. Confirm exact duplicates and same-timestamp-different-quote ticks are counted, not dropped, and
   disorder is counted, not reordered. Compare the counts with `duplicate_oracle.classify`
   ([ORACLE-SPEC.md](ORACLE-SPEC.md)). The extractor at `d6a7cda7...` counts against the previous tick
   only, so non-adjacent duplicates will differ (EXT-DEF-1): record the difference as a finding.
4. Re-run into the same folder: expect idempotent success; tamper one chunk byte on a copy and expect
   `chunk_exists_with_different_content`.

Pass: byte-identical across runs; hash relations as stated; counts equal `duplicate_oracle.classify`
(not the adjacency `order_report`, which restates the extractor).
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
Linux in the cloud). Twelve probes record what the platform does: hard link (true link: `nlink` 2
and same inode), symlink, `O_NOFOLLOW` presence, `O_EXCL`, rename-over-existing, unlink of an open
file, read-only attribute, directory `fsync`, case sensitivity, trailing dot/space aliasing,
reparse-point/junction (`manual_required` on Windows) and filesystem type (always
`manual_required`). Also mock-half tests for an emulated hard link, a probe error and an unsupported
hard link, each forcing `stop_required`.

Real-half steps (target volume = the exact volume that will hold the dataset, local NTFS; then
repeat on any share/synced folder the operator proposes, which is **not** recommended):
1. `python -m tests.windows_integration_readiness.filesystem_probes <empty dir on target volume>`;
   attach the JSON. Outcomes are `observed`, `unsupported`, `manual_required` (nothing was probed;
   attach evidence) or `error`. `stop_required` is true on any `error`, any unresolved
   `manual_required`, or an `unsupported` hard link; `verified_on_windows_ntfs` is always `false`.
2. Confirm filesystem type and volume (`fsutil fsinfo volumeinfo <drive>:`), that 8.3 and
   short-name behaviour are noted, and that real-time antivirus/indexer exclusions (or lack of them)
   are recorded.
3. Manual reparse probe: create a junction (`mklink /J`) and a symlink (needs privilege) pointing
   outside the output folder; ask the extractor to use each as `--out`, as a chunk path and as the
   lock path. Each must be refused or the finding recorded as UNSUPPORTED.
4. Crash probe: **BLOCKED (DEP-03)**, the extractor has no reviewed fault hook. Specification: a
   hook that raises after the temp file is written but before the link; confirm no final file,
   leftover `*.tmp-*`, clean re-run succeeds.
5. Lock probe (V4 semantics, `lock.py` at `d6a7cda7...`): start two runs on one scratch folder; the
   second must fail `writer_lock_held`. Nothing is ever reaped: after the first run exits or is
   killed, the OS releases its `msvcrt.locking`/`flock` lock and a new run may proceed. Never delete
   `.writer.lock` while any run could be active (PR #96 Reviewer A F6).

Expected / Windows facts to confirm, not assume: `os.O_NOFOLLOW` is absent so link refusal must come
from `lstat`/`fstat`; hard links require NTFS (fail on FAT/exFAT/ReFS variants and many shares);
`os.rename` onto an existing file raises on Windows (POSIX replaces); deleting a file with an open
handle is refused unless opened with delete-sharing; directory `fsync` is a no-op/unsupported;
symlinks need privilege; junctions are reparse points that `lstat` may not flag as symlinks.

Pass: every probe has a recorded outcome; unsupported outcomes map to an explicit refusal or a listed
unsupported guarantee; crash/lock scenarios behave as expected with the evidence attached. Fail: any
unrecorded probe, any probe `error`, an unresolved `manual_required`, a link/junction followed
silently, a final file left partial, or two writers proceeding on one folder.

Evidence fields: `case6.probe_json_ref`, `case6.fs_type`, `case6.volume_info_ref`,
`case6.av_indexer_state`, `case6.reparse_results`, `case6.crash_results`, `case6.lock_results`,
`case6.probe_error_count`, `case6.probe_manual_unresolved_count`.

Does NOT prove: durability after power loss (no hardware test); behaviour on network shares, OneDrive/
cloud-synced folders, BitLocker/dedup volumes, or with any specific antivirus product; atomicity on
filesystems other than the one probed. See [UNSUPPORTED-GUARANTEES.md](UNSUPPORTED-GUARANTEES.md).

## Case 7 - read-only API enforcement, cleanup, provenance, independent verification

Mock half: `test_case7_read_only_enforcement.py` (forbidden names refused and never reach the
module, allowlist has no trade/account word, full read path touches only allowed names,
`initialize` with no arguments, `shutdown` on success/exception and none needed on failed init,
immutable gate with no raw-module attribute, argument-recording proxy without credential values,
BaseException tripwires on every forbidden name, harness imports neither the extractor nor
`MetaTrader5`; network and `MetaTrader5` import blocked by the harness conftest).

Allowlist (as read at `e74ad035...`, unchanged at `d6a7cda7...`): `initialize` (no arguments), `shutdown`, `last_error`, `version`,
`terminal_info`, `symbol_info`, `copy_ticks_range`, `COPY_TICKS_ALL`. Everything else, notably
`login`, `order_send`, `order_check`, `order_calc_*`, `positions_*`, `orders_*`, `history_orders_get`,
`history_deals_get`, `account_info`, `market_book_*`, `symbol_select`, is refused.

Real-half steps:
1. **BLOCKED (DEP-03).** Specification: record every terminal name and call shape (positional types,
   keyword names; never credential values: `RecordingModuleProxy` now records these) during the tiny
   pre-registered window. Names must be a subset of the allowlist; `initialize` must take no
   arguments. Wrapping the real module outside the extractor gate would be operator-written code.
2. **BLOCKED (DEP-03), no reviewed fault injection.** Expected behaviour, reconciled (minor F6):
   `shutdown` is required after a successful `initialize` on both the success and the exception
   path. On a failed `initialize`, the extractor at `d6a7cda7...` raises inside
   `Mt5ReadOnlyHistorySource.__init__` before any close, and the harness model (`run_with_cleanup`)
   likewise does not call `shutdown`: that path is recorded, not required.
3. Provenance and custody (V4, [V4-CUSTODY-COMPATIBILITY.md](V4-CUSTODY-COMPATIBILITY.md)): the
   operator extracts with `--operator` and `--candidate-out`; an authorized custodian (not the
   operator) approves with `scripts/custody_market_history.py approve`; the store head is pinned
   through an independent channel at verification time.
4. Independent verification: a **second person**, on a **clean checkout of the same SHA** on a
   different session, runs `scripts/verify_market_history.py --dataset --store --registry
   --store-head SEQ:SHA256 --require-trusted`. A V3 `--anchor` alone never grants trust at V4.
   **TRUSTED is BLOCKED (DEP-01):** a dataset extracted or verified on Windows reports
   `platform_unproven:win32`, and the V4 custody baseline has open MAJOR findings. Record the actual
   output; the dataset stays `UNTRUSTED`.

Pass: observed names subset of allowlist; zero trade/account names; `shutdown` after every successful
`initialize`; the failed-`initialize` path recorded; custody evidence complete and the verifier
reporting `TRUSTED` with exit 0 (unreachable while DEP-01 is open). Fail: any non-allowlisted name,
any credential prompt, missing custody evidence, the operator approving or verifying, or `UNTRUSTED`
presented as trusted.

Evidence fields: `case7.observed_mt5_names`, `case7.initialize_args`, `case7.shutdown_paths`,
`case7.forbidden_names_observed`; custody and verification in the record's `custody.*` and
`people.*` sections.

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
`content_sha256`, `manifest_sha256`, candidate hash; custody approval `seq`/`entry_sha256`, pinned
store head and its channel; commands run verbatim; raw output references (file path + SHA-256), not
contents; result; reviewer; timestamps (UTC, with node clock source). `evidence_validator.py` rejects
a record that lacks any of these or contradicts itself.
