"use client";

import { useState } from "react";
import type { ControlCenterSnapshot, TradingMode } from "./control-center-types";
import { TradingModePanel } from "./control-center-trading-mode-panel";
import { SystemHealthPanel } from "./control-center-system-health-panel";
import { RiskSettingsPanel } from "./control-center-risk-settings-panel";
import { PositionMonitorPanel } from "./control-center-position-monitor-panel";
import { MarketContextPanel } from "./control-center-market-context-panel";
import { ScannersPanel } from "./control-center-scanners-panel";
import { EmergencyControlsPanel } from "./control-center-emergency-controls-panel";

// Composition root for the Autonomous Trading Control Center.
//
// This is a standalone, isolated tree: it is not imported from app/page.tsx
// and does not read any live backend state. `snapshot` is caller-supplied
// (mock data in this phase — see mock-data.ts); mode selection is local UI
// state only and never reaches any execution path, because none exists.
export function ControlCenter({ snapshot }: { snapshot: ControlCenterSnapshot }) {
  const [mode, setMode] = useState<TradingMode>(snapshot.tradingMode.mode);

  const tradingMode = { ...snapshot.tradingMode, mode, autoTradeEngaged: false as const };

  return (
    <div className="cc-root" data-cc-root="">
      <header className="cc-header">
        <span className="panel-eyebrow">AUTONOMOUS TRADING</span>
        <h2>Control Center</h2>
        <p className="cc-phase-banner">
          Phase 1 — research / observation / backtest / paper only. No live or demo orders.
        </p>
      </header>
      <div className="cc-grid">
        <TradingModePanel state={tradingMode} onSelect={setMode} />
        <SystemHealthPanel health={snapshot.systemHealth} />
        <RiskSettingsPanel settings={snapshot.riskSettings} />
        <PositionMonitorPanel positions={snapshot.positions} />
        <MarketContextPanel regime={snapshot.marketRegime} />
        <ScannersPanel news={snapshot.newsScanner} social={snapshot.socialScanner} />
        <EmergencyControlsPanel emergencyStop={snapshot.emergencyStop} alerts={snapshot.alertChannels} />
      </div>
    </div>
  );
}
