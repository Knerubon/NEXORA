"use client";

import type { SystemHealthSnapshot } from "./control-center-types";

export function SystemHealthPanel({ health }: { health: SystemHealthSnapshot }) {
  return (
    <section className="cc-panel cc-system-health" aria-label="System health" data-cc-health={health.status}>
      <header>
        <h3>System Health</h3>
        <span className={`cc-health-chip cc-health-${health.status.toLowerCase()}`}>{health.status}</span>
      </header>
      <p>Feed: <strong>{health.feed}</strong></p>
      <p>Research mode: <strong>{health.researchMode ?? "Unavailable"}</strong></p>
      <div className="cc-block-reasons">
        <h4>Block Reasons</h4>
        {health.blockReasons.length === 0
          ? <p className="cc-panel-note">None reported.</p>
          : <ul>{health.blockReasons.map((r) => (
              <li key={r.code} data-cc-block-source={r.source}>
                <strong>{r.source}</strong> · {r.message} <small>{r.code}</small>
              </li>
            ))}</ul>}
      </div>
    </section>
  );
}
