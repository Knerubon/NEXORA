# Dukascopy XAUUSD pilot — plan (written BEFORE any download; no data has been fetched)

Purpose: validate the **data pipeline** only (provenance, byte preservation, decoding, hashing,
coverage). No strategy signals, PnL, optimisation, holdout evaluation or profitability claim.

## Selection (fixed now, not derived from any data)
- Instrument: XAUUSD (provider symbol as published by the source; broker-agnostic code).
- Window: **seven consecutive calendar days, 2026-09-07 00:00:00 through 2026-09-13 23:59:59**
  on the provider's declared clock (Monday–Sunday). Chosen for: ends before the FIX3 observation
  samples of 2026-09-21 (exposure ledger Q-D02), and includes one full weekend so closure handling
  is exercised. The window is NOT part of any split and must be excluded from any later evaluation
  (pilot data is exposed to the pipeline authors).
- Slots: 7 x 24 = 168 hourly files. A missing or empty hour is **not** a defect by itself
  (weekends, closures, holidays); it is reported as coverage state.

## Status: real acquisition BLOCKED (see FINDINGS.md)
1. The cloud workspace cannot reach `datafeed.dukascopy.com` (proxy 403 on CONNECT; not worked around).
2. Dukascopy Terms of Use section 3 forbids bots/scrapers/data-mining without prior written
   consent and forbids building a database from the site; downloads are licensed for personal,
   non-commercial use. Automated bulk retrieval is therefore **not licence-verified**.
3. The `.bi5` layout and the time base are documented only by third parties/forum posts.

Unblock: the data owner obtains the 168 files lawfully (written consent for automated access, or
another route the licence permits), records URL + retrieval time per file, and runs the CLI on a
machine that holds them. No fabricated or substituted data will be used.
