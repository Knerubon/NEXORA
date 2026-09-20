"use client";

import { useCallback, useEffect, useRef, type PointerEvent } from "react";
import { MatrixSummary, type PanelProps } from "./signal-intelligence";
import { clampPosition, defaultPosition, readPosition, savePosition, type Position } from "./matrix-position";

export function MatrixFloat({ onDetails, ...props }: PanelProps & { onDetails?: () => void }) {
  const panel = useRef<HTMLElement>(null);
  const position = useRef<Position | null>(null);
  const drag = useRef<{ id: number; x: number; y: number; start: Position } | null>(null);
  const bounds = useCallback(() => {
    const el = panel.current!, parent = el.parentElement!;
    return { width: parent.clientWidth, height: parent.clientHeight, panelWidth: el.offsetWidth, panelHeight: el.offsetHeight };
  }, []);
  const place = useCallback((p: Position | null) => {
    const el = panel.current;
    if (!el) return;
    const b = bounds();
    const next = p ? clampPosition(p, b) : defaultPosition(b);
    position.current = next;
    el.style.left = `${next.x}px`; el.style.top = `${next.y}px`;
  }, [bounds]);
  useEffect(() => {
    try { place(readPosition(window.localStorage)); } catch { place(null); }
    const observer = new ResizeObserver(() => {
      if (window.matchMedia("(min-width: 701px)").matches) place(position.current);
    });
    observer.observe(panel.current!.parentElement!);
    observer.observe(panel.current!);
    return () => observer.disconnect();
  }, [place]);
  function finish(event: PointerEvent<HTMLButtonElement>) {
    if (drag.current?.id !== event.pointerId) return;
    drag.current = null;
    try { savePosition(window.localStorage, position.current); } catch { /* Optional persistence. */ }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  return <aside ref={panel} className="matrix-float" aria-label="Floating Matrix">
    <div className="matrix-float-toolbar">
      <button className="matrix-drag" aria-label="Drag Matrix; arrow keys move position" onPointerDown={event => {
        if (event.button !== 0 || !event.isPrimary || !window.matchMedia("(min-width: 701px)").matches) return;
        place(position.current);
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, start: position.current! };
        event.currentTarget.setPointerCapture(event.pointerId);
      }} onPointerMove={event => {
        const d = drag.current;
        if (d?.id === event.pointerId) place({ x: d.start.x + event.clientX - d.x, y: d.start.y + event.clientY - d.y });
      }} onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={finish} onKeyDown={event => {
        const delta: Record<string, Position> = { ArrowLeft: { x: -20, y: 0 }, ArrowRight: { x: 20, y: 0 }, ArrowUp: { x: 0, y: -20 }, ArrowDown: { x: 0, y: 20 } };
        if (!delta[event.key] || !window.matchMedia("(min-width: 701px)").matches) return;
        event.preventDefault();
        const p = position.current ?? defaultPosition(bounds());
        place({ x: p.x + delta[event.key].x, y: p.y + delta[event.key].y });
        try { savePosition(window.localStorage, position.current); } catch { /* Optional persistence. */ }
      }}>⠿ Matrix</button>
      <button onClick={() => { drag.current = null; place(null); try { savePosition(window.localStorage, null); } catch { /* Optional persistence. */ } }}>Reset position</button>
    </div>
    <MatrixSummary {...props} />
    <a href="#signal-analysis" onClick={onDetails}>รายละเอียด · Full analysis</a>
  </aside>;
}
