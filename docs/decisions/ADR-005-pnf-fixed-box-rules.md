# ADR-005 — P&F fixed-box deterministic rules

Status: accepted for P3 implementation; independent review pending
Task: [P3 — P&F Engine](../../tasks/P3-pnf-engine.md)
Sources: [requirements FR-02](../requirements.md), [ADR-001](./ADR-001-pnf.md), [P2 contract ADR-004](./ADR-004-market-data.md)

## Decision

P3 uses a pure incremental Point & Figure core with explicit, versioned rules:

- Input stream is ordered [NormalizedPriceEvent](../../packages/nexora/market_data/models.py) from P2.
- A symbol state is independent from other symbols.
- Price values are quantized to `price_precision` and compared using `box_size`.
- First column direction is not assumed. It is seeded only after price moves at least one full box from the seed price.
- Exact thresholds are inclusive:
  - extension when `price >= current_high + box` for X or `price <= current_low - box` for O
  - reversal when `price <= current_high - reversal*box` for X or `price >= current_low + reversal*box` for O
- Multi-box moves are processed in one deterministic transition with explicit `boxes_moved`.
- Duplicate events are ignored (`is_duplicate=true`); out-of-order events are rejected explicitly.
- Engine never reorders history and never reads wall clock, network, DB, or UI state.

## Rule table

### Seed and grid
- `seed_price` is the first accepted event price for `(symbol, config_version)`.
- If movement from seed is less than one box, no column is created.
- If movement reaches at least one box:
  - Up move: create X column from `seed_price` to `seed_price + n*box`
  - Down move: create O column from `seed_price` to `seed_price - n*box`
  - `n = floor(|price-seed|/box)` and `n >= 1`

### Extension
- X column extends by `n = floor((price-current_high)/box)` when `n >= 1`.
- O column extends by `n = floor((current_low-price)/box)` when `n >= 1`.

### Reversal
- X reverses to O when `price <= current_high - reversal*box`.
  - `n = floor((current_high-price)/box)`, `n >= reversal`
  - new O column high = `current_high - box`, low = `current_high - n*box`
- O reverses to X when `price >= current_low + reversal*box`.
  - `n = floor((price-current_low)/box)`, `n >= reversal`
  - new X column low = `current_low + box`, high = `current_low + n*box`

## Consequences

- No hidden 3-box default. `reversal_boxes` is required in config.
- Price source is explicit in config and validated against incoming events.
- Snapshot + restart uses serialized engine state only; continuous and restarted replay must match.
- Out-of-order rejection and duplicate ignore preserve deterministic behavior from P2 policy.

## Validation expected in P3

- Golden fixtures for flat/rise/fall/exact threshold/reversal/multi-box gap.
- Invalid config and invalid event ordering cases.
- Snapshot/restart parity against continuous replay.
- Symbol isolation and deterministic equality for same input/config/version.

