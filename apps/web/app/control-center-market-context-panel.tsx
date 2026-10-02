"use client";

import type { MarketRegimeDisplay } from "./control-center-types";

// Display-only mirror of packages/nexora/market_regime output shape; never
// recomputes regime/sideways state in the browser.
export function MarketContextPanel({ regime }: { regime: MarketRegimeDisplay }) {
  return (
    <section className="cc-panel cc-market-context" aria-label="Market regime">
      <header><h3>Market Regime</h3></header>
      <p className="cc-regime-label"><strong>{regime.label ?? "Unavailable"}</strong></p>
      {regime.reason && <small>{regime.reason}</small>}
      <p className="cc-sideways-flag" data-cc-sideways={regime.sidewaysDetected ?? "unavailable"}>
        Sideways Trading: <strong>{regime.sidewaysDetected == null ? "Unavailable" : regime.sidewaysDetected ? "Detected" : "Not detected"}</strong>
      </p>
    </section>
  );
}
