export type Position = { x: number; y: number };
export type Bounds = { width: number; height: number; panelWidth: number; panelHeight: number };
export const POSITION_KEY = "nexora.matrix-position.v1";
export function clampPosition(p: Position, b: Bounds): Position {
  return { x: Math.max(0, Math.min(Number.isFinite(p.x) ? p.x : 0, Math.max(0, b.width - b.panelWidth))),
    y: Math.max(0, Math.min(Number.isFinite(p.y) ? p.y : 0, Math.max(0, b.height - b.panelHeight))) };
}
export function defaultPosition(b: Bounds): Position {
  return clampPosition({ x: b.width - b.panelWidth - 18, y: 20 }, b);
}
export function readPosition(storage: Pick<Storage, "getItem">): Position | null {
  try {
    const p = JSON.parse(storage.getItem(POSITION_KEY) ?? "null");
    return p && Number.isFinite(p.x) && Number.isFinite(p.y) ? { x: p.x, y: p.y } : null;
  } catch { return null; }
}
export function savePosition(storage: Pick<Storage, "setItem" | "removeItem">, p: Position | null) {
  try { if (p) storage.setItem(POSITION_KEY, JSON.stringify(p)); else storage.removeItem(POSITION_KEY); } catch { /* Storage may be disabled. */ }
}
