# Pilot runbook (operator) — status: acquisition BLOCKED

1. Confirm in writing that your use of the Dukascopy data is licensed (ToU s.3/s.4: personal,
   non-commercial; automated access needs prior written consent). Do not commit raw files.
2. Obtain the 168 hourly files for 2026-09-07..2026-09-13 (`PILOT-PLAN.md`) by a licensed route.
   Keep them byte-for-byte as received. Absent/empty hours are fine.
3. Write `retrieval_log.json`: `{"files":[{"file":"XAUUSD/2026/08/07/10h_ticks.bi5","url":"...","retrieved_at":"2026-..T..+00:00"}]}`
   (file paths follow the declared month index base; Dukascopy-style directories are 0-indexed).
4. Write `semantics.json` with the fields of `DeclaredSemantics` (clock offset + evidence, price divisor,
   month index base, who confirmed the 20-byte layout). Nothing is defaulted.
5. `python scripts/ingest_dukascopy_pilot.py --source-dir ... --out ... --symbol XAUUSD --start-day 2026-09-07 --days 7 --semantics semantics.json --retrieval-log retrieval_log.json --selection-ref PILOT-PLAN.md --max-gap-ms <Quant value>`
6. Commit only the resulting `manifest.json` (hashes, counts) if the owner approves; independent
   reviewer runs `verify` on the same bytes. If verification or layout plausibility fails, STOP.

Not implemented: CSV export parsing (timestamp text format unverified), any network fetch.
