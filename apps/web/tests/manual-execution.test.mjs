import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { manualExecution as me, manualExecutionSource } from './manual-execution-harness.mjs';

// MANUAL-EXEC-1: Manual Execution Test Panel V1 — UI + contract/scaffold only, no broker calls.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const html = (props = {}) => renderToStaticMarkup(createElement(me.ManualExecutionPanel, props));
const validForm = { symbol: 'XAUUSD', side: 'BUY', quantity: '0.10', stopLoss: '', takeProfit: '' };

test('1. BUY exists', () => {
  const markup = html();
  assert.match(markup, /aria-label="Submit manual BUY order"[^>]*>BUY<\/button>/);
});

test('2. SELL exists', () => {
  const markup = html();
  assert.match(markup, /aria-label="Submit manual SELL order"[^>]*>SELL<\/button>/);
});

test('3. CLOSE ALL exists', () => {
  const markup = html();
  assert.match(markup, /aria-label="Close all NEXORA-owned positions"[^>]*>CLOSE ALL<\/button>/);
});

test('4. execution controls are disabled/locked by default', () => {
  const markup = html();
  assert.equal(markup.match(/<button type="button" disabled=""/g)?.length, 3, 'BUY, SELL and CLOSE ALL are each disabled');
  assert.match(markup, /data-manual-execution-state="LOCKED"/);
  assert.match(markup, /EXECUTION LOCKED/);
});

test('5. CLOSE ALL requires confirmation before a proposed request can exist', () => {
  assert.equal(me.closeAllReducer('idle', 'request'), 'confirming');
  assert.equal(me.buildCloseAllRequest('idle', 'XAUUSD'), null, 'idle produces no request');
  assert.equal(me.buildCloseAllRequest('confirming', 'XAUUSD'), null, 'confirming alone produces no request');
  const confirmed = me.closeAllReducer('confirming', 'confirm');
  assert.equal(confirmed, 'confirmed');
  const request = me.buildCloseAllRequest(confirmed, 'XAUUSD');
  assert.deepEqual(request, { kind: 'MANUAL_CLOSE_ALL_REQUEST_PROPOSAL', scope: 'XAUUSD' });
  assert.equal(me.closeAllReducer('confirming', 'cancel'), 'idle');
  assert.equal(me.closeAllReducer('confirmed', 'cancel'), 'idle');
});

test('6. invalid quantity cannot produce a proposed BUY/SELL request', () => {
  for (const quantity of ['0', '-1', '', 'abc', 'NaN']) {
    const result = me.buildManualTradeRequest({ ...validForm, quantity });
    assert.equal(result, null, `quantity ${JSON.stringify(quantity)} must not produce a request`);
  }
  assert.ok(me.buildManualTradeRequest(validForm), 'a valid form does produce a request');
  assert.equal(me.buildManualTradeRequest({ ...validForm, symbol: '' }), null, 'missing symbol blocks the request');
  assert.equal(me.buildManualTradeRequest({ ...validForm, side: '' }), null, 'missing side blocks the request');
  assert.equal(me.buildManualTradeRequest({ ...validForm, stopLoss: 'abc' }), null, 'non-numeric SL blocks the request');
});

test('7. UI cannot directly invoke broker/MT5 execution', () => {
  assert.doesNotMatch(manualExecutionSource, /fetch\(|XMLHttpRequest|WebSocket|order_send|MetaTrader|axios/);
  const page = read('../app/page.tsx');
  const panelBlock = page.slice(page.indexOf('<ManualExecutionPanel'), page.indexOf('/>', page.indexOf('<ManualExecutionPanel')) + 2);
  assert.doesNotMatch(panelBlock, /fetch\(|act\(/);
});

test('8. AUTO remains unavailable', () => {
  const markup = html();
  assert.match(markup, /data-auto-state="UNAVAILABLE"/);
  assert.match(markup, /AUTO<\/dt><dd data-auto-state="UNAVAILABLE">UNAVAILABLE/);
  assert.doesNotMatch(manualExecutionSource, /TradingMode\.AUTO\s*=|setAuto|enableAuto|auto.*=.*true/i);
});

test('9. panel distinguishes simulated/proposed state from executed broker state', () => {
  const markup = html();
  assert.match(markup, /Execution status: NO BROKER ORDER SENT\./);
  assert.match(markup, /data-execution-transmitted="false"/);
  assert.doesNotMatch(markup, /data-execution-transmitted="true"/);
  assert.match(html({}), /No proposed request/);
});

test('form fields build a locally-previewable proposed request without transmitting it', () => {
  const request = me.buildManualTradeRequest(validForm);
  assert.deepEqual(request, {
    kind: 'MANUAL_TRADE_REQUEST_PROPOSAL', side: 'BUY', symbol: 'XAUUSD', quantity: 0.1, stop_loss: null, take_profit: null,
  });
});

test('ManualTradeRequest is explicitly tagged as not an ADR-033 TradeIntent', () => {
  assert.match(manualExecutionSource, /Explicitly NOT an ADR-033 TradeIntent/);
  assert.match(manualExecutionSource, /MANUAL_TRADE_REQUEST_PROPOSAL/);
});

test('health/mode fields default to an honest unknown rather than an invented value', () => {
  const markup = html();
  assert.match(markup, /Trading Mode<\/dt><dd>UNKNOWN<\/dd>/);
  assert.match(markup, /System Health<\/dt><dd>UNKNOWN<\/dd>/);
  assert.match(markup, /Broker\/Feed Health<\/dt><dd>UNKNOWN<\/dd>/);
});
