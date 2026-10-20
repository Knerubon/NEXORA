# Windows execution checklist (operator-controlled, FUTURE)

**Nothing in this checklist is authorized by this PR and no step is run in this wave.** No step below
starts, attaches to, or logs in to a terminal in the current wave. It is the plan an operator would
follow only after (1) independent review has approved readiness of the exact SHA and this package,
and (2) Rin/the Owner has given a written go-ahead naming the SHA and the window. It complements,
and does not replace, `docs/research/market-history/WINDOWS-MT5-RUNBOOK.md` (stacked PR; as read at
`e74ad035e7f21aa07528965da6ebe190c0512793`).

Class: WINDOWS-INTEGRATION. Not PRODUCTION-ONLY. Never touches NEXORA PROD, PROD journals/checkpoints/
DSNs, orders, or account data. Notation: **STOP** = abort, record, do not continue; **GO** = all
listed conditions true.

## Phase A - authorization and checkout (no terminal)

| # | Step | Go / stop condition |
|---|---|---|
| A1 | Record the written go-ahead reference, the named full SHA `<SHA>`, window, and operator. | STOP if any is missing, or the go-ahead names a different SHA. |
| A2 | Confirm independent readiness review of `<SHA>` and of this package is APPROVE and its HEAD matches. | STOP if absent, stale (HEAD moved after review), or self-review only. |
| A3 | Disposable checkout (not the PROD worktree): `git clone`, `git checkout <SHA>`; `git rev-parse HEAD` equals `<SHA>` (40 chars); `git status --short` empty. Re-verify against `<SHA>` the API names/refusal codes/allowlist quoted in INTEGRATION-MATRIX.md. | STOP on dirty tree, SHA mismatch, or any quoted name/behaviour that differs (update the matrix first and re-review). |
| A4 | In the shell: confirm `NEXORA_MT5_*`, `NEXORA_POSTGRES_DSN`, `NEXORA_ENV=production` are NOT set. | STOP if any is set. |
| A5 | `uv sync --locked --extra mt5`; record versions of Python, uv, `MetaTrader5`, `numpy`. | STOP on resolver change (`uv lock --check` fails). |
| A6 | Pre-register the window (raw and UTC bounds, symbol as in Market Watch, purpose) in a place that timestamps it **before** any data is read; record the reference. | STOP if the window was chosen after seeing data, or overlaps any locked/holdout range. |
| A7 | Decide protected ranges (`--protected-utc-ranges` file or explicit `--no-protected-ranges` with reason) and margin >= offset uncertainty. | STOP if undecided. |

## Phase B - offline probes and mock-source proof (no terminal)

| # | Step | Go / stop condition |
|---|---|---|
| B1 | `uv run --no-sync pytest -q tests/windows_integration_readiness` on the node. | STOP on any failure; skips must each have their stated reason. |
| B2 | Run `python -m tests.windows_integration_readiness.filesystem_probes <empty dir on the dataset volume>`; attach JSON (case 6). Record filesystem type, antivirus/indexer state. | STOP if the volume is a network share, cloud-synced folder, FAT/exFAT, or hard-link probe is `unsupported`. |
| B3 | Manual reparse probe (case 6 step 3) and crash/lock probes against a **mock** source (case 6 steps 4-5). | STOP if a junction/symlink is followed silently or a lock is reaped for a live/foreign owner. |
| B4 | Replay vectors (cases 2-5) against the real extractor at `<SHA>` with the mock source only. | STOP on any divergence from `vectors.py`; record as finding. |
| B5 | Obtain the broker clock rule **independently of ticks** (case 3 step 1) and the DST fall-back list (case 4 step 1); assign `evidence_ref`s; write `time_conversion.json` and `clock_attestation.json`; compute the split windows. | STOP if no independent evidence exists. Never default an offset. |

## Phase C - terminal contact (FUTURE; each step needs the go-ahead in A1)

| # | Step | Go / stop condition |
|---|---|---|
| C1 | Confirm the terminal is already running, logged in by the operator with an investor (read-only) or demo account. The tool takes no credentials. | STOP on any credential prompt, any request to type a password into a command, file or ticket. |
| C2 | Record terminal facts (build, company/name, server name; no account number/balance). | STOP if the terminal is a live-funds account with a trading password. |
| C3 | Wrap the module in `RecordingModuleProxy`; run case 1 on the tiny window; attach `proxy.names`. | STOP if any name outside the allowlist appears. |
| C4 | Run preflight refusal checks (cases 3 and 4) and confirm `initialize` was not called for refused inputs. | STOP if the terminal was contacted before validation. |
| C5 | Run the extraction for the approved window(s) only (split around folds). Exact command per runbook section 4. | STOP on any non-zero exit, `writer_lock_held`, `chunk_exists_with_different_content`, or any hard defect. |
| C6 | On failure or interruption: do not delete anything; follow runbook section 5; report. | - |

## Phase D - independent verification and close-out

| # | Step | Go / stop condition |
|---|---|---|
| D1 | Store the anchor file independently of the data (ticket/PR comment or another host). | STOP if the only copy sits next to the data. |
| D2 | **Second person**, clean checkout of `<SHA>`, different session: run the verifier with `--anchor <fetched from independent store> --require-trusted`. | GO only on exit 0 and status `TRUSTED`. STOP otherwise; the dataset stays `UNTRUSTED` (development use only). |
| D3 | Fill [EVIDENCE-TEMPLATE.md](EVIDENCE-TEMPLATE.md) completely; every unverified item stays `NOT VERIFIED`. | STOP if a field is blank instead of `NOT VERIFIED`/value. |
| D4 | Attach evidence to the PR; request independent review of the record. | The record is not accepted until a reviewer who did not operate or implement it approves. |
| D5 | Shut down nothing that you did not start. Remove the disposable checkout. Do not commit raw chunks. | - |

## Global stop conditions

Unreviewed or moved SHA; dirty checkout; any credential prompt; missing independent clock evidence;
window overlapping a protected/holdout range; verification or usability blockers non-empty; anything
that would send, modify or close an order; any proposal to run a step outside this list; any
Dukascopy or other external retrieval (separate authority, not this package).
