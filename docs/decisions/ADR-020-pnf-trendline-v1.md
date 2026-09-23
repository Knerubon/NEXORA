# ADR-020 — P&F Trendline Engine V1 (Confirmed-Pivot Price-Slope Convention)

Status: accepted
Date: 2026-09-23
Related: [ADR-005](./ADR-005-pnf-fixed-box-rules.md) (P&F grid formulas), [ADR-006](./ADR-006-adaptive-box-sizing.md) (adaptive box, prefix invariance), [ADR-009](./ADR-009-market-structure-lifecycle.md) (Structure lifecycle — the direct precedent this ADR extends)
Sources: `packages/nexora/structure/{models,engine}.py`, `packages/nexora/pnf/{models,engine}.py`

## Context

NEXORA needs a deterministic trendline layer derived from confirmed P&F structure, as the foundation for a later Entry Readiness layer and AI Analyst (neither in scope of this ADR). This ADR freezes only the deterministic trendline formulas — anchor selection, projection, break, touch, and retest. AI is out of scope for every formula here: no AI, no LLM provider, no Entry Readiness semantics are decided in this document.

## Decision 1 — Convention

**Confirmed-Pivot Price-Slope Trendline.** Classic 45°/box-count trendlines are explicitly rejected for V1, because Adaptive Box means `effective_box_size` is not constant across a chart's history, and a box-count line is only a straight line in price terms when box size never changes. The price-slope convention anchors on two already-causally-confirmed pivots and projects a constant price-per-column slope, independent of box size by construction.

## Decision 2 — Anchor rules

Given `structure.pivots` — `StructureEngine`'s existing, causally-confirmed, append-only, chronologically-ordered pivot sequence (consumed as-is; **never rediscovered**):

```
lows  = [p for p in structure.pivots if p.kind == "low"]   # chronological
highs = [p for p in structure.pivots if p.kind == "high"]  # chronological
```

**Bullish Support Line anchors:** `anchor_a = lows[-2]`, `anchor_b = lows[-1]`.
Valid iff `anchor_b.price > anchor_a.price` (higher low).

**Bearish Resistance Line anchors:** `anchor_a = highs[-2]`, `anchor_b = highs[-1]`.
Valid iff `anchor_b.price < anchor_a.price` (lower high).

**No backward search:** anchor candidates are always exactly the two most recent same-kind pivots at the moment of evaluation. If that pair does not satisfy the higher-low/lower-high test, no line is formed — the engine never scans further back (`lows[-3]`, `lows[-4]`, …) looking for a pair that would validate. Re-evaluation happens exactly once per newly confirmed same-kind pivot.

**Determinism:** `structure.pivots` is already a proven-deterministic function of the processed P&F state, so `lows[-2:]`/`highs[-2:]` are trivially deterministic. Same processed state ⇒ same anchors ⇒ same line.

**Replacement reuses the same rule:** whenever no line is currently `active`/`broken`/`retesting`/`retest_held`/`retest_failed` for a direction, the identical two-most-recent-pivot rule is re-applied on every new same-kind pivot until it validates. This may legitimately reuse the prior line's `anchor_b` as the new `anchor_a` — not special-cased, it falls out of the rule naturally.

## Decision 3 — Projection formula

`column_id` is verified (`packages/nexora/pnf/engine.py`) to increment by exactly 1 per new column (seed → 1; extension → unchanged; reversal → `current.column_id + 1`) and stay constant across extension transitions within a column — a valid, gap-free integer column index.

```
slope = (anchor_b.price − anchor_a.price) / (anchor_b.column_id − anchor_a.column_id)   # Decimal price per column
projected_price(c) = anchor_b.price + slope × (c − anchor_b.column_id)                   # for c ≥ anchor_b.column_id
```

All arithmetic is exact `Decimal` — never `float`.

## Decision 4 — Break rule

Strict, no threshold, evaluated on already-confirmed transitions only (no lookahead):

```
Bullish support:    BREAK  iff  price_T < projected_price(c_T)
Bearish resistance: BREAK  iff  price_T > projected_price(c_T)
```

Equality is not a break (mirrors `StructureEngine._invalidate_levels`'s existing strict-inequality convention). **Break is always evaluated before Touch; a small penetration is still a break, never reinterpreted as a touch merely because it is less than one box away.**

## Decision 5 — Touch rule (frozen)

```
box = transition.effective_box_size          # box size active AT this transition
distance = price_T − projected_price(c_T)    # signed

Bullish support, evaluated only when NOT already a break:
    TOUCH  iff  0 ≤ distance < box

Bearish resistance, symmetric:
    TOUCH  iff  0 ≤ −distance < box
```

Tolerance is frozen at exactly **1 × `effective_box_size`**. Touch is checked strictly after break, on the non-broken side only, so a touch can never simultaneously be a break. Touch is recorded as an evidence event (`touch_columns`) on the still-`active` line, not a separate durable lifecycle state.

## Decision 6 — Retest lifecycle (V2, final)

**Entry** (from `broken`):

```
box = transition.effective_box_size          # box size of the entry-evaluating transition
distance = price_T − projected_price(c_T)    # signed; geometry uses the line's original, frozen anchors/slope

RETESTING entered  iff  |distance| < box
```

**Resolution** (from `retesting`), symmetric and directional, using the box size of the *resolving* transition:

```
Bullish support (break occurred BELOW the line):
    RETEST_HELD    iff  distance ≤ −box     (price moved further below → broken support behaved as resistance)
    RETEST_FAILED  iff  distance ≥ +box     (price reclaimed above → the broken support line was reclaimed)

Bearish resistance (break occurred ABOVE the line):
    RETEST_HELD    iff  distance ≥ +box     (price moved further above → broken resistance behaved as support)
    RETEST_FAILED  iff  distance ≤ −box     (price fell back below → the broken resistance line was reclaimed)
```

Entry (`|distance| < box`) and resolution (`distance` beyond `±box`) are mutually exclusive by construction — a transition can never simultaneously enter and resolve a retest. Transitions that remain within `|distance| < box` while `retesting` change nothing. Once resolved, `retest_held`/`retest_failed` is **terminal for further break/touch/retest evaluation** against that line — the only further transition is `→ replaced`.

`retest_outcome: Literal["held", "failed", "none"]` is a **sticky** field: set exactly once when a retest resolves, defaulting to `"none"` if a line is replaced without ever resolving a retest, and **never overwritten again**, including after `state` becomes `replaced`.

### Boundary examples

**Bullish support** — `anchor_a=(col 2, 100.0)`, `anchor_b=(col 5, 101.5)` (higher low). `slope = 0.5/column`. `projected(c) = 101.5 + 0.5×(c−5)`.
- Col 8: `projected=103.0`; `price_T=102.7` → `102.7 < 103.0` → **BREAK** (even though `|distance|=0.3 < box`).
- Col 9: `projected=103.5`; `price_T=103.2` → `distance=−0.3`, `|distance|<box(1.0)` → **`retesting`**.
- Col 10 (held): `projected=104.0`; `price_T=102.0` → `distance=−2.0 ≤ −1.0` → **`retest_held`**.
- Col 10 (failed, alt.): `price_T=105.5` → `distance=+1.5 ≥ +1.0` → **`retest_failed`**.

**Bearish resistance** — `anchor_a=(col 2, 110.0)`, `anchor_b=(col 5, 108.0)` (lower high). `slope ≈ −0.6667/column`. `projected(c) = 108.0 − 0.6667×(c−5)`.
- Col 8: `projected≈106.0`; `price_T=106.5` → `106.5 > 106.0` → **BREAK**.
- Col 9: `projected≈105.333`; `price_T=105.8` → `distance=+0.467 < box(1.0)` → **`retesting`**.
- Col 10 (held): `projected≈104.667`; `price_T=106.0` → `distance=+1.333 ≥ +1.0` → **`retest_held`**.
- Col 10 (failed, alt.): `price_T=103.5` → `distance=−1.167 ≤ −1.0` → **`retest_failed`**.

## Decision 7 — Invalidation ("invalidated" removed from V1)

The prior draft's `invalidated` state had exactly one use — the terminal outcome of a resolved retest — and no other distinct meaning. It is therefore **removed** from the V1 state enum, replaced by the two machine-queryable outcomes `retest_held`/`retest_failed` above.

## Decision 8 — Replacement lifecycle

A fresh valid same-kind anchor pair (Decision 2) may create a new line whenever the previous line is in `broken`, `retest_held`, or `retest_failed` (replacement need not wait for a retest to resolve). The previous line is stamped `state="replaced"`; its `retest_outcome` (`"held"`, `"failed"`, or `"none"`) is left exactly as it already was — never reset, never recomputed. No other field on the replaced line is ever mutated. A wholly new `TrendlineLine` record represents the new line; the old record's geometry (`anchor_a`, `anchor_b`, `slope`, `break_column`, etc.) is frozen forever.

## Decision 9 — State model

```python
TrendlineKind = Literal["bullish_support", "bearish_resistance"]
TrendlineLifecycleState = Literal[
    "forming", "active", "broken", "retesting", "retest_held", "retest_failed", "replaced"
]
RetestOutcome = Literal["held", "failed", "none"]
```

| From state | Trigger | To state |
|---|---|---|
| *(none)* | 2 most-recent same-kind pivots validate (Decision 2) | `active` |
| `active` | `0 ≤ distance < box` on correct side | `active` (+ `touch_columns` appended) |
| `active` | transition crosses line (Decision 4) | `broken` |
| `broken` | `|distance| < box` | `retesting` |
| `broken` | fresh valid anchor pair | old → `replaced` (`retest_outcome="none"`); new → `active` |
| `retesting` | `distance` beyond `±box`, confirming side | `retest_held` |
| `retesting` | `distance` beyond `±box`, reclaiming side | `retest_failed` |
| `retest_held` / `retest_failed` | fresh valid anchor pair | old → `replaced` (`retest_outcome` preserved); new → `active` |

## Decision 10 — No-lookahead invariant

Every decision above (anchor selection, projection, touch, break, retest) is a pure function of transitions/pivots already processed as of the current step — mirrors `StructureEngine`'s one-step-delayed pivot confirmation. Required property: **prefix invariance** (the same named property ADR-006 mandates for Adaptive Box) — processing events `1..k` must produce identical recorded state at step `k` regardless of what arrives afterward; appending future events must never rewrite a past `TrendlineLine` record.

## Decision 11 — Adaptive Box interaction

The projection (Decision 3) operates in raw Decimal price-per-column-index space and is box-size-independent by construction — no special-casing is needed when `effective_box_size` changes between columns. Box size enters only the touch/retest tolerance width (Decisions 5–6), intentionally using the box size active **at the transition being evaluated**, so the tolerance band self-consistently rescales with the chart's current resolution.

## Decision 12 — Entry Readiness boundary (not decided here)

Retest outcomes may only ever reduce or preserve Signal-derived permission — never create it. `WAIT → READY` remains structurally impossible. No new trading-entry semantics are decided from `RETEST_HELD` in this ADR; that is an explicit, separate future decision.

## Decision 13 — Experience Engine boundary

Trendline structured state (including `retest_outcome`) may be added to Experience's frozen `context` for observational visibility only, exactly like `matrix`/`structure`/`regime` today — additively, without changing `fingerprint()`'s hash, and without any AI-generated text ever being read by any Experience function (`fingerprint`, `freeze`, `plan`, `measure`, `advance`).

## Consequences

- Zero changes to P&F construction, Adaptive Box formulas, Matrix, Regime, Signal, Risk, Paper, Backtest, Experience Engine semantics, MT5 adapter, or REMOTE1/Tailscale.
- All arithmetic is exact `Decimal`; no floating-point epsilon anywhere in this model.
- `retest_held` vs. `retest_failed` is now a durable, machine-queryable distinction (not free-text), enabling future Experience/research analysis without committing to any live-decision feedback in V1.

## Validation expected in Phase 1

Prefix invariance (no-lookahead) · anchor tie-break exactness (including the negative "no backward search" case) · exact Decimal slope/projection arithmetic on fixture pivot pairs · break-before-touch ordering including small-penetration-is-still-break · touch/retest boundary fixtures at `box − ε`, `box`, `box + ε` in Decimal terms (no float epsilon) · bullish and bearish `retest_held`/`retest_failed` resolution · sticky `retest_outcome` surviving `replaced` · replacement chaining from `broken`, `retest_held`, and `retest_failed` · Adaptive-box-change fixtures confirming projection is unaffected while tolerance width rescales · incremental `process()` sequence equals full `replay()` + `snapshot()`.
