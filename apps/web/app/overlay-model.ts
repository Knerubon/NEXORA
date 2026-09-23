// View model for P&F chart overlays (Chart Visual Intelligence V1).
//
// Presentation only. Every value comes from an existing backend contract:
// - trendlines:  research.output.trendline (TrendlineEngine, ADR-020)
// - patterns:    research.output.signals.decision.patterns[] (SignalEngine,
//                ADR-011) - the INTERIM PATTERN VISUALIZATION SOURCE, not the
//                frozen P&F Pattern Engine contract
// - positioning: research.output.transitions[].identity_key -> column_id
//                (identity lookup only)
// Nothing here detects, reinterprets or scores a pattern, trendline or break.
// Unresolvable references render nothing; nothing is guessed.

export type OverlayLayers = { trendline: boolean; sr: boolean; patterns: boolean };
export const DEFAULT_LAYERS: OverlayLayers = { trendline: true, sr: true, patterns: true };
export const LAYERS_OFF: OverlayLayers = { trendline: false, sr: false, patterns: false };

export type TrendlineAnchor = { pivot_kind: string; price: string; column_id: number; source_pivot_id: string };
export type TrendlineLine = {
  line_id: string; kind: string; state: string;
  anchor_a: TrendlineAnchor; anchor_b: TrendlineAnchor;
  slope_price_per_column: string; projected_price_at_latest_column: string;
  touch_columns: number[]; break_column: number | null; break_transition_id: string | null;
  retest_column: number | null; retest_resolved_column: number | null; retest_outcome: string;
  age_columns: number; config_version: string; evidence: string[];
};
export type TrendlineSnapshot = { active_bullish?: TrendlineLine | null; active_bearish?: TrendlineLine | null };
export type PatternEvidence = {
  pattern_type: string; direction: string; relation: string;
  price_low: string; price_high: string; evidence_code: string;
  source_data_reference: string; algorithm_version: string;
  start_time: string; confirmation_time: string;
};
export type OverlayTransition = { column_id: number; to_price: string; identity_key?: string };
export type OverlayInput = {
  transitions?: readonly OverlayTransition[];
  trendline?: TrendlineSnapshot | null;
  signals?: { decision?: { patterns?: readonly Partial<PatternEvidence>[] } | null } | null;
};

export type Point = { column: number; price: number };
export type TrendlineOverlay = { key: string; line: TrendlineLine; from: Point; to: Point; latestColumn: number };
export type BreakOverlay = { key: string; line: TrendlineLine; column: number; price: number; transitionId: string };
export type PatternOverlay = { key: string; pattern: PatternEvidence; columns: number[]; priceLow: number; priceHigh: number };
export type OverlayModel = { trendlines: TrendlineOverlay[]; breaks: BreakOverlay[]; patterns: PatternOverlay[] };

const finite = (value: unknown): number | null => {
  const n = typeof value === "string" && value.trim() !== "" ? Number(value) : NaN;
  return Number.isFinite(n) ? n : null;
};
const isColumn = (value: unknown): value is number => Number.isSafeInteger(value);

function activeLines(trendline: TrendlineSnapshot | null | undefined): TrendlineLine[] {
  // Max one current line per kind: only active_bullish / active_bearish.
  // TrendlineSnapshot.history (historical/replaced lines) is never drawn.
  return [trendline?.active_bullish, trendline?.active_bearish].filter((l): l is TrendlineLine => Boolean(l && l.line_id));
}

export function trendlineOverlays(trendline: TrendlineSnapshot | null | undefined): TrendlineOverlay[] {
  const result: TrendlineOverlay[] = [];
  for (const line of activeLines(trendline)) {
    const a = line.anchor_a, b = line.anchor_b;
    const aPrice = finite(a?.price), projected = finite(line.projected_price_at_latest_column);
    if (aPrice === null || projected === null || !isColumn(a?.column_id) || !isColumn(b?.column_id) || !isColumn(line.age_columns)) continue;
    // ADR-020: age_columns = (column of the last evaluated transition) - anchor_b.column_id,
    // and projected_price_at_latest_column is the engine's projection at that column.
    // Both segment ends are therefore backend points; no slope is evaluated here.
    const latestColumn = b.column_id + line.age_columns;
    result.push({ key: `trendline:${line.line_id}`, line, latestColumn,
      from: { column: a.column_id, price: aPrice }, to: { column: latestColumn, price: projected } });
  }
  return result;
}

function transitionIndex(transitions: readonly OverlayTransition[] | undefined) {
  const index = new Map<string, OverlayTransition>();
  for (const t of transitions ?? []) if (t.identity_key) index.set(t.identity_key, t);
  return index;
}

export function breakOverlays(trendline: TrendlineSnapshot | null | undefined, transitions: readonly OverlayTransition[] | undefined): BreakOverlay[] {
  const index = transitionIndex(transitions);
  const result: BreakOverlay[] = [];
  for (const line of activeLines(trendline)) {
    if (!isColumn(line.break_column) || !line.break_transition_id) continue;
    const transition = index.get(line.break_transition_id);
    const price = finite(transition?.to_price);
    // The recorded break transition must exist and belong to the recorded break column.
    if (!transition || price === null || transition.column_id !== line.break_column) continue;
    result.push({ key: `break:${line.line_id}:${line.break_transition_id}`, line, column: line.break_column, price, transitionId: line.break_transition_id });
  }
  return result;
}

export function patternOverlays(patterns: readonly Partial<PatternEvidence>[] | undefined, transitions: readonly OverlayTransition[] | undefined): PatternOverlay[] {
  const index = transitionIndex(transitions);
  const seen = new Set<string>();
  const result: PatternOverlay[] = [];
  for (const p of patterns ?? []) {
    const low = finite(p.price_low), high = finite(p.price_high);
    if (!p.pattern_type || !p.evidence_code || !p.source_data_reference || low === null || high === null) continue;
    // IDENTITY LOOKUP: every referenced pivot transition must resolve, else no marker.
    const columns = p.source_data_reference.split("|").map((ref) => index.get(ref)?.column_id);
    if (!columns.length || columns.some((c) => !isColumn(c))) continue;
    const key = `pattern:${p.evidence_code}:${p.source_data_reference}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({ key, pattern: p as PatternEvidence, columns: [...new Set(columns as number[])].sort((x, y) => x - y),
      priceLow: Math.min(low, high), priceHigh: Math.max(low, high) });
  }
  return result;
}

export function buildOverlayModel(output: OverlayInput): OverlayModel {
  return {
    trendlines: trendlineOverlays(output.trendline),
    breaks: breakOverlays(output.trendline, output.transitions),
    patterns: patternOverlays(output.signals?.decision?.patterns, output.transitions),
  };
}

export function overlayKeys(model: OverlayModel): string[] {
  return [...model.trendlines, ...model.breaks, ...model.patterns].map((item) => item.key);
}

// Column x in the chart's visible window. P&F column_id increases by exactly 1
// per new column (pnf/engine.py), so a column left of the 60-column window gets
// a negative offset and the overlay clip path hides the off-window part.
export function columnX(visibleColumnIds: readonly number[], columnId: number): number | null {
  if (!visibleColumnIds.length) return null;
  const index = visibleColumnIds.indexOf(columnId);
  return 90 + (index >= 0 ? index : columnId - visibleColumnIds[0]) * 30;
}
