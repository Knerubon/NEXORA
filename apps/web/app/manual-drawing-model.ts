// Manual Drawing V1: user annotations on the P&F chart.
//
// Presentation only. A manual drawing is what the viewer drew, never system
// evidence: it is stored only in this browser (localStorage, per symbol), is
// never sent to the backend, and is never read by Entry Readiness, signals,
// backtests or the Pattern Engine. Nothing here detects or scores anything.
//
// Drawings are anchored in data space (P&F column_id, price) - not pixels - so
// they stay attached to the same boxes across zoom, scroll and new columns.

export type DrawingPoint = { column: number; price: number };
export type HorizontalDrawing = { id: string; kind: "horizontal"; price: number; created_at: string };
export type TrendDrawing = { id: string; kind: "trend"; a: DrawingPoint; b: DrawingPoint; created_at: string };
export type ManualDrawing = HorizontalDrawing | TrendDrawing;
export type DrawingTool = "none" | "horizontal" | "trend";
export type DrawingDoc = { version: 1; visible: boolean; drawings: readonly ManualDrawing[] };

export const MAX_DRAWINGS = 50;
export const STORAGE_PREFIX = "nexora:manual-drawings:v1:";
export const EMPTY_DOC: DrawingDoc = Object.freeze({ version: 1, visible: true, drawings: Object.freeze([]) as readonly ManualDrawing[] });

export const storageKey = (symbol: string) => STORAGE_PREFIX + symbol;

const isColumn = (value: unknown): value is number => Number.isSafeInteger(value);
const isPrice = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isText = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 64;

function point(value: unknown): DrawingPoint | null {
  const p = value as Partial<DrawingPoint> | null;
  return p && isColumn(p.column) && isPrice(p.price) ? { column: p.column, price: p.price } : null;
}

function drawing(value: unknown): ManualDrawing | null {
  const d = value as Record<string, unknown> | null;
  if (!d || !isText(d.id) || !isText(d.created_at)) return null;
  if (d.kind === "horizontal" && isPrice(d.price)) return { id: d.id, kind: "horizontal", price: d.price, created_at: d.created_at };
  if (d.kind === "trend") {
    const a = point(d.a), b = point(d.b);
    if (a && b && !samePoint(a, b)) return { id: d.id, kind: "trend", a, b, created_at: d.created_at };
  }
  return null;
}

// Stored data is untrusted: bad JSON or an unknown version yields an empty
// document; individual invalid or duplicate drawings are dropped.
export function parseDrawings(raw: string | null): DrawingDoc {
  if (!raw) return EMPTY_DOC;
  let data: Record<string, unknown>;
  try { data = JSON.parse(raw); } catch { return EMPTY_DOC; }
  if (!data || data.version !== 1 || !Array.isArray(data.drawings)) return EMPTY_DOC;
  const ids = new Set<string>();
  const drawings: ManualDrawing[] = [];
  for (const item of data.drawings) {
    const d = drawing(item);
    if (!d || ids.has(d.id) || drawings.length >= MAX_DRAWINGS) continue;
    ids.add(d.id);
    drawings.push(d);
  }
  return { version: 1, visible: data.visible !== false, drawings };
}

export const serializeDrawings = (doc: DrawingDoc) => JSON.stringify(doc);

export function addDrawing(doc: DrawingDoc, item: ManualDrawing): DrawingDoc {
  if (doc.drawings.length >= MAX_DRAWINGS || doc.drawings.some((d) => d.id === item.id)) return doc;
  return { ...doc, drawings: [...doc.drawings, item] };
}
export const removeDrawing = (doc: DrawingDoc, id: string): DrawingDoc => ({ ...doc, drawings: doc.drawings.filter((d) => d.id !== id) });
export const clearDrawings = (doc: DrawingDoc): DrawingDoc => ({ ...doc, drawings: [] });
export const setVisible = (doc: DrawingDoc, visible: boolean): DrawingDoc => ({ ...doc, visible });

export const samePoint = (a: DrawingPoint, b: DrawingPoint) => a.column === b.column && a.price === b.price;

// Chart geometry, mirroring StructureChart in page.tsx: grid row height 26,
// column pitch 30, first column centre x=90, price axis left of x=75, and
// y(p) = 26 + (top - p) / rowStep * 26. Like the X/O glyphs and the overlays,
// price p is drawn at the centre of the row above y(p): cy(p) = y(p) - 13.
export type ChartGrid = { top: number; rowStep: number; visibleColumnIds: readonly number[] };

// Snap an SVG point to the P&F cell under it. Columns are snapped to the
// nearest column slot - including empty slots right of the latest column,
// because P&F column_id increases by exactly 1 per column (pnf/engine.py).
export function snapPoint(grid: ChartGrid, svgX: number, svgY: number): DrawingPoint | null {
  const first = grid.visibleColumnIds[0];
  if (!isColumn(first) || !isPrice(grid.top) || !(grid.rowStep > 0) || !isPrice(svgX) || !isPrice(svgY) || svgX < 75) return null;
  const column = first + Math.max(0, Math.round((svgX - 90) / 30));
  const row = Math.ceil((svgY - 26) / 26 - 1e-9);
  // Round away binary floating-point noise (e.g. 0.1 box sizes).
  const price = Number((grid.top - row * grid.rowStep).toPrecision(12));
  return isColumn(column) && isPrice(price) ? { column, price } : null;
}

let sequence = 0;
export function drawingId(now: number = Date.now()): string {
  const random = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function" ? crypto.randomUUID() : Math.random().toString(36).slice(2);
  return `d-${now.toString(36)}-${(sequence++).toString(36)}-${random.slice(0, 8)}`;
}

// Per-symbol store behind useSyncExternalStore. Memory is primary; localStorage
// is best-effort: if it is unavailable or throws, drawings still work for this
// page session and `saved(key)` reports false.
export type DrawingStorage = { getItem(key: string): string | null; setItem(key: string, value: string): void };

export function createDrawingStore(getStorage: () => DrawingStorage | null) {
  const docs = new Map<string, DrawingDoc>();
  const unsaved = new Set<string>();
  const listeners = new Set<() => void>();
  const storage = () => { try { return getStorage(); } catch { return null; } };
  const notify = () => listeners.forEach((listener) => listener());
  function get(key: string): DrawingDoc {
    let doc = docs.get(key);
    if (!doc) {
      let raw: string | null = null;
      try { raw = storage()?.getItem(key) ?? null; } catch { raw = null; }
      doc = parseDrawings(raw);
      docs.set(key, doc);
    }
    return doc;
  }
  function set(key: string, doc: DrawingDoc) {
    docs.set(key, doc);
    try {
      const target = storage();
      if (!target) throw new Error("storage unavailable");
      target.setItem(key, serializeDrawings(doc));
      unsaved.delete(key);
    } catch { unsaved.add(key); }
    notify();
  }
  // Another tab changed this key: drop the cached copy and re-read on demand.
  function reload(key: string) { docs.delete(key); unsaved.delete(key); notify(); }
  function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
  return { get, set, reload, subscribe, saved: (key: string) => !unsaved.has(key) };
}
