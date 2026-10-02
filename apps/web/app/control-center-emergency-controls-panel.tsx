"use client";

import type { AlertChannelsDisplay, EmergencyStopDisplay } from "./control-center-types";

// Emergency Stop here is a UI affordance only. There is nothing for it to stop:
// no live/demo order path exists in this build (AGENTS.md §0/§9). Wiring it to
// an actual kill-switch (packages/nexora/risk has one, paper-scoped) is future
// work behind a frozen contract, not this component's job.
export function EmergencyControlsPanel({ emergencyStop, alerts, onTriggerStop }: {
  emergencyStop: EmergencyStopDisplay;
  alerts: AlertChannelsDisplay;
  onTriggerStop?: () => void;
}) {
  return (
    <section className="cc-panel cc-emergency" aria-label="Emergency controls">
      <header><h3>Emergency Stop</h3></header>
      <button
        type="button"
        className={`cc-emergency-stop ${emergencyStop.armed ? "cc-emergency-armed" : ""}`}
        aria-pressed={emergencyStop.armed}
        onClick={() => onTriggerStop?.()}
        title="Display-only in this build — no execution exists to stop"
      >
        {emergencyStop.armed ? "STOP ARMED" : "Emergency Stop"}
      </button>
      {emergencyStop.lastTriggeredAt && <small>Last triggered: {emergencyStop.lastTriggeredAt}</small>}
      <p className="cc-panel-note">No execution exists yet for this control to act on.</p>

      <div className="cc-alert-channels">
        <h4>Alerts</h4>
        <label className="cc-inert-toggle" title="Not wired to any audio system — display only">
          <input type="checkbox" checked={alerts.soundEnabled} disabled readOnly /> Sound
        </label>
        <span className="cc-telegram-status" data-cc-telegram={alerts.telegramConnected ? "connected" : "disconnected"}>
          Telegram: {alerts.telegramConnected ? "Connected" : "Not connected"}
        </span>
      </div>
    </section>
  );
}
