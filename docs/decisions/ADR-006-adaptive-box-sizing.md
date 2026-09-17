# ADR-006 — Adaptive box sizing (fixed baseline + ATR-derived candidate)

Status: accepted for P4 implementation; independent review pending
Task: [P4 — Adaptive Box](../../tasks/P4-adaptive-box.md)
Sources: [requirements FR-03](../requirements.md), [adaptive research note](../research/adaptive-box.md), [P3 ADR-005](./ADR-005-pnf-fixed-box-rules.md)

## Decision

- Fixed mode is baseline and must match P3 output.
- Adaptive mode uses a causal ATR-derived candidate from past-only price deltas.
- Effective box applies per event at processing time; historical columns are never rewritten inside the same run.
- Every P&F transition records `effective_box_size` and `sizing_rule_version`.

## Adaptive formula (candidate, not profitability claim)

- Input: normalized event stream with explicit price source.
- True range proxy (tick stream): `abs(price_t - price_{t-1})`.
- Warm-up:
  - no previous price -> fixed baseline box
  - fewer than `atr_period` deltas -> hold last effective box
- Ready state:
  - `atr = average(last atr_period true ranges)`
  - `candidate = atr * atr_multiplier`
  - clamp to `[min_box_size, max_box_size]`
  - quantize to configured `price_precision`
  - if candidate/quantized is zero or invalid -> hold last effective box

## Consequences and limits

- This is a volatility proxy candidate for research only; it does not claim profitability.
- Different inputs (bars with true high/low sequencing) may require another ADR update.
- Prefix invariance must hold: appending future events cannot change previous transitions.
- Snapshot/restart must reproduce the same states and transitions as continuous processing.

## Validation expected in P4

- Fixed mode parity with P3 baseline fixtures.
- Warm-up, zero-volatility, clamp boundary, and gap move tests.
- Prefix invariance and snapshot/restart deterministic parity for adaptive mode.
- Explicit transition trace with effective box size and sizing rule version.

