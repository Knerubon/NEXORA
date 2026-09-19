import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../app/signal-intelligence.tsx', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} });
const exports = {};
new Function('require', 'exports', outputText)(require, exports);
const render = decision => renderToStaticMarkup(createElement(exports.SignalIntelligence, { decision }));
const decision = {
  action: 'WAIT', score: 47, buy_strength: 78, sell_strength: 31, strength_available: true,
  entry_zone: null, invalidation_price: null, invalidation_reason: null, targets: [], risk_reward: null,
  patterns: [], positive_evidence: [], negative_evidence: [{code: 'matrix_mixed', reason: 'Matrix disagreement'}],
};

test('evaluated WAIT renders independent strengths and legacy score from backend', () => {
  const html = render(decision);
  for (const text of ['WAIT', '78/100', '31/100', '47/100', 'Matrix disagreement', 'not win probability']) assert.ok(html.includes(text));
  assert.ok(!html.includes('22/100'));
});
test('initial, old payload and cooldown never render zero evaluated strength', () => {
  for (const d of [undefined, {...decision, strength_available: undefined}, {...decision, strength_available: false, buy_strength: 0, sell_strength: 0}, {...decision, buy_strength: null, sell_strength: null}]) {
    const html = render(d);
    assert.match(html, /BUY Strength <strong>Unavailable/);
    assert.match(html, /SELL Strength <strong>Unavailable/);
  }
  assert.match(render({...decision, buy_strength: 0}), /BUY Strength <strong>0\/100/);
});
test('trade plan and patterns render exact backend values, without inferred levels', () => {
  const html = render({...decision, action: 'SELL', entry_zone: {low: '100.125', high: '101.875', reason: 'Confirmed box'},
    invalidation_price: '104.333', invalidation_reason: 'Confirmed resistance',
    targets: [{name: 'TP1', price: '97.123', method: 'risk_reward'}, {name: 'TP2', price: '93.456', method: 'risk_reward'}], risk_reward: '2.5',
    patterns: [{pattern_type: 'head_and_shoulders', direction: 'bearish', relation: 'confirmation', confirmation_time: '2026-02-03T09:13:00Z'}],
  });
  for (const text of ['100.125', '101.875', '104.333', '97.123', '93.456', '1:2.5', 'head_and_shoulders', 'bearish', 'confirmation', 'Confirmed resistance']) assert.ok(html.includes(text));
});
test('dashboard keeps chart first and reads current decision instead of latest historical signal', () => {
  const page = readFileSync(new URL('../app/page.tsx', import.meta.url), 'utf8');
  assert.ok(page.indexOf('<StructureChart output=') < page.indexOf('<SignalIntelligence decision='));
  assert.ok(page.includes('<SignalIntelligence decision={output.signals?.decision} />'));
});
