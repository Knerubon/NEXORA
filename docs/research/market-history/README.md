# Market history extraction (EDGE-VALIDATION-1, Agent B)

Status: tooling complete (data-integrity fix wave applied, see `RISK-REVIEW.md`; operator steps in
`WINDOWS-MT5-RUNBOOK.md`); **real extraction BLOCKED** (no MT5 host in cloud sessions).
Package `nexora.market_history`, CLI `scripts/extract_market_history.py` (operator-started,
WINDOWS-INTEGRATION, read-only, no credentials, no orders).

## Guarantees (each covered by `tests/test_market_history.py`)
- Raw broker `time_msc` is always stored; UTC is derived only via a `TimeConversion` of
  operator-declared segments with a mandatory `evidence_ref`. No default offset, no auto-detection,
  no application outside a segment's raw-time interval; uncovered windows are refused.
- MT5 access passes a call allowlist (`initialize/shutdown/last_error/version/terminal_info/
  symbol_info/copy_ticks_range`; `initialize()` takes no arguments); trade calls are refused and the gate exposes no
  raw-module attribute. Terminal `path` and account fields are not captured.
- Chunks are write-once, created atomically (temp + fsync + no-overwrite link) and read-only; the manifest is written last, write-once, with a self hash and
  `dataset_id`; `verify_dataset` re-derives hashes, counts, contiguity and the quality report
  from files alone.
- Commission is recorded `unavailable` (not exposed by `symbol_info`), never zero.
- The quality report has counts and coverage only (no price/spread distributions) so it does not
  breach holdout custody. `max_gap_ms` has no default (Quant decision, OPEN).
- `retrieved_at` is historical-retrieval time, not original knowledge time.

## +10800 s offset investigation (status: UNVERIFIED)
Prior evidence (FIX2/FIX3, ADR-019) shows raw `time_msc` ~10800 s ahead of UTC on one observed feed.
Stored ticks cannot reveal their own offset; `offset.summarize_offsets` takes paired live samples
(raw time vs independent UTC reference), reports per-day medians and flags variation (DST /
effective dates). It never applies anything. Open: source semantics, DST behaviour, effective
dates, per symbol/server. Until a documented `evidence_ref` exists, no UTC conversion may be used
for a gate-G2 dataset.

## Missing-data register
| ID | Item | Status | Unblock condition | Owner |
|---|---|---|---|---|
| MD-1 | Real historical ticks (bid/ask, any symbol) | BLOCKED | Operator runs the CLI on the Windows node against a named SHA | Operator / Market data |
| MD-2 | Verified raw->UTC offset and effective dates | BLOCKED | Paired-sample report + documented evidence_ref | Market data / Quant |
| MD-3 | Broker commission and account-level costs | BLOCKED | Source of truth for commission (contract spec / statement) | Quant |
| MD-4 | Swap schedule incl. triple-swap day, effective dates | PARTIAL | `swap_long/short/mode/rollover3days` captured; history of changes unavailable | Market data |
| MD-5 | Trading-session calendar / holidays | BLOCKED | Source + Quant decision | Quant |
| MD-6 | Gap threshold `max_gap_ms` | OPEN | Quant decision (register) | Quant |
| MD-7 | Historical bid/ask availability depth per broker | UNKNOWN | Discovered only by real extraction | Operator |
| MD-8 | Dukascopy addendum (Issue #77) | NOT ACTED ON | Explicit owner instruction; it is issue text, not an instruction | Owner |

`sample-manifest.synthetic.json` is generated from a synthetic fixture to show the schema. It is
NOT market data.
