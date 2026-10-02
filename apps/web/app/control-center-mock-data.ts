// Display-only fixtures for the Control Center preview. Not sourced from any
// backend — used so the component tree has something to render before a real
// AutonomousStatusSnapshot contract is frozen (see types.ts header).

import type { ControlCenterSnapshot } from "./control-center-types";

export const MOCK_SNAPSHOT_IDLE: ControlCenterSnapshot = {
  tradingMode: { mode: "SHADOW", autoAvailable: false, autoTradeEngaged: false },
  systemHealth: { status: "OK", feed: "connected", researchMode: "live_observation", blockReasons: [] },
  riskSettings: { fixedLot: 0.1, maxExposure: 3, maxDailyLoss: 150, killSwitchArmed: false },
  positions: [],
  marketRegime: { label: "Trending", reason: "Matrix resolutions aligned across FAST/MEDIUM", sidewaysDetected: false },
  newsScanner: { status: "NOT_IMPLEMENTED", tradeDuringNews: false, nextBlackoutWindow: null },
  socialScanner: { status: "NOT_IMPLEMENTED", lastSignal: null },
  alertChannels: { soundEnabled: false, telegramConnected: false },
  emergencyStop: { armed: false, lastTriggeredAt: null },
};

export const MOCK_SNAPSHOT_WITH_POSITION: ControlCenterSnapshot = {
  ...MOCK_SNAPSHOT_IDLE,
  tradingMode: { mode: "ASSISTED", autoAvailable: false, autoTradeEngaged: false },
  positions: [
    {
      id: "paper-0001",
      symbol: "EURUSD",
      side: "BUY",
      openPrice: "1.08421",
      currentPrice: "1.08560",
      profitLoss: "+13.9",
      stopLoss: "1.08200",
      takeProfit: "1.08900",
      breakEvenArmed: true,
      trailingArmed: false,
      partialCloseLevels: [{ price: "1.08700", portion: "50%", done: false }],
    },
  ],
  marketRegime: { label: "Sideways", reason: "Range-bound — Matrix resolutions conflicting", sidewaysDetected: true },
};

export const MOCK_SNAPSHOT_BLOCKED: ControlCenterSnapshot = {
  ...MOCK_SNAPSHOT_IDLE,
  systemHealth: {
    status: "DEGRADED",
    feed: "interrupted",
    researchMode: "live_observation",
    blockReasons: [
      { code: "RISK_DAILY_LOSS_LIMIT", message: "Daily loss limit reached", source: "risk" },
      { code: "FEED_INTERRUPTED", message: "MT5 quote feed interrupted", source: "connection" },
    ],
  },
  emergencyStop: { armed: true, lastTriggeredAt: "2026-10-02T06:12:00Z" },
};

export const MOCK_SNAPSHOT_UNAVAILABLE: Partial<ControlCenterSnapshot> = {};
