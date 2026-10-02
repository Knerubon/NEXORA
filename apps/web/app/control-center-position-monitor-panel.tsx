"use client";

import type { PositionMonitorItem } from "./control-center-types";

function armed(flag: boolean | null) {
  return flag == null ? "Unavailable" : flag ? "Armed" : "Off";
}

function PositionRow({ p }: { p: PositionMonitorItem }) {
  return (
    <article className="cc-position-row" data-cc-side={p.side}>
      <header>
        <strong>{p.symbol}</strong>
        <span className={`cc-side cc-side-${p.side.toLowerCase()}`}>{p.side}</span>
        <span className="cc-pl">{p.profitLoss ?? "Unavailable"}</span>
      </header>
      <dl className="cc-position-grid">
        <div><dt>Open</dt><dd>{p.openPrice ?? "Unavailable"}</dd></div>
        <div><dt>Current</dt><dd>{p.currentPrice ?? "Unavailable"}</dd></div>
        <div><dt>SL</dt><dd>{p.stopLoss ?? "Unavailable"}</dd></div>
        <div><dt>TP</dt><dd>{p.takeProfit ?? "Unavailable"}</dd></div>
        <div><dt>Break-even</dt><dd>{armed(p.breakEvenArmed)}</dd></div>
        <div><dt>Trailing</dt><dd>{armed(p.trailingArmed)}</dd></div>
      </dl>
      {p.partialCloseLevels.length > 0 && (
        <ul className="cc-partial-close">
          {p.partialCloseLevels.map((lvl, i) => (
            <li key={i} className={lvl.done ? "cc-partial-done" : ""}>
              {lvl.portion} @ {lvl.price} {lvl.done ? "· done" : ""}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

// Display-only. There is no Position Supervisor / Exit Engine in the codebase
// today (no SL/TP/breakeven/trailing/partial-close execution exists) — this
// panel renders whatever evidence a future engine reports; it never computes
// or manages position state itself.
export function PositionMonitorPanel({ positions }: { positions: PositionMonitorItem[] }) {
  return (
    <section className="cc-panel cc-position-monitor" aria-label="Position monitor">
      <header><h3>Position Monitor</h3></header>
      {positions.length === 0
        ? <p className="cc-panel-note">No open positions.</p>
        : positions.map((p) => <PositionRow key={p.id} p={p} />)}
    </section>
  );
}
