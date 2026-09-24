"use client";

// P&F chart overlays (Chart Visual Intelligence V1). A renderer of existing
// backend evidence only - see overlay-model.ts for the source-of-truth rules.
// Consumes the existing state snapshot; opens no connection and fetches nothing.

import { useCallback, useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";
import {
  DEFAULT_LAYERS, buildOverlayModel, columnX,
  type BreakOverlay, type OverlayInput, type OverlayLayers, type OverlayModel, type PatternOverlay, type TrendlineOverlay,
} from "./overlay-model";

export type Inspection = { key: string; left: number; top: number; above: boolean };

const readable = (name: string) => name.replaceAll("_", " ");
const colors = { bullish_support: "#2563eb", bearish_resistance: "#d97706", pattern: "#7c3aed", break: "#111827" } as const;
const lineColor = (kind: string) => kind === "bullish_support" ? colors.bullish_support : colors.bearish_resistance;
// Layer visibility is UI state only: hiding a layer never touches backend state.
const visibleItems = (model: OverlayModel, layers: OverlayLayers) => ({
  trendlines: layers.trendline ? model.trendlines : [],
  breaks: layers.trendline ? model.breaks : [],
  patterns: layers.patterns ? model.patterns : [],
});
const findItem = (model: OverlayModel, layers: OverlayLayers, key: string) => {
  const v = visibleItems(model, layers);
  return v.trendlines.find((i) => i.key === key) ?? v.breaks.find((i) => i.key === key) ?? v.patterns.find((i) => i.key === key);
};

export function useChartOverlays(output: OverlayInput, initialLayers: OverlayLayers = DEFAULT_LAYERS) {
  const [layers, setLayers] = useState(initialLayers);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const { columns, trendline, transitions, signals } = output;
  const model = useMemo(() => buildOverlayModel({ columns, trendline, transitions, signals }), [columns, trendline, transitions, signals]);
  // Evidence gone from the latest snapshot (or its layer hidden): close, never show stale evidence.
  if (inspection && !findItem(model, layers, inspection.key)) setInspection(null);
  const toggle = useCallback((layer: keyof OverlayLayers) => setLayers((prior) => ({ ...prior, [layer]: !prior[layer] })), []);
  const close = useCallback(() => setInspection(null), []);
  return { layers, toggle, model, inspection, inspect: setInspection, close };
}

export function LayerControls({ layers, onToggle }: { layers: OverlayLayers; onToggle: (layer: keyof OverlayLayers) => void }) {
  const items: [keyof OverlayLayers, string][] = [["trendline", "Trendline"], ["sr", "S/R"], ["patterns", "Patterns"]];
  return <div className="chart-layers" role="group" aria-label="Chart layers">
    {items.map(([layer, label]) => <button key={layer} aria-pressed={layers[layer]} onClick={() => onToggle(layer)}>{label}</button>)}
  </div>;
}

function place(event: MouseEvent<Element> | KeyboardEvent<Element>, key: string): Inspection {
  const target = event.currentTarget as Element;
  const body = target.closest(".chart-body")?.getBoundingClientRect();
  const own = target.getBoundingClientRect();
  const clientX = "clientX" in event && event.clientX ? event.clientX : own.left + own.width / 2;
  const clientY = "clientY" in event && event.clientY ? event.clientY : own.top + own.height / 2;
  if (!body) return { key, left: 8, top: 8, above: false };
  const left = Math.max(8, Math.min(clientX - body.left + 12, body.width - 296));
  const top = clientY - body.top;
  return { key, left, top: Math.max(8, Math.min(top, body.height - 8)), above: top > body.height / 2 };
}

function activate(key: string, onInspect: (inspection: Inspection) => void) {
  return {
    role: "button", tabIndex: 0,
    onClick: (event: MouseEvent<SVGGElement>) => { event.stopPropagation(); onInspect(place(event, key)); },
    onKeyDown: (event: KeyboardEvent<SVGGElement>) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault(); event.stopPropagation(); onInspect(place(event, key));
    },
  };
}

type OverlayProps = {
  model: OverlayModel; layers: OverlayLayers; visibleColumnIds: readonly number[];
  y: (price: number) => number; width: number; height: number;
  selectedKey?: string; onInspect: (inspection: Inspection) => void;
};

// Rendered inside the existing chart <svg>. Renders nothing at all when there
// is no overlay evidence or its layers are OFF (chart regression invariant).
export function ChartOverlays({ model, layers, visibleColumnIds, y, width, height, selectedKey, onInspect }: OverlayProps) {
  const { trendlines, breaks, patterns } = visibleItems(model, layers);
  if (!visibleColumnIds.length || (!trendlines.length && !breaks.length && !patterns.length)) return null;
  const x = (column: number) => columnX(visibleColumnIds, column) ?? 0;
  // Same convention as the X/O glyphs: price p is drawn at the centre of the row above y(p).
  const cy = (price: number) => y(price) - 13;
  return <g data-chart-overlays="">
    <defs><clipPath id="pnf-overlay-clip"><rect x="75" y="13" width={width - 75} height={height + 26} /></clipPath></defs>
    <g clipPath="url(#pnf-overlay-clip)">
      {patterns.map((p) => <PatternMark key={p.key} item={p} x={x} cy={cy} selected={selectedKey === p.key} onInspect={onInspect} />)}
      {trendlines.map((t) => <TrendlineMark key={t.key} item={t} x={x} cy={cy} selected={selectedKey === t.key} onInspect={onInspect} />)}
      {breaks.map((b) => <BreakMark key={b.key} item={b} x={x} cy={cy} selected={selectedKey === b.key} onInspect={onInspect} />)}
    </g>
  </g>;
}

type MarkProps<T> = { item: T; x: (column: number) => number; cy: (price: number) => number; selected: boolean; onInspect: (inspection: Inspection) => void };

function TrendlineMark({ item, x, cy, selected, onInspect }: MarkProps<TrendlineOverlay>) {
  const { line, from, to } = item;
  const color = lineColor(line.kind);
  const coords = { x1: x(from.column), y1: cy(from.price), x2: x(to.column), y2: cy(to.price) };
  return <g data-overlay="trendline" data-key={item.key} data-kind={line.kind} data-state={line.state}
    aria-label={`${readable(line.kind)} trendline, ${line.state}`} {...activate(item.key, onInspect)} style={{ cursor: "pointer" }}>
    <title>{`${readable(line.kind)} trendline · ${line.state}`}</title>
    <line {...coords} stroke="transparent" strokeWidth="18" pointerEvents="stroke" />
    <line {...coords} stroke={color} strokeWidth={selected ? 3 : 2} strokeDasharray={line.state === "active" ? undefined : "6 4"} strokeLinecap="round" />
  </g>;
}

function BreakMark({ item, x, cy, selected, onInspect }: MarkProps<BreakOverlay>) {
  const cx = x(item.column), py = cy(item.price), r = selected ? 10 : 9;
  return <g data-overlay="trendline-break" data-key={item.key} aria-label={`Trendline break at column ${item.column}`}
    {...activate(item.key, onInspect)} style={{ cursor: "pointer" }}>
    <title>{`Trendline break · column ${item.column}`}</title>
    <rect x={cx - 20} y={py - 20} width="40" height="40" fill="transparent" />
    <path d={`M ${cx} ${py - r} L ${cx + r} ${py} L ${cx} ${py + r} L ${cx - r} ${py} Z`} fill="none" stroke={colors.break} strokeWidth="2" />
    <text x={cx + 13} y={py + 4} fontSize="11" fill={colors.break} stroke="white" strokeWidth="3" paintOrder="stroke">Trendline break</text>
  </g>;
}

function PatternMark({ item, x, cy, selected, onInspect }: MarkProps<PatternOverlay>) {
  const left = x(item.columns[0]) - 14, right = x(item.columns.at(-1)!) + 14;
  const top = cy(item.priceHigh) - 13, bottom = cy(item.priceLow) + 13;
  const p = item.pattern;
  return <g data-overlay="pattern" data-key={item.key} data-pattern-type={p.pattern_type}
    aria-label={`Pattern ${readable(p.pattern_type)}, ${p.direction}`} {...activate(item.key, onInspect)} style={{ cursor: "pointer" }}>
    <title>{`${readable(p.pattern_type)} · ${p.direction} · ${p.relation}`}</title>
    <rect x={left} y={top} width={Math.max(28, right - left)} height={Math.max(26, bottom - top)} rx="3"
      fill={colors.pattern} fillOpacity={selected ? 0.12 : 0.06} stroke={colors.pattern} strokeWidth={selected ? 2 : 1.25} strokeDasharray="4 3" />
    <text x={left} y={top - 5} fontSize="11" fill={colors.pattern} stroke="white" strokeWidth="3" paintOrder="stroke">{`${readable(p.pattern_type)} · ${p.direction}`}</text>
  </g>;
}

type Row = [string, string];
function details(item: TrendlineOverlay | BreakOverlay | PatternOverlay): { title: string; rows: Row[]; source: string } {
  if ("pattern" in item) {
    const p = item.pattern;
    return { title: `Pattern: ${readable(p.pattern_type)}`, rows: [
      ["Pattern type", p.pattern_type], ["Direction", p.direction], ["Relation", p.relation],
      ["Price range", `${p.price_low} – ${p.price_high}`], ["Columns", item.columns.join(", ")],
      ["Confirmed", p.confirmation_time ?? "—"], ["Evidence code", p.evidence_code], ["Algorithm", p.algorithm_version ?? "—"],
    ], source: "signals.decision.patterns[] · SignalEngine (interim pattern visualization source)" };
  }
  const l = item.line;
  if ("transitionId" in item) {
    return { title: "Trendline break", rows: [
      ["Line", readable(l.kind)], ["Line state", l.state], ["Break column", String(item.column)],
      ["Break transition price", String(item.price)], ["Evidence", l.evidence.join(" · ") || "—"], ["Config", l.config_version],
    ], source: "trendline.break_column / break_transition_id · TrendlineEngine (ADR-020)" };
  }
  return { title: `Trendline: ${readable(l.kind)}`, rows: [
    ["State", l.state],
    ["Anchor A", `column ${l.anchor_a.column_id} · ${l.anchor_a.price}`], ["Anchor B", `column ${l.anchor_b.column_id} · ${l.anchor_b.price}`],
    ["Projected price", item.projectedColumn === null ? `${l.projected_price_at_latest_column} (not drawn: no current projection at the latest column)` : `${l.projected_price_at_latest_column} at latest column ${item.projectedColumn}`],
    ["Touch columns", l.touch_columns.length ? l.touch_columns.join(", ") : "none"],
    ["Break column", l.break_column === null ? "none" : String(l.break_column)],
    ["Retest outcome", l.retest_outcome], ["Evidence", l.evidence.join(" · ") || "—"], ["Config", l.config_version],
  ], source: "trendline.active_* · TrendlineEngine (ADR-020)" };
}

export function OverlayPopup({ model, layers, inspection, onClose }: { model: OverlayModel; layers: OverlayLayers; inspection: Inspection | null; onClose: () => void }) {
  if (!inspection) return null;
  const item = findItem(model, layers, inspection.key);
  if (!item) return null;
  const { title, rows, source } = details(item);
  return <aside className={`overlay-popup${inspection.above ? " is-above" : ""}`} role="dialog" aria-label="Chart evidence"
    data-key={item.key} style={{ left: inspection.left, top: inspection.top }}
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }}>
    <div className="overlay-popup-head"><strong>{title}</strong><button aria-label="Close evidence" onClick={onClose}>×</button></div>
    <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p className="overlay-source">Source: {source}. Backend evidence only; not a trading recommendation.</p>
  </aside>;
}
