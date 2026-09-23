# P&F Chart Visual Intelligence V1

Status: implemented on `claude/chart-visual-intelligence-v1` (DEV-CHART). Phase 1 review decisions 1–5 by Rin are frozen for V1.

The live P&F chart (`apps/web/app/page.tsx` → `StructureChart`) renders existing deterministic backend evidence as visual layers. The frontend is a renderer: it detects no pattern, derives no trendline, evaluates no slope, and creates no signal, break or trading state. It changes no BUY/SELL/WAIT logic, Entry Readiness, Pattern or Trendline calculation, risk rule or execution path.

## Source-of-truth mapping

| Visual element | API field (`/state` and `state_snapshot` → `research.output`) | Backend model | Owning engine |
|---|---|---|---|
| Bullish support trendline | `trendline.active_bullish` | `TrendlineLine` | `TrendlineEngine` ([ADR-020](decisions/ADR-020-pnf-trendline-v1.md)) |
| Bearish resistance trendline | `trendline.active_bearish` | `TrendlineLine` | `TrendlineEngine` |
| Trendline break marker | `trendline.active_*.break_column`, `break_transition_id` → `transitions[].identity_key` | `TrendlineLine`, `PnfTransition` | `TrendlineEngine` |
| Support / resistance bands (existing) | `structure.levels[status="confirmed"]` | `CandidateLevel` | `StructureEngine` ([ADR-009](decisions/ADR-009-market-structure-lifecycle.md)) |
| Pattern bracket | `signals.decision.patterns[]` | `PatternEvidence` | `SignalEngine._patterns` ([ADR-011](decisions/ADR-011-signal-evidence-policy.md)); interim source, see below |
| Pattern column position | `PatternEvidence.source_data_reference` → `transitions[].identity_key` → `column_id` | `PnfTransition` | Identity lookup only |

Geometry:
- A trendline is drawn from `anchor_a (column_id, price)` to `(anchor_b.column_id + age_columns, projected_price_at_latest_column)`. Per ADR-020, `age_columns` is measured from `anchor_b` to the last evaluated transition's column, and the projection is the engine's price at that column. Both endpoints are backend values, so no slope is evaluated in the browser.
- Overlay prices use the X/O glyph convention: price `p` is drawn at the centre of the display row above `y(p)`.
- P&F `column_id` increases by exactly 1 per column. An anchor left of the 60-column window therefore gets a negative x, and a clip path hides the off-window part.

## INTERIM PATTERN VISUALIZATION SOURCE

`research.output.signals.decision.patterns[]` is used for V1 visualization only. It is **not** the frozen P&F Pattern Engine contract that [AGENTS.md](../AGENTS.md) §2–3 require.
- Only canonical repository names are shown (`double_top`, `double_bottom`, `head_and_shoulders`, `inverse_head_and_shoulders`, `triangle_breakout`, `triangle_breakdown`), formatted with underscores replaced by spaces.
- The repository `double_top` is a bearish three-pivot reversal. It is never labelled "Double Top Breakout".
- The frontend detects no additional patterns and does not reinterpret existing ones.
- Current backend behaviour exposes the current decision's patterns only. Markers disappear when `decision.patterns` changes, and no pattern history is invented.
- Every `|`-separated `source_data_reference` must resolve to a transition `identity_key`. If any reference is unresolved, no marker is drawn; nothing is guessed. Double top/bottom reference only the confirming pivot, so their bracket spans that one column across `price_low`–`price_high`.

## Layers and controls

Trendline, S/R and Patterns can each be toggled; all are ON by default. Toggles are UI state only: they hide drawings and never disable, alter or re-request backend engines. Trendline breaks belong to the Trendline layer.

## Clutter rule (deterministic)

- Trendlines: at most one line per kind (`active_bullish`, `active_bearish`). `history` and replaced lines are hidden.
- S/R: confirmed levels only, the existing maximum of 12, and the existing band visualization is kept unchanged.
- Patterns: the current decision only. Duplicates collapse to one stable key `pattern:<evidence_code>:<source_data_reference>`.
- Stable keys: `trendline:<line_id>` and `break:<line_id>:<break_transition_id>`. There is no fade animation.

## Evidence popup

Click, tap, Enter or Space on a marker opens a popup listing only backend fields and their source. For a trendline: kind, state, anchors, projected price and column, touch columns, break column, retest outcome, evidence and config version. For a pattern: canonical type, direction, relation, price range, resolved columns, confirmation time, evidence code and algorithm version.

The popup gives no AI interpretation and no trading recommendation. It closes on ×, Escape, clicking the chart background, hiding its layer, or when its evidence is gone from a newer snapshot; stale evidence is never shown. At widths of 540px or less it becomes a full-width sheet at the top of the chart.

## Realtime

Overlays consume the existing `StructureChart` `output` prop, fed by the single existing `/api/nexora/ws/events` connection. They open no additional WebSocket, do no polling and run no fetch loop; a test asserts this statically. The overlay model is memoized on `trendline`, `transitions` and `signals`.

## Regression invariant

`tests/page-chart-regression.test.mjs` asserts:
- With no overlay data, or with the Trendline and Patterns layers OFF, the chart SVG is byte-identical to baseline `8dd31a2`.
- With all layers OFF, it equals the baseline minus the S/R bands.
- With layers ON, the only difference is one appended `<g data-chart-overlays>` group.

## Out of scope / backend contract gaps

- **Generic ▲ Breakout / ▼ Breakdown.** There is no backend event. Structure `status="invalidated"` is never shown as a breakout or breakdown. This needs a backend contract (StructureEngine or the Pattern Engine) that defines the semantics with column and transition references.
- **Pattern column_index, stable id and history.** These need the frozen P&F Pattern Engine contract (DEV-PNF).
- **Classic P&F patterns** (Double Top Breakout, Double Bottom Breakdown, Triple Top/Bottom, Catapult) are not represented by the backend.
- **Per-pivot HH/HL/LH/LL labels.** These exist only as aggregate signal evidence codes, with no pivot or column reference.
- **Observed, not changed:** the existing S/R band `<title>` uses mixed children, which React 19 does not render as tooltip text. It is left untouched to preserve the regression invariant.
