// Autonomous Trading Control Center — UI-only read-model type.
//
// NONE of these types are a backend contract. ControlCenterSnapshot is a UI
// read-model only; it grants no backend authority and nothing here may be
// treated as an API, schema_version or execution contract.
//
// ADR-033 (docs/decisions/ADR-033-autonomous-trading-contracts-v1.md), section 20,
// freezes a target MAPPING from each field below to a future backend contract —
// it does not wire any of them up, and it is PROPOSED (Rin review pending), not
// yet ratified. Field-level comments below cite that mapping for traceability.
// Before any real data flows into this tree, the receiving side must be built
// against ADR-033's actual frozen contracts (section 3 of AGENTS.md governs
// changing any of this once something downstream depends on it) — this file
// must not silently become the spec itself.
//
// Phase 1 (AGENTS.md §0, §9): research / observation / backtest / paper only.
// No live or demo broker order path exists anywhere in the codebase. AUTO is
// therefore modeled as PRESENT BUT LOCKED, not merely "defaulted off" — see
// TradingModePanel. This matches ADR-033 section 8 (AI/UI has no vote in
// execution authority) and section 21 (governance blockers unchanged).

export const TRADING_MODES = ["SHADOW", "ASSISTED", "AUTO"] as const;
export type TradingMode = (typeof TRADING_MODES)[number];

// Why AUTO can never be "on" yet: no order_send-capable code exists (see
// packages/nexora/paper vs. a real broker adapter) and AGENTS.md §9 bans
// live/demo execution outright for Phase 1. AUTO stays visually present
// (so the control surface can be designed ahead of time) but non-activatable.
// ADR-033 §20 mapping: future-fed by TradingModeGate / TradingConfig (§8).
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

// ADR-033 §20 mapping: future-fed by SystemHealthGate (§18) + EntryReadiness.blockers (existing).
export type SystemHealthSnapshot = {
  status: SystemHealthStatus;
  feed: "connected" | "interrupted" | "unavailable";
  researchMode: string | null;
  blockReasons: BlockReason[];
};

// ADR-033 §20 mapping: future-fed by RiskPolicy (ADR-015) + the kill-switch flag
// carried on SystemHealthGate.inputs (§18) — not a new risk-settings API.
export type RiskSettingsDisplay = {
  fixedLot: number | null;
  maxExposure: number | null;
  maxDailyLoss: number | null;
  killSwitchArmed: boolean | null; // packages/nexora/risk kill-switch exists (paper-scoped) — display only here
};

export type PositionSide = "BUY" | "SELL";

// ADR-033 §20 mapping: future-fed by TradeLifecycle instances in OPEN/MANAGING
// + PositionSupervisor state (§15). PositionSupervisor is explicitly NOT
// implemented by ADR-033 — this type stays a display shape with nothing behind
// it until that lands; it must not be treated as pre-approval of its fields.
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

// ADR-033 §20 mapping: future-fed by MarketRegimeEngine / RegimeSnapshot (existing, unchanged).
export type MarketRegimeDisplay = {
  label: string | null; // packages/nexora/market_regime exists — this mirrors its shape loosely, not verbatim
  reason: string | null;
  sidewaysDetected: boolean | null;
};

export type ScannerStatus = "ACTIVE" | "INACTIVE" | "NOT_IMPLEMENTED";

// ADR-033 §16/§20: News/Social backend is BLOCKED — no provider, calendar or event
// envelope exists. NOT_IMPLEMENTED is the honest status and must stay until §16 unblocks;
// do not invent an ACTIVE-capable backend shape ahead of that envelope being frozen.
export type NewsScannerDisplay = {
  status: ScannerStatus; // always NOT_IMPLEMENTED in this build — no backend exists
  tradeDuringNews: boolean; // UI-only toggle, inert; no engine reads it
  nextBlackoutWindow: string | null;
};

export type SocialScannerDisplay = {
  status: ScannerStatus; // always NOT_IMPLEMENTED in this build
  lastSignal: string | null;
};

// ADR-033 §20: alertChannels is not addressed by ADR-033 at all — notifications
// remain entirely missing per the TASK 0 audit. No contract to map to yet.
export type AlertChannelsDisplay = {
  soundEnabled: boolean; // UI-only, no audio wiring in this phase
  telegramConnected: boolean; // always false — no integration exists
};

// ADR-033 §20 mapping: future-fed by Kill Switch state within
// SystemHealthGate / NewTradeAuthority (§8, §18) — not a standalone stop API.
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
