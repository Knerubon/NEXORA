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
  --operator <your principal id> --operator-key <path to your 0600 operator key, NOT in the dataset> `
  --candidate-out D:\nexora-evidence-staging\<dataset-name>.candidate.json `
  --out D:\nexora-data\<dataset-name>
```
- Every input above is validated BEFORE the tool attaches to the terminal; a bad range, attestation or
  conversion exits without touching MT5.
- Choose `--chunk-ms` so one chunk is far below `--max-chunk-ticks`. A chunk over the limit is refused
  before tick objects are built, but the terminal's own array for the window is already allocated
  (about 8x smaller than the Python objects) and cannot be bounded by this tool.
- Output lives outside Git. Do not commit raw chunks (size, and broker data terms).
- Exit code 0 only if verification is clean **and** there are no usability blockers.
- Only ONE extraction may own an output folder. Ownership is an OS advisory lock held on an open
  descriptor of `.writer.lock` (`msvcrt.locking` on Windows; `flock` on Linux). The OS drops it when the
  process exits or crashes, so a crashed run never needs manual unlocking and **nothing is ever
  reaped or renamed**. A second run fails with `writer_lock_held`; so does any lock file whose owner
  cannot be verified (garbage, partial, foreign-host or V3-format record): then check by hand that no
  extraction is running on that folder, and remove `.writer.lock` yourself. Do not run V3 and V4
  tooling on the same folder.
- `--out` is opened once with no-follow semantics; a symlink (or junction) at the folder **or any
  parent** is refused. Give the real path.
- The tool probes the output filesystem under the lock (hard links without overwrite, no-follow open,
  link counts, advisory-lock exclusion, directory fsync, local vs network class) and records the result
  as `filesystem_capabilities` in the manifest. Extraction on a filesystem without hard links stops with
  `filesystem_cannot_hardlink` (FAT/exFAT/some shares).
  **Windows/NTFS is currently UNVERIFIED**: this tool records the probe, but a dataset extracted on
  Windows (or on a network share) is `UNTRUSTED` for evidence purposes until a recorded
  WINDOWS-INTEGRATION run proves the guarantees on that configuration (Rin/Architect decision; see
  `THREAT-MODEL-V4.md`). It remains fully usable for development.
- Every dataset is born **UNTRUSTED** (`development_status`) and the extractor can never make it
  anything else. `--operator` records who ran the extraction (a dataset without one can never be
  trusted); it must be the strict lower-case principal id from the registry. `--candidate-out`
  (outside `--out`) writes an anchor CANDIDATE signed with `--operator-key`: the dataset hashes, the
  operator principal and key id, and a nonce. A candidate grants nothing. Hand the candidate file
  and the dataset to an authorized custodian (section 7). The old `--anchor-out` V3 anchor still works
  but is not custody evidence: it can never produce `TRUSTED`.

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

## 7. Custody: signed independent approval, signed head pin, then verification
Three DIFFERENT principals take part: the operator (extracted the data), an authorized custodian
(approves) and a pin authority (signs the store head through its own channel). **No pin authority
is designated today, so no dataset can become TRUSTED yet**: that needs a governance decision naming
one and a `max_pin_age` policy. Do not invent either.

Keys (each holder, once, on their own machine; the private key file never enters Git, the dataset or
the store):
```powershell
uv run --no-sync python scripts/custody_market_history.py keygen --out <private-key-file>
# prints {"ed25519": "<public key hex>", "key_id": "<sha256>"}: give the PUBLIC key to the registry owner
```
Registry (JSON kept OUTSIDE the dataset, maintained by whoever owns custody; ids are strict
lower-case ASCII, one entry per principal):
`{"schema":"nexora.market-history-principals/2","principals":[{"id":"carol","roles":["custodian"],
"keys":[{"ed25519":"<hex>","valid_from":"...","valid_until":"...","revoked":false}]}]}`
(roles: `operator`, `custodian`, `pin_authority`; a key marked `revoked` voids everything it signed).

Approval (custodian, on a machine they control, from a clean checkout of the same SHA). The store is
append-only and outside the dataset folder (`LocalAppendOnlyStore` is the reference for tests/single
host; production needs real immutability such as object lock or a ledger, which you must provide):
```powershell
uv run --no-sync python scripts/custody_market_history.py approve --dataset <folder> `
  --candidate <candidate.json> --store <evidence-store-dir> --registry <registry.json> `
  --signing-key <custodian-key-file> --evidence-ref "<ticket / PR comment where the decision is recorded>"
uv run --no-sync python scripts/custody_market_history.py revoke --dataset <folder> `
  --store <dir> --registry <registry.json> --signing-key <custodian-key-file> --evidence-ref "<ref>"
```
`approve` re-verifies ONE pinned copy of the dataset and refuses it on blockers, hard defects, an
unsigned or foreign operator attestation, a signer who is not a custodian, a custodian who is the
operator, a candidate already used, or a dataset path replaced during the check. The entry is signed
and lists every earlier revocation of the dataset (`acknowledged_revocations`). A re-approval after a
revocation needs a fresh signed candidate from the operator.

Pin (pin authority, reading the store through its own channel, after the approval):
```powershell
uv run --no-sync python scripts/custody_market_history.py pin --store <dir> --registry <registry.json> `
  --signing-key <pin-authority-key-file> --out <new-pin.json> (--previous <last-pin.json> | --initial | --reanchor-ref "<authorization ref>")
```
A pin must extend the previous pin; `--reanchor-ref` names where an independent authorization to
re-anchor a rewritten store is recorded. `head` prints the unsigned head (never sufficient on its own).

Verification (a second person):
`uv run --no-sync python scripts/verify_market_history.py --dataset <folder> --store <dir>
--registry <registry.json> --pin <pin.json> --max-pin-age-seconds <policy value>`
(exit codes: `0` only if `TRUSTED`; `1` UNTRUSTED; `2` invalid input; `3` operational error; any non-zero
means do not use the dataset as evidence; `--require-trusted` is a deprecated no-op). Consumers read the data through `open_trusted`, which re-hashes every
chunk as it is read. `TRUSTED` needs ALL of: clean verification on one pinned copy; no hard defects; a
proven `filesystem_capabilities` record and a supported verifier platform; a signed operator
attestation by the recorded operator; a custodian-signed approval bound to this exact dataset id,
content hash, manifest hash, symbol and window, from a key valid at signing and now, by a principal
independent of the operator; correct revocation lineage and no later revocation; and a pin signed by
an independent pin authority, no older than `max_pin_age`, that covers the approval.

Crash recovery: `custody_market_history.py recover-store --store <dir>` finishes an append that
crashed between link and temp unlink; `scripts/recover_market_history.py <dataset-folder>` does the
same for a dataset publication (extract re-runs do it automatically). Both remove only temp names
that are the same file as their published entry and print what they removed.

After a `revoke`, the pin authority must re-pin (`pin --previous <last-pin>`) so the new pin covers the
revocation, and verifiers must use only the newest pin: a revocation newer than the latest pin can be
hidden by truncating the store tail until that pin becomes stale (`max_pin_age`; THREAT-MODEL-V4.md
section 9). `revoke` prints `repin_required` as a reminder.

Process controls this code cannot prove: registry upkeep, private-key custody, that the store really
is append-only, and that the pin authority and custodian are independent people.

## 8. Evidence to report back to the PR (no raw data)
SHA, node/terminal build, operator, window and its pre-registration reference, `dataset_id` (content identity), `content_sha256`,
`manifest_sha256`, the operator identity, the clock attestation reference, the `filesystem_capabilities` record, the candidate hash, the custody approval `seq`/`entry_sha256`, the pin id and the `max_pin_age` used,  tick count, gap count (threshold used), hard defects (must be none), offset
investigation report id, and any deviation. Missing commission/swap history stays `unavailable`.

## STOP conditions
Unreviewed SHA; dirty checkout; any prompt for credentials; offset evidence missing; window overlaps a
protected range; verification or blockers non-empty; anything that would send/modify/close an order.
