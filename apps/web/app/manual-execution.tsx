"use client";

import { useState } from "react";

// MANUAL-EXEC-1: Manual Execution Test Panel V1 — UI + CONTRACT/SCAFFOLD ONLY.
//
// This component never calls a broker or MT5 execution API, never imports a network client for
// order submission, and has no code path that can transmit an executable order in V1. It only
// builds a local, clearly-labelled "proposed" request object for display/testing.
//
// ADR-033 (autonomous-trading-contracts-v1, status: PROPOSED — not yet Rin-approved) freezes
// `EntryOrigin` as requiring `signal_decision_ref` + `entry_readiness_ref`
// (packages/nexora/autonomous_contracts.py). A manual, operator-initiated order has neither, so a
// manual request can NOT be represented as a conforming ADR-033 `TradeIntent` today. Rather than
// misuse `EntryOrigin`/`PositionOrigin` or silently extend the frozen contract, this module defines
// its own, explicitly-not-TradeIntent local shape (`ManualTradeRequest`) and documents the proposed
// future `ManualTradeOrigin` contract as a dependency for Rin/Architect review — see
// tasks/MANUAL-EXEC-1-manual-execution-ui.md section "Proposed future contract".

export type OrderSide = "BUY" | "SELL";

export type ManualTradeForm = {
  symbol: string;
  side: OrderSide | "";
  quantity: string; // raw input text; parsed by validate/build
  stopLoss: string;
  takeProfit: string;
};

export const EMPTY_MANUAL_TRADE_FORM: ManualTradeForm = {
  symbol: "", side: "", quantity: "", stopLoss: "", takeProfit: "",
};

export type ManualTradeValidation = { valid: true } | { valid: false; errors: string[] };

// Pure validation: symbol present, side valid, quantity > 0. SL/TP are optional but must be
// numeric when provided. Does not know about Risk policy, lot sizing or broker constraints —
// those belong to the future Risk Guard / BrokerCapabilities layers, not this scaffold.
export function validateManualTradeRequest(form: ManualTradeForm): ManualTradeValidation {
  const errors: string[] = [];
  if (!form.symbol.trim()) errors.push("symbol is required");
  if (form.side !== "BUY" && form.side !== "SELL") errors.push("side must be BUY or SELL");
  const quantity = Number(form.quantity);
  if (!form.quantity.trim() || !Number.isFinite(quantity) || quantity <= 0) errors.push("quantity must be greater than 0");
  if (form.stopLoss.trim() && !Number.isFinite(Number(form.stopLoss))) errors.push("stop loss must be numeric");
  if (form.takeProfit.trim() && !Number.isFinite(Number(form.takeProfit))) errors.push("take profit must be numeric");
  return errors.length ? { valid: false, errors } : { valid: true };
}

// Explicitly NOT an ADR-033 TradeIntent (see module header). `kind` is tagged so nothing can
// mistake this for the frozen contract shape by accident.
export type ManualTradeRequest = {
  kind: "MANUAL_TRADE_REQUEST_PROPOSAL";
  side: OrderSide;
  symbol: string;
  quantity: number;
  stop_loss: number | null;
  take_profit: number | null;
};

// Returns null when the form is invalid — an invalid form can never produce a proposed request.
export function buildManualTradeRequest(form: ManualTradeForm): ManualTradeRequest | null {
  const result = validateManualTradeRequest(form);
  if (!result.valid) return null;
  return {
    kind: "MANUAL_TRADE_REQUEST_PROPOSAL",
    side: form.side as OrderSide,
    symbol: form.symbol.trim(),
    quantity: Number(form.quantity),
    stop_loss: form.stopLoss.trim() ? Number(form.stopLoss) : null,
    take_profit: form.takeProfit.trim() ? Number(form.takeProfit) : null,
  };
}

// CLOSE ALL means: close every position NEXORA owns and is authorized to manage — never every
// MT5 account position. Ownership resolution does not exist yet (Position Supervisor is
// unimplemented per ADR-033 section 15); V1 can only enumerate the symbol currently in view.
export type CloseAllPhase = "idle" | "confirming" | "confirmed";
export type CloseAllAction = "request" | "confirm" | "cancel";

export function closeAllReducer(phase: CloseAllPhase, action: CloseAllAction): CloseAllPhase {
  if (action === "cancel") return "idle";
  if (phase === "idle" && action === "request") return "confirming";
  if (phase === "confirming" && action === "confirm") return "confirmed";
  return phase;
}

export type CloseAllRequest = { kind: "MANUAL_CLOSE_ALL_REQUEST_PROPOSAL"; scope: string };

// Only a confirmed phase can produce a request object — mirrors the BUY/SELL invalid-input rule:
// no path exists to a proposed request without passing through the confirmation step.
export function buildCloseAllRequest(phase: CloseAllPhase, scope: string): CloseAllRequest | null {
  if (phase !== "confirmed") return null;
  return { kind: "MANUAL_CLOSE_ALL_REQUEST_PROPOSAL", scope };
}

export type ManualExecutionProps = {
  symbol?: string | null;
  currentPrice?: string | number | null;
  // ADR-033 TradingModeGate (section 8) is not wired to any backend yet. Left undefined rather
  // than defaulted to a real-looking value — an invented mode would be a speculative default
  // (AGENTS.md section 17), not an honest "not available" state.
  tradingMode?: string | null;
  // ADR-033 SystemHealthGate (section 18) defaults every axis to UNKNOWN (fail-closed) until a
  // real health source is wired. This panel follows the same fail-closed default rather than
  // inferring health from unrelated feed-connection state.
  systemHealth?: string | null;
  brokerHealth?: string | null;
};

const display = (value: string | number | null | undefined, fallback = "UNKNOWN") =>
  value === null || value === undefined || value === "" ? fallback : String(value);

export function ManualExecutionPanel({ symbol, currentPrice, tradingMode, systemHealth, brokerHealth }: ManualExecutionProps) {
  const [form, setForm] = useState<ManualTradeForm>({ ...EMPTY_MANUAL_TRADE_FORM, symbol: symbol ?? "" });
  const [closeAllPhase, setCloseAllPhase] = useState<CloseAllPhase>("idle");

  // Execution is unconditionally locked in V1: no prop, state, or interaction in this component
  // can set this to false. Enabling real execution requires a future version, not a toggle here.
  const EXECUTION_LOCKED = true;

  const field = (key: keyof ManualTradeForm) => (event: { target: { value: string } }) =>
    setForm((prior) => ({ ...prior, [key]: event.target.value }));

  const validation = validateManualTradeRequest(form);
  const proposed = buildManualTradeRequest(form);
  const closeAllRequest = buildCloseAllRequest(closeAllPhase, form.symbol.trim() || "current view");

  return <section className="manual-execution" aria-label="Manual Order Test" data-manual-execution-state={EXECUTION_LOCKED ? "LOCKED" : "UNLOCKED"}>
    <h2>Manual Order Test</h2>
    <p className="manual-execution-lock" role="status">
      <strong>EXECUTION LOCKED</strong> — this is a test harness scaffold; no order is ever sent to a broker or MT5 in this version.
    </p>

    <dl className="manual-execution-status">
      <div><dt>Symbol</dt><dd>{display(symbol)}</dd></div>
      <div><dt>Current Price</dt><dd>{display(currentPrice)}</dd></div>
      <div><dt>Trading Mode</dt><dd>{display(tradingMode)}</dd></div>
      <div><dt>System Health</dt><dd>{display(systemHealth)}</dd></div>
      <div><dt>Broker/Feed Health</dt><dd>{display(brokerHealth)}</dd></div>
      <div><dt>AUTO</dt><dd data-auto-state="UNAVAILABLE">UNAVAILABLE — Phase 1 research/paper only (AGENTS.md section 0)</dd></div>
    </dl>

    <form className="manual-execution-form" onSubmit={(event) => event.preventDefault()}>
      <label>Symbol <input value={form.symbol} onChange={field("symbol")} aria-label="Order symbol" /></label>
      <label>Order Side
        <select value={form.side} onChange={field("side")} aria-label="Order side">
          <option value="">Choose side</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
      </label>
      <label>Lot / Quantity <input value={form.quantity} onChange={field("quantity")} aria-label="Order quantity" inputMode="decimal" /></label>
      <label>SL <input value={form.stopLoss} onChange={field("stopLoss")} aria-label="Stop loss" inputMode="decimal" /></label>
      <label>TP <input value={form.takeProfit} onChange={field("takeProfit")} aria-label="Take profit" inputMode="decimal" /></label>
      {!validation.valid && <ul className="manual-execution-errors">{validation.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
    </form>

    <div className="manual-execution-controls" role="group" aria-label="Manual execution controls">
      <button type="button" disabled={EXECUTION_LOCKED} aria-label="Submit manual BUY order" title="Locked: no execution pipeline exists yet (see tasks/MANUAL-EXEC-1-manual-execution-ui.md)">BUY</button>
      <button type="button" disabled={EXECUTION_LOCKED} aria-label="Submit manual SELL order" title="Locked: no execution pipeline exists yet (see tasks/MANUAL-EXEC-1-manual-execution-ui.md)">SELL</button>
      <button type="button" disabled={EXECUTION_LOCKED} aria-label="Close all NEXORA-owned positions" title="Locked: ownership resolution (Position Supervisor) is not implemented yet">CLOSE ALL</button>
    </div>

    <div className="manual-execution-closeall" aria-label="Close All preview (local, never transmitted)">
      {closeAllPhase === "idle" && <button type="button" onClick={() => setCloseAllPhase(closeAllReducer(closeAllPhase, "request"))}>Preview Close All</button>}
      {closeAllPhase === "confirming" && <>
        <p role="alert">Confirm: preview a CLOSE ALL request for positions NEXORA owns and manages under &quot;{form.symbol.trim() || "current view"}&quot;? This will not be sent to a broker.</p>
        <button type="button" onClick={() => setCloseAllPhase(closeAllReducer(closeAllPhase, "confirm"))}>Confirm preview</button>
        <button type="button" onClick={() => setCloseAllPhase(closeAllReducer(closeAllPhase, "cancel"))}>Cancel</button>
      </>}
      {closeAllPhase === "confirmed" && closeAllRequest && <>
        <p data-trade-request-state="simulated">SIMULATED — NOT SENT TO BROKER: proposed close-all scope &quot;{closeAllRequest.scope}&quot;.</p>
        <button type="button" onClick={() => setCloseAllPhase(closeAllReducer(closeAllPhase, "cancel"))}>Reset preview</button>
      </>}
    </div>

    <div className="manual-execution-preview" aria-label="Proposed manual trade request (local preview)">
      {proposed
        ? <p data-trade-request-state="simulated">SIMULATED — NOT SENT TO BROKER: proposed {proposed.side} {proposed.quantity} {proposed.symbol}
            {proposed.stop_loss !== null ? ` · SL ${proposed.stop_loss}` : ""}
            {proposed.take_profit !== null ? ` · TP ${proposed.take_profit}` : ""}</p>
        : <p data-trade-request-state="unavailable">No proposed request — fill in a valid symbol, side and quantity above.</p>}
      <p className="manual-execution-status-line" data-execution-transmitted="false">Execution status: NO BROKER ORDER SENT.</p>
    </div>

    <p className="manual-execution-note">
      Conceptual future flow: Web → Manual Trade Request → TradeIntent → Risk / Authority → Execution Guard →
      Broker Adapter → Position/Reconciliation. The same pipeline will serve AUTO; only the command origin differs.
      See ADR-033 and tasks/MANUAL-EXEC-1-manual-execution-ui.md.
    </p>
  </section>;
}
