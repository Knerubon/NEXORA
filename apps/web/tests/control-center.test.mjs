import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ControlCenter, mockData, sources } from './control-center-harness.mjs';

const render = (snapshot) => renderToStaticMarkup(createElement(ControlCenter, { snapshot }));

test('AUTO is locked, not just defaulted off', () => {
  const html = render(mockData.MOCK_SNAPSHOT_IDLE);
  assert.match(html, /AUTO TRADE: OFF/);
  assert.match(html, /cc-mode-locked/);
  assert.match(html, /disabled=""/);
  assert.match(html, /Locked — Phase 1/);
});

test('SHADOW is the default rendered mode for the idle snapshot', () => {
  const html = render(mockData.MOCK_SNAPSHOT_IDLE);
  assert.match(html, /data-cc-mode="SHADOW"/);
});

test('position monitor renders SL/TP/break-even/trailing/partial-close as evidence, not computed values', () => {
  const html = render(mockData.MOCK_SNAPSHOT_WITH_POSITION);
  assert.match(html, /EURUSD/);
  assert.match(html, /Break-even/);
  assert.match(html, /Armed/);
  assert.match(html, /50% @ 1\.08700/);
});

test('block reasons and degraded health render from snapshot verbatim', () => {
  const html = render(mockData.MOCK_SNAPSHOT_BLOCKED);
  assert.match(html, /DEGRADED/);
  assert.match(html, /RISK_DAILY_LOSS_LIMIT/);
  assert.match(html, /FEED_INTERRUPTED/);
});

test('scanners report NOT_IMPLEMENTED honestly, no fabricated activity', () => {
  const html = render(mockData.MOCK_SNAPSHOT_IDLE);
  assert.match(html, /News Scanner/);
  assert.match(html, /Not implemented/);
  assert.match(html, /Social Scanner/);
});

test('no fetch/axios/XMLHttpRequest/order call anywhere in the control-center tree', () => {
  for (const [name, src] of Object.entries(sources)) {
    assert.doesNotMatch(src, /fetch\(|axios\.|new XMLHttpRequest|order_send\(|placeOrder\(/, `${name} must not call out to any execution/network API`);
  }
});
