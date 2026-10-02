// Autonomous Trading Control Center — UI-only type model.
//
// NONE of these types are a backend contract. No API, schema_version or ADR
// defines "AutonomousStatusSnapshot" or any field below today (verified against
// docs/decisions and apps/api as of base SHA 44037e25b3217ea099a7e4a7e51fdf1b7fab634d).
// They exist so Control Center components have something concrete to render in
// this design/preview phase. Before any real data flows into this tree, these
// types must be reconciled against an actual frozen contract (AGENTS.md §3) —
// do not let this file silently become the spec.
//
// Phase 1 (AGENTS.md §0, §9): research / observation / backtest / paper only.
// No live or demo broker order path exists anywhere in the codebase. AUTO is
// therefore modeled as PRESENT BUT LOCKED, not merely "defaulted off" — see
// TradingModePanel.

export const TRADING_MODES = ["SHADOW", "ASSISTED", "AUTO"] as const;
export type TradingMode = (typeof TRADING_MODES)[number];

// Why AUTO can never be "on" yet: no order_send-capable code exists (see
// packages/nexora/paper vs. a real broker adapter) and AGENTS.md §9 bans
// live/demo execution outright for Phase 1. AUTO stays visually present
// (so the control surface can be designed ahead of time) but non-activatable.
export type TradingModeState = {
  mode: TradingMode;
  autoAvailable: false; // Phase 1 invariant — never true in this build
  autoTradeEngaged: false; // derived, always false while autoAvailable is false
};

export type SystemHealthStatus = "OK" | "DEGRADED" | "UNAVAILABLE";

export type BlockReason = {
  code: string;
  message: string;
  source: "risk" | "readiness" | "connection" | "data_quality" | "manual";
};

export type SystemHealthSnapshot = {
  status: SystemHealthStatus;
  feed: "connected" | "interrupted" | "unavailable";
  researchMode: string | null;
  blockReasons: BlockReason[];
};

export type RiskSettingsDisplay = {
  fixedLot: number | null;
  maxExposure: number | null;
  maxDailyLoss: number | null;
  killSwitchArmed: boolean | null; // packages/nexora/risk kill-switch exists (paper-scoped) — display only here
};

export type PositionSide = "BUY" | "SELL";

export type PositionMonitorItem = {
  id: string;
  symbol: string;
  side: PositionSide;
  openPrice: string | null;
  currentPrice: string | null;
  profitLoss: string | null;
  stopLoss: string | null;
  takeProfit: string | null;
  breakEvenArmed: boolean | null;
  trailingArmed: boolean | null;
  partialCloseLevels: { price: string; portion: string; done: boolean }[];
};

export type MarketRegimeDisplay = {
  label: string | null; // packages/nexora/market_regime exists — this mirrors its shape loosely, not verbatim
  reason: string | null;
  sidewaysDetected: boolean | null;
};

export type ScannerStatus = "ACTIVE" | "INACTIVE" | "NOT_IMPLEMENTED";

export type NewsScannerDisplay = {
  status: ScannerStatus; // always NOT_IMPLEMENTED in this build — no backend exists
  tradeDuringNews: boolean; // UI-only toggle, inert; no engine reads it
  nextBlackoutWindow: string | null;
};

export type SocialScannerDisplay = {
  status: ScannerStatus; // always NOT_IMPLEMENTED in this build
  lastSignal: string | null;
};

export type AlertChannelsDisplay = {
  soundEnabled: boolean; // UI-only, no audio wiring in this phase
  telegramConnected: boolean; // always false — no integration exists
};

export type EmergencyStopDisplay = {
  armed: boolean; // visual only; no execution exists to stop
  lastTriggeredAt: string | null;
};

// Root snapshot a future backend MAY serve. Every field is optional/nullable
// on purpose — components must render "Unavailable" rather than assume shape,
// matching the EntryReadiness / SignalIntelligence convention in this app.
export type ControlCenterSnapshot = {
  tradingMode: TradingModeState;
  systemHealth: SystemHealthSnapshot;
  riskSettings: RiskSettingsDisplay;
  positions: PositionMonitorItem[];
  marketRegime: MarketRegimeDisplay;
  newsScanner: NewsScannerDisplay;
  socialScanner: SocialScannerDisplay;
  alertChannels: AlertChannelsDisplay;
  emergencyStop: EmergencyStopDisplay;
};
