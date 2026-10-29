# Oracle specification (finding B-M4)

Reviewer B found that the harness "oracle" restated the extractor line for line and shared at least
one blind spot: neither counts a non-adjacent exact duplicate. This page states the rules the
package's own oracle follows, independently of the extractor, and says honestly which reference
functions are restatements.

## 1. Scope

The duplicate and ordering oracle is `tests/windows_integration_readiness/duplicate_oracle.py`. It
classifies a sequence of tick observations. It does **not** change the extractor, does not decide
what a quality threshold is (that is MD-6 / PR #94, DEP-05), and does not decide whether a duplicate
is harmful. It only counts.

## 2. Canonical tick identity

An observation is a record with `time_msc` (int), `bid`, `ask` (decimal text) and optional
`flags` (int). Its identity is the tuple

```
(time_msc, canonical(bid), canonical(ask), flags or ABSENT)
```

Field selection:

- `time_msc` is the only time field in the identity. `time` (seconds) is redundant and is checked
  for agreement separately (`reference_model.check_time_fields`).
- `flags` is part of the identity. Two observations that differ only in `flags` are **not** exact
  duplicates (Reviewer B: the adjacency model counted them as exact).
- A missing `flags` is the distinct value `ABSENT`, so it never equals a present value.
- `last`, `volume`, `volume_real` are not in the identity. The extractor does not carry them into
  its canonical tick, and this package makes no claim about them.

Normalization (`canonical_decimal`):

- Input must be decimal **text**. Binary floats, bools, `None` and other types are malformed, because
  their decimal value is ambiguous. This also stops `True` from being read as a price.
- Equivalent spellings are equal: `1.10`, `1.1`, `+1.1`, `01.1`, `1.1E0`, `11E-1` all become `1.1`.
  `-0`, `0.000` become `0`. No exponent is ever produced.
- `NaN`, `Infinity`, `-Infinity` are kept as named values and compare equal to themselves (so two
  identical NaN ticks are exact duplicates). They are never dropped.
- The method (`Decimal` at precision 1000, then fixed-point formatting and stripping zeros) differs
  on purpose from `reference_model.decimal_text`, which works from `Decimal(repr(float))` digits.

## 3. Classes and counts

Computed over the **whole** sequence. Equal identities are grouped by sorting, so the distance
between repeats is irrelevant.

| Count | Rule |
|---|---|
| `malformed` | Missing `time_msc`/`bid`/`ask`, wrong types, or unparsable quotes. Counted and excluded from every other count. |
| `exact_duplicate` | For each identity seen `n` times, `n - 1`. Adjacent, non-adjacent and far-apart repeats count the same. |
| `same_ts_diff_quote` | For each `time_msc`, the number of distinct `(bid, ask)` pairs minus 1. Several quotes in one millisecond can be legitimate, so this is a count, not a defect. |
| `same_quote_diff_flags` | For each `(time_msc, bid, ask)`, the number of distinct `flags` values (including `ABSENT`) minus 1. These are conflicting records. |
| `out_of_order` | Observations whose `time_msc` is lower than the maximum `time_msc` seen before them. Adjacency is not enough: `T3, T1, T2` has 2, not 1. |
| (none) | The same quote at a different timestamp is a legitimate update and is not reported. |

## 4. Golden vectors

`test_duplicate_oracle.py` holds D1-D10. Each expected count was derived by hand from section 3,
and the derivation is written next to the vector:

| Vector | Content | Expected (non-zero) |
|---|---|---|
| D1 | `(T1,A) (T1,A)` | exact 1 |
| D2 | `(T1,A) (T1,B) (T1,A)` | exact 1, same_ts 1 |
| D3 | `(T1,A) (T2,B) (T1,A) (T3,C) (T1,A)` | exact 2, out_of_order 2 |
| D4 | `(T1,A) (T1,B)` | same_ts 1 |
| D5 | `(T1,A) (T2,A)` | nothing |
| D6 | `(T3,A) (T1,B) (T2,C)` | out_of_order 2 |
| D7 | `(T1,A,6) (T1,A,ABSENT) (T2, ask missing)` | same_quote_diff_flags 1, malformed 1 |
| D8 | `1.10/1.20`, `1.1/1.2`, `1.1E0/+1.20` at T1 | exact 2 |
| D9 | 10 000 distinct ticks, tick 0 repeated at position 5000 and at the end | exact 2, out_of_order 2 |
| D10 | Four mutants of the oracle (adjacency-only duplicates, flags ignored, no canonicalization, adjacency-only ordering) | each killed by at least one of D1-D9 |

**Known shared defect, detected.** The adjacency model `reference_model.order_report`, restated from
the extractor's `quality_report`, reports **fewer** exact duplicates than the specification on D2,
D3 and D9, and counts a flags-only difference as an exact duplicate. The extractor at an earlier #96 head had the same adjacency logic: cross-track defect **EXT-DEF-1**.
At the pinned candidate (`dependency-manifest.json`) a differential run of the real `QualityAccumulator`
against this oracle on D1-D9 gives equal exact-duplicate, same-timestamp and flags counts, so EXT-DEF-1
is recorded as resolved there ([DEPENDENCY-REGISTER.md](DEPENDENCY-REGISTER.md)). PR #96 is not modified by this package.

**Out-of-order metric: two definitions, no choice made here.** The oracle counts ticks whose
timestamp is below the running maximum (`out_of_order`). #96's `non_monotonic_count` counts adjacent
descents. They agree on D3 (2/2) and D9 (2/2) and differ on D6 `(T3,A) (T1,B) (T2,C)`: the oracle gives 2, #96 gives 1.
Both numbers are pinned by `test_v9_out_of_order_metric.py`. Which definition is the contract is a
decision for Rin / the #96 owner; this package neither picks one nor treats the two as equal.

## 5. Independence ledger of `reference_model.py`

| Function | Class | Independent cross-check |
|---|---|---|
| `check_tick_layout` | SPECIFIED | Documented MT5 layout in `vectors.py` (NOT VERIFIED against a terminal) |
| `decimal_text` | RESTATED | Hand-written `DECIMAL_TEXT_VECTORS`; round-trip test; `duplicate_oracle.canonical_decimal` uses a different method |
| `classify_quote` | RESTATED | Hand-written `QUOTE_VECTORS`. Extreme spreads are not classified, by design (no threshold may be invented, DEP-05). |
| `validate_segments` | RESTATED | Hand-written segment vectors. The offset bound is SPECIFIED (Python's own `datetime.timezone` limit, below 24 h). |
| `check_time_fields` | SPECIFIED | `time == time_msc // 1000` |
| `to_utc_msc` | RESTATED | Hand-computed conversions in `test_case3_time_conversion.py` |
| `derived_fold_ranges` | RESTATED | Brute-force clock simulation in `test_oracle_independence.py`, which never uses the formula |
| `check_window` | SPECIFIED | Refuses empty or reversed windows, windows outside declared segments, and fold or attested ranges, with the extractor's codes |
| `split_around_folds` | SPECIFIED | Every returned window must pass `check_window` with the same attestation |
| `chunk_windows` | SPECIFIED | Contiguity and coverage tests |
| `encode_tick` | HARNESS | Harness-only byte format; says nothing about the extractor's bytes |
| `build_chunks` | HARNESS | Boundary and out-of-window tests |
| `content_sha256` | HARNESS | Chunking-invariance tests (harness expectation, confirm at `<SHA>`) |
| `order_report` | RESTATED | `duplicate_oracle.classify`; divergence on D2, D3 and D9 is the documented shared defect |
| `run_with_cleanup` | HARNESS | Fake-module call log |

RESTATED means a shared conceptual error would pass both the model and the extractor. The listed
cross-check is what stands between that error and a false PASS.

## 6. Refusal-code alignment (minor F5)

The reference model now uses the extractor's refusal codes as read at the pinned candidate:
`segments_not_sorted`, `segments_overlap`, `time_conversion_does_not_cover_window`,
`window_touches_derived_fold_range`, `window_touches_ambiguous_raw_range`,
`invalid_extraction_request`. Not modelled: the segment `basis` field and
`unsupported_offset_basis`. Package-only codes: `offset_out_of_range`, `time_fields_conflict`
(EXT-DEF-2 and EXT-DEF-3: the extractor checks neither).
