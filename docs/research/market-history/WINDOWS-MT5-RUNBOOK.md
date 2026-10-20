# Windows MT5 read-only extraction — operator runbook (WINDOWS-INTEGRATION)

Class: WINDOWS-INTEGRATION. Operator-started only, against a **named SHA**, on the Windows MT5 node.
Not automatic, not a cloud step, not PRODUCTION-ONLY. The tool reads history; it never submits,
modifies or closes orders, never starts or stops NEXORA PROD, and never reads PROD journals/DSNs.

## 0. Before you start (all must be true, otherwise STOP)
- You hold a written go-ahead for this extraction and the named commit SHA (record it).
- The code on the node is the reviewed SHA of branch `claude/market-history-fix-v1` (or its merged
  successor). Independent review of that exact head is recorded in the PR. Do not run unreviewed code.
- Use a **disposable checkout** (not the PROD worktree): `git clone` + `git checkout <SHA>`;
  `git rev-parse HEAD` equals the SHA; `git status --short` is empty.
- Prefer a terminal logged in with an **investor (read-only) password** or a demo account. Never put a
  password in a command line, file, ticket or log. The tool takes no credentials; it attaches to the
  already-running, already-logged-in terminal.
- Do NOT set `NEXORA_MT5_*`, `NEXORA_POSTGRES_DSN` or any production variable in this shell.
- The date window is decided and written down **before** looking at any data, and does not overlap any
  locked/holdout range (see section 6).

## 1. Install
```powershell
python -m pip install uv==0.12.15
uv sync --locked --extra mt5          # MetaTrader5 wheel is win32-only
uv run --no-sync python -c "import MetaTrader5 as m; print(m.__version__)"
```

## 2. Record the terminal facts (no secrets)
Terminal build, company/name as shown by Help > About, server name as shown in the title bar, and
the local timezone of the node. Do not record account number or balance.

## 3. Investigate the broker-clock offset (do NOT assume +10800 s)
Stored ticks cannot reveal their own offset. Collect paired samples while connected: raw broker
`time_msc` of the latest tick versus an independent UTC reference read at the same moment (e.g.
`w32tm`/NTP-synced UTC), many samples per day on several days spanning a DST change if one occurs in
the window. Feed them to `nexora.market_history.offset.summarize_offsets` (snap and minimum samples
are your choice, write them down). Interpret:
- `single_offset_observed_in_sample_window_only`: valid for the sampled days only.
- `offset_varies_investigate_dst_or_effective_dates`: split the window; each segment needs its own
  evidence.
Write `time_conversion.json`: `{"segments":[{"from_raw_msc":..,"to_raw_msc":..,"offset_seconds":..,
"basis":"operator_declared","evidence_ref":"<report id / ticket>"}]}`. Segments are in RAW time and must
cover the whole window; extraction refuses otherwise. A segment boundary where the offset FALLS (DST fall-back) makes raw time repeat, and a
raw-keyed mapping cannot represent that. The tool DERIVES the ambiguous raw range from the segments
(`[end of earlier segment - drop, start of later segment + drop)`, both sides because a raw-keyed
boundary cannot say which side repeats) and refuses any window touching it. No attestation can waive
this: split the extraction into windows that stop before and start after that range.

### 3b. Clock attestation (mandatory, fail closed)
A range query on raw time cannot separate the two passes of a repeated hour, and a provider that
returns ticks in ascending order hides the repeat completely (nothing in the data shows it). So the
tool does not try to detect it; it refuses to run without a recorded claim. Write
`clock_attestation.json`:
`{"claim": "<exact text from nexora.market_history.clock.CLAIM>", "evidence_ref": "<report/ticket>",
"attested_by": "<name>", "window_from_raw_msc": A, "window_to_raw_msc": B,
"ambiguous_raw_ranges": [[lo, hi], ...]}`.
- The attested window must cover the extraction window; `ambiguous_raw_ranges` lists known repeated
  hours (from the broker's DST rules) and the extraction window must not touch them: choose windows
  that stop before and start after the repeated hour.
- Fold ranges derived from the conversion are enforced in addition to `ambiguous_raw_ranges`; an empty
  `ambiguous_raw_ranges` does not mean "no fold" when the conversion contains a falling offset.
- The attestation is stored in the manifest and in the dataset identity; `verify_dataset` re-checks it
  against the conversion. It is **not proof**: with a single-segment conversion nothing can be derived,
  so a false attestation cannot be detected from the data.
  The evidence must show the broker's clock rule for the window (not just "I looked at the chart").

## 4. Run
```powershell
uv run --no-sync python scripts/extract_market_history.py `
  --symbol <SYMBOL AS SHOWN IN MARKET WATCH> --from-raw-msc <A> --to-raw-msc <B> `
  --chunk-ms 3600000 --max-chunk-ticks 1000000 --max-gap-ms <Quant value, unset = do not run> `
  --time-conversion time_conversion.json --clock-attestation clock_attestation.json `
  (--protected-utc-ranges ranges.json | --no-protected-ranges) --protected-margin-ms <ms> `
  --out D:\nexora-data\<dataset-name>
```
- Every input above is validated BEFORE the tool attaches to the terminal; a bad range, attestation or
  conversion exits without touching MT5.
- Choose `--chunk-ms` so one chunk is far below `--max-chunk-ticks`. A chunk over the limit is refused
  before tick objects are built, but the terminal's own array for the window is already allocated
  (about 8x smaller than the Python objects) and cannot be bounded by this tool.
- Output lives outside Git. Do not commit raw chunks (size, and broker data terms).
- Exit code 0 only if verification is clean **and** there are no usability blockers.
- Only ONE extraction may own an output folder: a `.writer.lock` file (pid/host/token, `O_EXCL`) is
  taken first. A second run fails with `writer_lock_held`. A lock is removed automatically only if
  its owner is provably dead on the same host; otherwise investigate and remove it yourself.
- `--out` must be a real directory (a symlink is refused).
- Every dataset is born **UNTRUSTED** (`development_status`). To make it TRUSTED, add
  `--anchor-out <file outside --out> --anchor-recorded-by <name> --anchor-storage-ref "<where it is kept>"`
  and then **store that anchor file independently** (PR/ticket comment, another host). The anchor
  carries `manifest_sha256`, `dataset_id`, `content_sha256`, `symbol`. A copy left next to the data on
  the same disk is not independent.

## 5. If the run is interrupted
Re-run the identical command. Final files are only ever complete (atomic create); leftover
`*.tmp-*` files are removed automatically at start. A final chunk that disagrees with a fresh fetch
stops the run with `chunk_exists_with_different_content`: do not delete anything; report it (the
source history changed under you or the file was tampered with).

## 6. Holdout custody
Reading a window is itself an access. If a split plan exists, pass its locked ranges as
`--protected-utc-ranges ranges.json` (UTC ms, `[start, end)`); overlap is refused. Malformed ranges
(inverted, empty, non-integer, wrong shape) are refused, never skipped, and one of
`--protected-utc-ranges` / `--no-protected-ranges` is required. Because protection relies on the
declared offset, set `--protected-margin-ms` to at least the offset uncertainty. No spread/price
statistics are produced by the quality report. Do not open chunk files for the locked segment.

## 7. Independent verification
A second person on a clean checkout of the same SHA runs
`uv run --no-sync python scripts/verify_market_history.py --dataset <folder> --anchor <anchor file
fetched from the independent store> --require-trusted` (exit 0 only if `TRUSTED`). `TRUSTED` needs: a
clean verification, no hard defects, and an anchor whose manifest hash, `dataset_id`, `content_sha256`
and symbol all match. Without an anchor the result is `UNTRUSTED` (development use only).

## 8. Evidence to report back to the PR (no raw data)
SHA, node/terminal build, operator, window and its pre-registration reference, `dataset_id` (content identity), `content_sha256`,
`manifest_sha256`, the clock attestation reference, tick count, gap count (threshold used), hard defects (must be none), offset
investigation report id, and any deviation. Missing commission/swap history stays `unavailable`.

## STOP conditions
Unreviewed SHA; dirty checkout; any prompt for credentials; offset evidence missing; window overlaps a
protected range; verification or blockers non-empty; anything that would send/modify/close an order.
