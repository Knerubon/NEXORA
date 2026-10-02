"use client";

import type { RiskSettingsDisplay } from "./control-center-types";

function field(value: number | null, suffix = "") {
  return value == null ? "Unavailable" : `${value}${suffix}`;
}

// Display-only. packages/nexora/risk already implements sizing/exposure/drawdown/
// daily-loss guards and a kill-switch (paper-scoped) — this panel does not
// reimplement or recalculate any of it; inputs are disabled until a real
// settings contract + endpoint exist.
export function RiskSettingsPanel({ settings }: { settings: RiskSettingsDisplay }) {
  return (
    <section className="cc-panel cc-risk-settings" aria-label="Risk settings">
      <header><h3>Risk Settings</h3></header>
      <dl className="cc-risk-grid">
        <div><dt>Fixed Lot</dt><dd>{field(settings.fixedLot)}</dd></div>
        <div><dt>Max Exposure</dt><dd>{field(settings.maxExposure, "%")}</dd></div>
        <div><dt>Max Daily Loss</dt><dd>{field(settings.maxDailyLoss, " USD")}</dd></div>
        <div>
          <dt>Kill Switch</dt>
          <dd className={settings.killSwitchArmed ? "cc-kill-armed" : ""}>
            {settings.killSwitchArmed == null ? "Unavailable" : settings.killSwitchArmed ? "Armed" : "Disarmed"}
          </dd>
        </div>
      </dl>
      <p className="cc-panel-note">
        Read-only in this preview. Editing requires a frozen risk-settings contract and backend
        endpoint — not implemented here.
      </p>
    </section>
  );
}
