# Windows execution checklist (operator-controlled, FUTURE)

**Nothing in this checklist is authorized by this PR and no step is run in this wave.** No step
below starts, attaches to, or logs in to a terminal in the current wave. It is the plan an operator
would follow only after (1) independent review has approved readiness of the exact SHA and this
package, (2) every dependency marked BLOCKED in [DEPENDENCY-REGISTER.md](DEPENDENCY-REGISTER.md)
that a step needs is resolved, and (3) Rin/the Owner has given a separate written go-ahead naming
the SHA and the window. It complements, and does not replace,
`docs/research/market-history/WINDOWS-MT5-RUNBOOK.md` (stacked; as read at PR #96
the pinned candidate head (`dependency-manifest.json`)).

Class: WINDOWS-INTEGRATION. Not PRODUCTION-ONLY. Never touches NEXORA PROD, PROD journals/checkpoints/
DSNs, orders, or account data. Notation: **STOP** = abort, record, do not continue; **GO** = all
listed conditions true; **BLOCKED** = no reviewed interface exists, the step is not performed.

**The operator never writes code.** Every step names an existing command from the extractor stack
or this package. A step that would need operator-written Python (a driver, a proxy around the real
module, a fault hook, direct `MetaTrader5` calls) is BLOCKED on DEP-03. Any proposal to improvise
one is a STOP.

## Real-half step register

`tests/windows_integration_readiness/execution_contract.py` (`REAL_HALF_STEPS`) holds the same
register; `test_execution_contract.py` fails if the two disagree.

| Step | Disposition | Command or blocking dependency |
|---|---|---|
| `case1.step1-3.dtype_and_layout` | BLOCKED | DEP-03: no reviewed way to read the terminal's `dtype.descr` |
| `case1.step4.empty_window` | BLOCKED | DEP-03: the extractor CLI does not report raw rows |
| `case2.step1-2.vector_replay` | BLOCKED | DEP-03: no entry point feeds vectors to the extractor's normalization |
| `case2.step3.quality_counts` | EXISTING_COMMAND | `scripts/extract_market_history.py` (phase C); manifest quality report |
| `case3.step2-3.preflight_refusals` | EXISTING_COMMAND | `scripts/extract_market_history.py` (phase C); preflight runs before the source is constructed |
| `case4.step2.derived_fold_ranges` | BLOCKED | DEP-03: no CLI prints derived fold ranges |
| `case4.step3.preflight_refusals` | EXISTING_COMMAND | `scripts/extract_market_history.py` (phase C) |
| `case5.step1-3.mock_source_runs` | BLOCKED | DEP-03: the extractor has no mock-source injection |
| `case5.step4.idempotent_rerun` | EXISTING_COMMAND | `scripts/extract_market_history.py` (phase C), same `--out` |
| `case6.step1.filesystem_probes` | EXISTING_COMMAND | `python -m tests.windows_integration_readiness.filesystem_probes <empty dir>` |
| `case6.step3.reparse_out_path` | EXISTING_COMMAND | `scripts/extract_market_history.py --out <junction>` (phase C) |
| `case6.step4.crash_hook` | BLOCKED | DEP-03: the extractor has no reviewed fault hook |
| `case6.step5.second_writer` | EXISTING_COMMAND | `scripts/extract_market_history.py` twice on one `--out` (phase C) |
| `case7.step1.recording_proxy_real_module` | BLOCKED | DEP-03: wrapping the real module needs operator-written code |
| `case7.step2.shutdown_paths` | BLOCKED | DEP-03: no reviewed fault injection |
| `case7.step3-4.custody_and_verify` | BLOCKED | DEP-01: TRUSTED is unreachable at V4 (see [V4-CUSTODY-COMPATIBILITY.md](V4-CUSTODY-COMPATIBILITY.md)) |

Because several cases have BLOCKED steps, no case can reach `PASS` and the evidence record's
`overall_result` cannot be `PASS` until DEP-01 and DEP-03 are resolved. **Real-half readiness is not
declared by this package.**

Every `scripts/extract_market_history.py` invocation is first checked offline with the reviewed
planning CLI below (finding N4). It wraps `execution_contract.plan_invocation` and checks the flags
as read at the pinned candidate: required flags, exactly one of `--protected-utc-ranges`/
`--no-protected-ranges`, the symbol verbatim from the pre-registration, the reviewed extractor SHA and
version, and an absolute, empty `--out` whose canonical path (symlinks and `..` resolved) is outside
the canonical checkout. `--repo-root` is mandatory. The command prints one JSON object, runs nothing,
contacts no terminal and exits 0 only for a valid plan. The operator types it; no Python is written.

Planning command (one run per extractor command line; `test_r4_execution_contract.py` runs this exact
line):

```
python -m tests.windows_integration_readiness.execution_contract plan --repo-root <checkout> --provider mt5 --preregistered-symbol <symbol> --extractor-sha <head> --expected-extractor-sha <SHA> --extractor-version nexora.market-history-extractor/6 --manifest-schema nexora.market-history/4 -- <extractor-arguments>
```

`<checkout>` is the disposable checkout of A3, `<head>` its `git rev-parse HEAD`, `<SHA>` the SHA named
in A1, and `<extractor-arguments>` every argument that will be passed to
`scripts/extract_market_history.py`. The planning step itself needs no dependency; the extraction it
plans is still phase C, and every real-half step marked BLOCKED above stays BLOCKED (DEP-03).

## Phase A - authorization and checkout (no terminal)

| # | Step | Go / stop condition |
|---|---|---|
| A1 | Record the written go-ahead reference, the named full SHA `<SHA>`, window, and operator. | STOP if any is missing, or the go-ahead names a different SHA. |
| A2 | Confirm independent readiness review of `<SHA>` and of this package is APPROVE and its HEAD matches. Confirm every dependency a planned step needs is RESOLVED in DEPENDENCY-REGISTER.md. | STOP if the review is absent, stale (HEAD moved after review) or self-review only, or if a needed dependency is not RESOLVED. |
| A3 | Disposable checkout (not the PROD worktree): `git clone`, `git checkout <SHA>`; `git rev-parse HEAD` equals `<SHA>` (40 chars); `git status --short` empty (record both outputs as `checkout.*`). Re-verify against `<SHA>` every name quoted in INTEGRATION-MATRIX.md, `execution_contract.py` and `custody_gate.py` (CLI flags, refusal codes, allowlist, `EXTRACTOR_VERSION`, schema, `PROVEN_PLATFORMS`). | STOP on dirty tree, SHA mismatch, or any quoted name/behaviour that differs (update this package first and re-review). |
| A4 | In the shell: confirm `NEXORA_MT5_*`, `NEXORA_POSTGRES_DSN`, `NEXORA_ENV=production` are NOT set. | STOP if any is set. |
| A5 | `uv sync --locked --extra mt5`; record versions of Python, uv, `MetaTrader5`, `numpy`. | STOP on resolver change (`uv lock --check` fails). |
| A6 | Pre-register the window (raw and UTC bounds, symbol exactly as in Market Watch, purpose) in a place that timestamps it **before** any data is read; record the reference. | STOP if the window was chosen after seeing data, or overlaps any locked/holdout range. |
| A7 | Decide protected ranges (`--protected-utc-ranges` file or explicit `--no-protected-ranges` with reason) and margin >= offset uncertainty. | STOP if undecided. |

## Phase B - offline probes (no terminal)

| # | Step | Go / stop condition |
|---|---|---|
| B1 | `uv run --no-sync pytest -q tests/windows_integration_readiness` on the node. | STOP on any failure; skips must each have their stated reason. |
| B2 | `python -m tests.windows_integration_readiness.filesystem_probes <empty dir on the dataset volume>`; attach the JSON (case 6). Resolve each `manual_required` probe by attaching its evidence (`fsutil fsinfo volumeinfo <drive>:` output; the junction probe of matrix case 6 step 3). Record antivirus/indexer state. | STOP while `stop_required` is true and any `manual_required` probe lacks attached evidence; STOP on any `error` outcome; STOP if the hard-link probe is `unsupported`, or the volume is a network share, cloud-synced folder, FAT or exFAT. `verified_on_windows_ntfs` is always `false`: the probes record, they do not verify. |
| B3 | Obtain the broker clock rule **independently of ticks** (case 3 step 1) and the DST fall-back list (case 4 step 1); assign `evidence_ref`s; write `time_conversion.json` and `clock_attestation.json` per the runbook. | STOP if no independent evidence exists. Never default an offset. |
| B4 | Check every planned extractor command line with the planning command above (`execution_contract plan`); attach each printed JSON. | STOP on any exit code other than 0 (`"ok": false` names the refusal code). |

## Phase C - terminal contact (FUTURE; each step needs the go-ahead in A1)

| # | Step | Go / stop condition |
|---|---|---|
| C1 | Confirm the terminal is already running, logged in by the operator with an investor (read-only) or demo account. The tool takes no credentials. | STOP on any credential prompt, any request to type a password into a command, file or ticket. |
| C2 | Record terminal facts (build, company/name, server name; no account number/balance). | STOP if the terminal is a live-funds account with a trading password. |
| C3 | Preflight refusal checks (cases 3 and 4) through `scripts/extract_market_history.py` with deliberately invalid inputs. Preflight runs before the source is constructed at the pinned candidate; record the refusal code and exit code. | STOP if a run that should refuse does not refuse (it would attach to the terminal). |
| C4 | Run the extraction for the approved window(s) only (split around folds), with `--operator <id>` and `--candidate-out <file outside --out>`. Exact command per runbook section 4. | STOP on any non-zero exit, `writer_lock_held`, `chunk_exists_with_different_content`, or any hard defect. |
| C5 | Second-writer and reparse probes (case 6 steps 3 and 5) with the same command, on a scratch `--out`. | STOP if a junction/symlink is followed silently or two writers both proceed. |
| C6 | On failure or interruption: do not delete anything, including `.writer.lock`; follow runbook section 5; report. Nothing is ever reaped at V4. | STOP. Never remove a lock file while any run on that folder could be active. |

## Phase D - custody and independent verification (V4)

| # | Step | Go / stop condition |
|---|---|---|
| D1 | Hand the candidate and the dataset location to an **authorized custodian** (not the operator, not the candidate creator). | STOP if the custodian is the operator or candidate creator under any spelling. |
| D2 | Custodian (key held by the custodian, never the operator): `scripts/custody_market_history.py approve --dataset --store --registry --signing-key --evidence-ref --candidate`; record `seq`, `entry_sha256`, `approval_id`, `key_id`, the signature and `acknowledged_revocations`. | STOP on any `refused` output, on a replayed `approval_id`, or on a candidate reused across approvals. |
| D3 | At verification time the pin authority runs `scripts/custody_market_history.py pin --store <dir> ...` with its own signing key and publishes the signed pin (`pin_id`, `pinned_by`, `key_id`, `signature`, `seq`, `entry_sha256`, `captured_at`) through an independent channel. An unsigned `head` / `--store-head` value is only a truncation check. | STOP if `chain` is not `ok` or the pin is not signed by an authority other than operator and custodian. |
| D4 | **Second person**, clean checkout of `<SHA>`, different session: `scripts/verify_market_history.py --dataset <dir> --store <dir> --registry <file> --pin <pin.json> --max-pin-age-seconds <N> --require-trusted`. Record command, exit code (0 = TRUSTED only; 1 untrusted, 2 invalid input, 3 operational error; any non-zero is "do not use as evidence") and printed JSON in `custody.*`. A V3 `--anchor` alone never grants trust. | **BLOCKED (DEP-01):** at V4 a dataset extracted or verified on Windows cannot be TRUSTED (`platform_unproven:win32`), and the V4 custody baseline has open MAJOR findings. Record the verifier's actual output; the dataset stays `UNTRUSTED` (development use only). |
| D5 | Fill the evidence record from [evidence-record.template.json](evidence-record.template.json); run `python -m tests.windows_integration_readiness.evidence_validator <record.json>`. | STOP on any validator error. A blank field instead of `NOT VERIFIED` is an error. |
| D6 | Attach the record to the PR; request independent review of the record. | The record is not accepted until a reviewer who did not operate or implement it approves. |
| D7 | Shut down nothing that you did not start. Remove the disposable checkout. Do not commit raw chunks. | - |

## Global stop conditions

Unreviewed or moved SHA; dirty checkout; any credential prompt; missing independent clock evidence;
window overlapping a protected/holdout range; verification or usability blockers non-empty; anything
that would send, modify or close an order; any step marked BLOCKED; any proposal to run a step
outside this list or to write code on the node; any Dukascopy or other external retrieval
(separate authority, not this package).
