"use client";

// Manual Drawing V1 on the P&F chart. User annotations only - see
// manual-drawing-model.ts: stored in this browser, never sent to the backend,
// never consumed by Entry Readiness, signals or the Pattern Engine.

import { useCallback, useState, useSyncExternalStore, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import {
  EMPTY_DOC, addDrawing, clearDrawings, createDrawingStore, drawingId, removeDrawing, samePoint, setVisible, snapPoint, storageKey,
  type ChartGrid, type DrawingPoint, type DrawingTool, type ManualDrawing,
} from "./manual-drawing-model";
import { place, type Inspection } from "./chart-overlays";

const store = createDrawingStore(() => typeof window === "undefined" ? null : window.localStorage);
const color = "#0f766e";
const priceText = (price: number) => String(price);
const pointText = (p: DrawingPoint) => `column ${p.column} · ${priceText(p.price)}`;

export function useManualDrawings(symbol: string | null) {
  const key = symbol ? storageKey(symbol) : null;
  const subscribe = useCallback((listener: () => void) => {
    const off = store.subscribe(listener);
    const onStorage = (event: StorageEvent) => { if (key && event.key === key) store.reload(key); };
    window.addEventListener("storage", onStorage);
    return () => { off(); window.removeEventListener("storage", onStorage); };
  }, [key]);
  // Server render and the first client render both see EMPTY_DOC (no hydration mismatch).
  const doc = useSyncExternalStore(subscribe, () => key ? store.get(key) : EMPTY_DOC, () => EMPTY_DOC);
  const [tool, setTool] = useState<DrawingTool>("none");
  const [pending, setPending] = useState<DrawingPoint | null>(null);
  const [hover, setHover] = useState<DrawingPoint | null>(null);
  const [selection, setSelection] = useState<Inspection | null>(null);
  const [owner, setOwner] = useState(key);
  // A different symbol: drop any in-progress tool, anchor or selection.
  if (owner !== key) { setOwner(key); setTool("none"); setPending(null); setHover(null); setSelection(null); }
  const enabled = Boolean(key) && doc.visible;
  // Drawing removed (another tab, clear) or layer hidden: never show a stale popup.
  if (selection && (!doc.visible || !doc.drawings.some((d) => d.id === selection.key))) setSelection(null);
  if (!enabled && (tool !== "none" || pending)) { setTool("none"); setPending(null); setHover(null); }

  const update = (next: typeof doc) => { if (key) store.set(key, next); };
  const cancel = () => { setTool("none"); setPending(null); setHover(null); };
  const choose = (next: DrawingTool) => {
    setPending(null); setHover(null); setSelection(null);
    setTool((prior) => prior === next ? "none" : next);
  };
  function put(p: DrawingPoint) {
    const created_at = new Date().toISOString();
    if (tool === "horizontal") {
      update(addDrawing(doc, { id: drawingId(), kind: "horizontal", price: p.price, created_at }));
      cancel();
    } else if (tool === "trend") {
      if (!pending) { setPending(p); return; }
      if (samePoint(pending, p)) return;
      update(addDrawing(doc, { id: drawingId(), kind: "trend", a: pending, b: p, created_at }));
      cancel();
    }
  }
  return {
    key, doc, tool, pending, hover, selection, enabled, saved: key ? store.saved(key) : true,
    choose, cancel, place: put,
    hoverAt: (p: DrawingPoint | null) => setHover((prior) => prior && p && samePoint(prior, p) ? prior : p),
    select: setSelection,
    close: () => setSelection(null),
    toggleVisible: () => update(setVisible(doc, !doc.visible)),
    remove: (id: string) => { setSelection(null); update(removeDrawing(doc, id)); },
    clear: () => { setSelection(null); update(clearDrawings(doc)); },
  };
}

export type ManualDrawings = ReturnType<typeof useManualDrawings>;

export function DrawingControls({ drawings, canDraw }: { drawings: ManualDrawings; canDraw: boolean }) {
  const { doc, tool, pending, enabled, saved } = drawings;
  const count = doc.drawings.length;
  const hint = tool === "horizontal" ? "Click a box to place a horizontal line · Esc cancels"
    : tool === "trend" ? (pending ? "Click the second point · Esc cancels" : "Click the first point · Esc cancels") : null;
  return <div className="chart-drawing" role="group" aria-label="Manual drawing">
    <button aria-pressed={doc.visible} disabled={!drawings.key} onClick={drawings.toggleVisible}
      title={saved ? "Show or hide your drawings (saved in this browser only)" : "Browser storage unavailable: drawings are not saved"}>
      {`Drawings${count ? ` (${count})` : ""}${saved ? "" : " · not saved"}`}
    </button>
    <button aria-pressed={tool === "horizontal"} disabled={!enabled || !canDraw} onClick={() => drawings.choose("horizontal")}>H-line</button>
    <button aria-pressed={tool === "trend"} disabled={!enabled || !canDraw} onClick={() => drawings.choose("trend")}>Trend line</button>
    <button disabled={!enabled || !count} onClick={() => { if (window.confirm(`Delete all ${count} drawings for this symbol?`)) drawings.clear(); }}>Clear</button>
    {hint && <span className="drawing-hint" role="status">{hint}</span>}
  </div>;
}

type LayerProps = {
  drawings: ManualDrawings; grid: ChartGrid; y: (price: number) => number; width: number; height: number;
  onSelect?: () => void;
};

function svgPoint(event: MouseEvent<SVGElement> | PointerEvent<SVGElement>) {
  const svg = event.currentTarget.ownerSVGElement;
  const matrix = svg?.getScreenCTM();
  if (!svg || !matrix) return null;
  return new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
}

// Rendered inside the chart <svg>, after the evidence overlays. Renders nothing
// when there are no drawings and no active tool (chart regression invariant).
export function DrawingLayer({ drawings, grid, y, width, height, onSelect }: LayerProps) {
  const { doc, tool, pending, hover, selection } = drawings;
  if (!doc.visible || !grid.visibleColumnIds.length || (!doc.drawings.length && tool === "none")) return null;
  const first = grid.visibleColumnIds[0];
  const x = (column: number) => 90 + (column - first) * 30;
  const cy = (price: number) => y(price) - 13;
  const labelX = x(grid.visibleColumnIds.at(-1)!) + 40;
  const at = (event: MouseEvent<SVGElement> | PointerEvent<SVGElement>) => {
    const p = svgPoint(event);
    return p ? snapPoint(grid, p.x, p.y) : null;
  };
  const selected = (d: ManualDrawing) => selection?.key === d.id;
  const pick = (d: ManualDrawing) => ({
    role: "button", tabIndex: 0, style: { cursor: "pointer" },
    onClick: (event: MouseEvent<SVGGElement>) => { event.stopPropagation(); onSelect?.(); drawings.select(place(event, d.id)); },
    onKeyDown: (event: KeyboardEvent<SVGGElement>) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault(); event.stopPropagation(); onSelect?.(); drawings.select(place(event, d.id));
    },
  });
  return <g data-manual-drawings="">
    <defs><clipPath id="pnf-drawing-clip"><rect x="75" y="13" width={width - 75} height={height + 26} /></clipPath></defs>
    <g clipPath="url(#pnf-drawing-clip)">
      {doc.drawings.map((d) => d.kind === "horizontal"
        ? <g key={d.id} data-drawing="horizontal" data-id={d.id} aria-label={`Manual horizontal line at ${priceText(d.price)}`} {...pick(d)}>
            <title>{`Manual line · ${priceText(d.price)}`}</title>
            <line x1="75" x2={width} y1={cy(d.price)} y2={cy(d.price)} stroke="transparent" strokeWidth="14" pointerEvents="stroke" />
            <line x1="75" x2={width} y1={cy(d.price)} y2={cy(d.price)} stroke={color} strokeWidth={selected(d) ? 3 : 1.75} />
            <text x={labelX} y={cy(d.price) - 5} fontSize="11" fill={color} stroke="white" strokeWidth="3" paintOrder="stroke">{`Manual · ${priceText(d.price)}`}</text>
          </g>
        : <g key={d.id} data-drawing="trend" data-id={d.id} aria-label={`Manual trend line from ${pointText(d.a)} to ${pointText(d.b)}`} {...pick(d)}>
            <title>{`Manual trend line · ${pointText(d.a)} → ${pointText(d.b)}`}</title>
            <line x1={x(d.a.column)} y1={cy(d.a.price)} x2={x(d.b.column)} y2={cy(d.b.price)} stroke="transparent" strokeWidth="14" pointerEvents="stroke" />
            <line x1={x(d.a.column)} y1={cy(d.a.price)} x2={x(d.b.column)} y2={cy(d.b.price)} stroke={color} strokeWidth={selected(d) ? 3 : 1.75} strokeLinecap="round" />
            {[d.a, d.b].map((p, i) => <circle key={i} cx={x(p.column)} cy={cy(p.price)} r={selected(d) ? 4 : 3} fill="white" stroke={color} strokeWidth="1.5" />)}
          </g>)}
      {pending && <g data-drawing-preview="" pointerEvents="none">
        <circle cx={x(pending.column)} cy={cy(pending.price)} r="4" fill={color} />
        {hover && !samePoint(pending, hover) && <line x1={x(pending.column)} y1={cy(pending.price)} x2={x(hover.column)} y2={cy(hover.price)} stroke={color} strokeWidth="1.5" strokeDasharray="5 4" />}
      </g>}
      {tool === "horizontal" && hover && <line data-drawing-preview="" pointerEvents="none" x1="75" x2={width} y1={cy(hover.price)} y2={cy(hover.price)} stroke={color} strokeWidth="1.5" strokeDasharray="5 4" />}
    </g>
    {tool !== "none" && <rect data-drawing-capture="" x="75" y="13" width={width - 75} height={height + 26} fill="transparent" style={{ cursor: "crosshair" }}
      onPointerMove={(event) => drawings.hoverAt(at(event))} onPointerLeave={() => drawings.hoverAt(null)}
      onClick={(event) => { event.stopPropagation(); const p = at(event); if (p) drawings.place(p); }} />}
  </g>;
}

export function DrawingPopup({ drawings }: { drawings: ManualDrawings }) {
  const { selection, doc } = drawings;
  const item = selection && doc.drawings.find((d) => d.id === selection.key);
  if (!selection || !item) return null;
  const rows: [string, string][] = item.kind === "horizontal"
    ? [["Type", "Horizontal line"], ["Price", priceText(item.price)], ["Created", item.created_at]]
    : [["Type", "Trend line"], ["Point A", pointText(item.a)], ["Point B", pointText(item.b)], ["Created", item.created_at]];
  return <aside className={`overlay-popup${selection.above ? " is-above" : ""}`} role="dialog" aria-label="Manual drawing"
    data-drawing-id={item.id} style={{ left: selection.left, top: selection.top }}
    onKeyDown={(event) => {
      if (event.key === "Escape") { event.stopPropagation(); drawings.close(); }
      if (event.key === "Delete") { event.stopPropagation(); drawings.remove(item.id); }
    }}>
    <div className="overlay-popup-head"><strong>Manual drawing</strong><button aria-label="Close drawing" onClick={drawings.close}>×</button></div>
    <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <button className="drawing-delete" onClick={() => drawings.remove(item.id)}>Delete drawing</button>
    <p className="overlay-source">Your annotation, saved in this browser only. Not system evidence; never used by Entry Readiness, signals or backtests.</p>
  </aside>;
}
