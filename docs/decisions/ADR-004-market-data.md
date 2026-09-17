# ADR-004 — Market data normalization and replay contract

Status: accepted for P2 implementation; independent review pending
Task: [P2 — Market Data](../../tasks/P2-market-data.md)
Sources: [requirements FR-01](../requirements.md), [architecture persistence/event flow](../architecture.md)

## Decision

- Normalize all market-data timestamps to UTC.
- Require explicit price-source selection per stream:
  - ticks use a configured tick price source
  - bars use a configured bar price source
- Preserve raw observations immutably and store normalized events separately.
- Treat duplicates as raw-retained / normalized-deduped events.
- Treat out-of-order observations as valid observations that are flagged and replayed deterministically.
- Treat sequence gaps as observable gaps that can be backfilled explicitly.
- Use replay ordering by `(event_time, received_at, source_sequence, source_order, source_event_id, identity_key)`.

## Rationale

P2 needs a lossless replay boundary without silently inventing trading semantics.
UTC normalization keeps cross-machine replay stable on Windows hosts and avoids local timezone drift.
Explicit price-source selection prevents hidden assumptions for bid/ask/ohlc mapping.
Raw-retained / normalized-deduped storage preserves auditability while preventing duplicate derived processing.
Deterministic replay order is needed so restart/backfill produces stable results independent of ingestion timing.

## Consequences

- Market data adapters must reject invalid, zero, negative, NaN, or crossed prices.
- Duplicate observations may be written to the raw log but must not produce duplicate normalized rows.
- Replay consumers must read normalized events in deterministic order rather than insertion order.
- Backfill is an explicit adapter capability, not an automatic hidden retry loop.
- MT5 integration remains read-only; no order methods are allowed in this phase.

## Validation evidence expected in P2

- Canonical tick/bar fixtures cover mapping, timezone, precision, invalid price, duplicate, out-of-order, and gap cases.
- Repository round-trip tests prove raw persistence plus normalized replay stability.
- Fake adapter tests prove reconnect and backfill behavior without network or broker credentials.
- MT5 adapter smoke stays read-only and does not call order APIs.

