# Market-history Windows readiness (EDGE-VALIDATION-1, Wave V4, Track B)

| Field | Value |
|---|---|
| Status | DRAFT readiness package. Documentation plus an offline fixture/mock harness. Not reviewed independently. |
| Class | Docs and CLOUD-SAFE tests. The steps it describes are WINDOWS-INTEGRATION and are **not** authorized by this PR. |
| Base | `origin/main` `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87` |
| Extractor read (read-only) | `origin/claude/market-history-fix-v3` at `e74ad035e7f21aa07528965da6ebe190c0512793` (stacked PRs #89, #91, #92, #93) |
| Parent issue | EDGE-VALIDATION-1 (#77) |

## Purpose

Turn "the extractor should work on the Windows MT5 node" into seven concrete, evidence-bearing
integration cases, so that a future operator-started run produces checkable evidence instead of
assertions, and so that every Windows guarantee that is **not** proven is written down.

## What this PR is not

- It does not authorize, schedule or start any real-data execution. An **independent reviewer must
  approve readiness BEFORE any real-data execution**, and a separate written go-ahead from Rin/the
  Owner for the exact SHA and window is still required (see the runbook section 0).
- It starts no MT5 terminal, extracts no history, reads no holdout/protected range, calls no
  order/account API (not even in harness code paths), performs no Dukascopy retrieval, and makes no
  PROD change.
- It contains no real or fabricated market data. Every fixture value is synthetic and labelled so.
- It does not freeze anything, merge anything, or change production strategy.

## Contents

| File | Role |
|---|---|
| [INTEGRATION-MATRIX.md](INTEGRATION-MATRIX.md) | The seven cases: steps, expected results, evidence fields, pass/fail, what each does NOT prove |
| [WINDOWS-EXECUTION-CHECKLIST.md](WINDOWS-EXECUTION-CHECKLIST.md) | Operator-controlled step list with go/no-go stops; no step in this wave starts a terminal |
| [EVIDENCE-TEMPLATE.md](EVIDENCE-TEMPLATE.md) | Evidence record fields, with [evidence-record.template.json](evidence-record.template.json) and [evidence-record.schema.json](evidence-record.schema.json) |
| [UNSUPPORTED-GUARANTEES.md](UNSUPPORTED-GUARANTEES.md) | What cannot be promised on Windows/NTFS/shares/reparse points/antivirus/locking, and what is Linux-cloud-tested only |
| `tests/windows_integration_readiness/` | Offline pytest harness (mocks, fixtures, filesystem probes) |

## Dependency and sequencing constraint (read this first)

The extractor under test (`nexora.market_history`, `scripts/extract_market_history.py`,
`scripts/verify_market_history.py`) is **not on `main`**. It lives in the unmerged stack
#89 -> #91 -> #92 -> #93 (head `e74ad035...` when read), and Track A is changing it concurrently.
Consequences:

1. The harness here is self-contained and **does not import `nexora.market_history`**. It carries an
   independent *reference model* of the contract (the oracle) plus golden vectors. Passing it proves
   the harness expectations are self-consistent and the probes work; it proves **nothing** about the
   extractor's code.
2. The API names, allowlist and behaviours quoted in these documents were read from the stacked code
   at `e74ad035e7f21aa07528965da6ebe190c0512793`. They **must be re-verified against the named SHA**
   that will actually run (checklist step A3). Where the stack changes, this package must be
   re-pointed before use.
3. Before real-data execution the stack must be reviewed and merged (or this harness re-pointed to
   the reviewed head), and each matrix case executed against the real extractor on the node using the
   same vectors.

## How the two halves relate

| Half | Where | What runs | Proves |
|---|---|---|---|
| Mock / fixture | Cloud, normal `pytest` | `tests/windows_integration_readiness/` | Harness logic, vectors, probe code, current-platform (Linux) filesystem answers |
| Real | Windows node, operator-started, named SHA, **future** | Same steps/vectors against the real extractor and terminal (checklist) | Whatever the evidence record shows, and only for that SHA/node/window |

Test-to-case mapping: each test module is named `test_caseN_*.py` and its docstring names the matrix
case it covers.

## Environment limits of the cloud half

`numpy` is not in the locked cloud environment (the `MetaTrader5` wheel that pulls it is win32-only),
so the one numpy-dtype test is skipped there with a stated reason; the dtype layout is also checked
without numpy through `dtype.descr` tuples. A skip is not a pass for that check.
