"use client";

import { TRADING_MODES, type TradingMode, type TradingModeState } from "./control-center-types";

const MODE_LABEL: Record<TradingMode, string> = {
  SHADOW: "Shadow",
  ASSISTED: "Assisted",
  AUTO: "Auto",
};

const MODE_DESCRIPTION: Record<TradingMode, string> = {
  SHADOW: "Observes and records signals only. No prompts, no orders.",
  ASSISTED: "Surfaces signals for a human to act on manually. No orders are sent by the system.",
  AUTO: "Would place and manage orders without confirmation. Not permitted in Phase 1 (research / observation / backtest / paper only) — locked, not just defaulted off.",
};

export function TradingModePanel({ state, onSelect }: {
  state: TradingModeState;
  onSelect?: (mode: TradingMode) => void;
}) {
  return (
    <section className="cc-panel cc-trading-mode" aria-label="Trading mode" data-cc-mode={state.mode}>
      <header>
        <h3>Trading Mode</h3>
        <span className={`cc-auto-chip ${state.autoTradeEngaged ? "cc-auto-on" : "cc-auto-off"}`}
          data-cc-auto-trade={state.autoTradeEngaged ? "on" : "off"}>
          AUTO TRADE: {state.autoTradeEngaged ? "ON" : "OFF"}
        </span>
      </header>
      <div className="cc-mode-options" role="radiogroup" aria-label="Select trading mode">
        {TRADING_MODES.map((mode) => {
          const locked = mode === "AUTO" && !state.autoAvailable;
          const active = state.mode === mode;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={active}
              aria-disabled={locked}
              disabled={locked}
              className={`cc-mode-option cc-mode-${mode.toLowerCase()} ${active ? "cc-mode-active" : ""} ${locked ? "cc-mode-locked" : ""}`}
              onClick={() => !locked && onSelect?.(mode)}
              title={MODE_DESCRIPTION[mode]}
            >
              <strong>{MODE_LABEL[mode]}</strong>
              {locked && <small className="cc-mode-lock-badge">Locked — Phase 1</small>}
            </button>
          );
        })}
      </div>
      <p className="cc-mode-note">{MODE_DESCRIPTION[state.mode]}</p>
      {!state.autoAvailable && (
        <p className="cc-mode-safety-note">
          AUTO is unavailable by policy: no live or demo broker order path exists in this build
          (AGENTS.md §0 / §9). This is not a toggle default — it cannot be enabled from the UI.
        </p>
      )}
    </section>
  );
}
