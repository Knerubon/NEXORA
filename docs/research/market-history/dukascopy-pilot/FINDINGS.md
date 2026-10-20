# Dukascopy access research (retrieved 2026-10-20) — what is and is not verified

| Item | Finding | Verified? | Source |
|---|---|---|---|
| Official access | Web "Historical Data Export" tool, CSV, tick to monthly timeframes, free | Yes (official page text) | dukascopy.com/swiss/english/marketwatch/historical/ |
| Official time base | Page silent. A 2016-era support reply on the forum: "Time is always GMT"; users also reported a GMT+2 offset in web downloads vs GMT in the JForex manager | **No** (old, conflicting, forum) | dukascopy.com JForex forum p=64530 |
| Bid/ask fields | CSV header quoted by users: `Time,Ask,Bid,AskVolume,BidVolume` | Partly (forum quote) | same |
| Official tick download limit | "limited by one day only" (old support reply) | No (may have changed) | same |
| `datafeed.dukascopy.com/.../HHh_ticks.bi5` | Used by community tools; LZMA files, month directory 0-indexed; per-hour. **No official specification found** | **No** (unofficial endpoint) | TickVault README (month 0-indexed, LZMA, original files kept) |
| bi5 record layout | Community-reported 20-byte big-endian records (ms offset, ask, bid, ask volume, bid volume). Field order matches the CSV header; widths/endianness not confirmed by any source read this session | **No** | — |
| XAUUSD price scale | Community tool states resolution 0.001, i.e. divisor 1000 | **No** | TickVault README |
| Licence | ToU s.3: no copying/recirculating/publishing; no scrapers/robots/bots/data mining without prior express written consent; "may not be used to construct a database of any kind"; download for own non-commercial use; s.4(i) personal use only | Yes (official text, as returned by the fetch tool; legal reading is for the data owner) | dukascopy.com/swiss/english/legal-pages/terms-of-use/ |
| Reachability from cloud | `HEAD https://datafeed.dukascopy.com/...` -> proxy `CONNECT tunnel failed, response 403` | Yes (observed) | session |

Consequences: (a) acquisition BLOCKED; (b) the adapter takes the operator's original files and
**requires** the operator to declare timestamp semantics, price divisor and layout confirmation;
(c) raw data must not be committed or redistributed (licence) — only manifests/hashes go to Git.
CSV export parsing is NOT implemented because its timestamp text format is unverified.
