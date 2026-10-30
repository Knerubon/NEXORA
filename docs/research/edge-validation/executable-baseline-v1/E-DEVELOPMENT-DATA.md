# Development Dataset Readiness Plan

DRAFT / acquisition portion BLOCKED on exact missing inputs below. Owner has already authorized real historical DEVELOPMENT data. Holdout and quality gates remain. No historical price payload fetched, opened or replayed; no backtest run. Git source/docs cloned only; operational CSV payloads not read.

## Metadata-only audit and existing records

| Record | Inspected revision / result | Consequence |
|---|---|---|
| Main tracked dataset inventory | `36c90c59e2aa4f0d221c9c468a8379c5f57cbb87`; filename inventory identifies only FIX3 operational CSVs, no registered historical manifest/partition by tracked filename | CSVs are previously exposed operational evidence, not presumed development/holdout. External data availability not established |
| Prior Track B | `62a8c8628651250ad79e8b9c35bf3b4651029725` [TRACK-B](https://github.com/Knerubon/NEXORA/blob/62a8c8628651250ad79e8b9c35bf3b4651029725/docs/research/edge-validation/readiness-20261015/TRACK-B.md) | Prior inventory/custody plan, not current verification of external storage; do not reopen legacy 51.8GB journal |
| #90 Dukascopy pilot | `858777a6b461713ce098a066b0adad5cb10df80d` [PILOT-PLAN](https://github.com/Knerubon/NEXORA/blob/858777a6b461713ce098a066b0adad5cb10df80d/docs/research/market-history/dukascopy-pilot/PILOT-PLAN.md) and FINDINGS | Declared XAUUSD seven calendar days Sept7–13 2026 on provider clock, acquisition blocked in that historical report; exposed pipeline-only window, explicitly excluded from later evaluation. No verified UTC interpretation or development approval/holdout registry |
| #99 metadata/custody tooling | `d2e8ee9ef4f6eb438dc79c5e04ad43ed46de764d` [README](https://github.com/Knerubon/NEXORA/blob/d2e8ee9ef4f6eb438dc79c5e04ad43ed46de764d/docs/research/market-history/README.md) | Records no designated Pin Authority; commission unavailable, swap partial, sessions blocked, gaps open, history depth unknown. Documentary state, not verified current dataset availability |
| #102 / #103 | Exact manifest in WORKSTREAMS.json; security/Windows remediation still draft | No acceptance or ready data inferred from test counts/CI. PR #103 body advertises `70c1b49…` while API head at initial query is `e60dbd53…`; consume exact queried SHA, not body prose |
| #100 MD-6 | `fc4aba439d9ee9e7ff2ad337b1211451d29b002f` [register](https://github.com/Knerubon/NEXORA/blob/fc4aba439d9ee9e7ff2ad337b1211451d29b002f/docs/research/edge-validation/md6-data-gap-policy/MD6-DECISION-REGISTER.md) | DRAFT, all sign-offs PENDING; closure/gap/quarantine/exposure proposals are not selected thresholds |
| #79 / #81 / #87 | Existing manifest, split registry proposal, EDGE-VP-1 and prior Baseline candidate, exact SHAs in B/manifest | No approved dated development/holdout range found in inspected metadata; no authorization inferred from example ranges |

Dukascopy access/licensing/time/layout findings above are **historical repository claims** (2026-10-20), not fresh internet verification. No provider endpoint tested this session. Reverify official access/license/layout before any future acquisition; no assumption that feeds match globally. Broker/provider bid/ask, sessions, spread and quote history can differ.

## Exact missing inputs — no price fetch until all established

1. Canonical instrument_id, intended market/product and provider-specific symbol/binding; historic XAUUSD requirement/pilot is not approval of the intended V1 instrument.
2. Selected provider/source and lawful acquisition route; custodian/source authenticity evidence, source schema/time precision/scale. Supply export manifest/location if a bounded existing copy exists.
3. Approved development interval in half-open UTC `[start,end)`, separate allowed warmup interval, and data-use label; no broad all-history query.
4. Independently supplied complete protected holdout/equivalent-stream ranges and registry hash/authority, including aliases/copies/re-encodings, plus cutoff/exposure history. Unknown protected bounds are not an empty list.
5. Approved historical availability mapping; quote sides/order/ties, session/calendar and gap/stale/closure rules; effective-dated instrument/contract/currency capabilities and cost schedules. Missing costs can remain unknown for engineering, but block net claims.
6. Integration of accepted custody/DQ policy with a reviewed acquisition path. Existing G1/G2/G3 NO-GO and Pin Authority/key/Windows dependencies cannot be silently waived by general development-data authorization.

Items1–4 prevent a safe bounded payload request now; items5–6 govern quality/trust and later evidence eligibility. No generic permission question is needed: provide these concrete records under D6.

## Acquisition choices and smallest next steps

A. **Custodian research export (recommended if available):** accept only development-specific files with raw manifest, exact interval receipt, original source/license and protected-range exclusion proof. No full journal/drive transfer. Validate metadata before decoding payload. Do not import bundled holdout files.

B. **Provider export:** after source/time/layout/license verified and D6 bounds registered, request only approved development chunks. Dukascopy pilot code is draft and its declared pilot is not a fresh validation range. Other providers require separate adapter semantics; never fabricate ask/spread, backfill a different source as identical or expose holdout while discovering coverage.

C. **Operator MT5 history export:** separate named-SHA read-only integration-node procedure; this task does not start MT5, connect a terminal or place orders. Cloud consumes only approved non-production development export once custody/platform checks permit. No broker dependency in core.

## Bounded data flow and readiness gate

1. Before request: pin source/binding/development/warmup/protected registry and offset/fold margins; compare against all protected ranges. Refuse overlap or missing metadata before accessing provider. Metadata requests themselves must not expose price-derived holdout summaries.
2. Request exact chunks with source receipts/counts/timezone, truncation/partial-return markers and retrieval UTC; immutable raw bytes/hashes. No broad fallback download, no price logging outside development custody.
3. Normalize with original time/sequence retained and declared historical knowledge mapping (retrieved_at is provenance only). Preserve synchronized bid+ask and declared sampling; bars require independent bid and ask OHLC plus close/availability intervals and tick lineage.
4. Audit quality: missing/crossed/nonfinite sides, identity/order/timestamp conflicts, truncation, duplicate pages, gaps vs independently supported closures, coverage and capability/cost validity. Quarantine and re-entry/exposure attribution follow accepted policy. Do not repair/interpolate invisibly or treat an unknown cost as zero.
5. Seal source/raw/normalized hashes, adapter/source SHA, normalizer version, schema/units, clock, capabilities, cost state, quality receipt, parent identity and protected registry. Independently authenticate custody; hashes alone show integrity, not source truth or unseen status.
6. Only then allow bounded development harness testing under approved config/contracts. No holdout access/summary, no tuning to present profitable result, no execution unlock. Evidence-grade metrics wait for full protocol freeze/independent reproduction; development readiness is not G1/G2/G3 acceptance.

No current payload existence/coverage, original ingest clock, net costs or full data-ready status is asserted. This plan audits available repository metadata; no Library/remote storage/Windows inventory was performed or claimed.

Issue #77 [addendum comment](https://github.com/Knerubon/NEXORA/issues/77#issuecomment-6281442488) was read: it records XAUUSD/Dukascopy pilot scope of at most seven days, with larger extensions separately scoped. This corroborates the pilot identity, but supplies no protected holdout registry or approved UTC development bounds for the executable baseline. The present chat authorizes development data; no re-authorization of that permission is requested. PR #104 discussion API returned no comments during this inspection.
