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
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} });
const exports = {};
new Function('require', 'exports', outputText)(require, exports);
const render = (decision, props = {}) => renderToStaticMarkup(createElement(exports.SignalIntelligence, { decision, ...props }));
const decision = {
  action: 'WAIT', score: 47, buy_strength: 78, sell_strength: 31, strength_available: true,
  entry_zone: null, invalidation_price: null, invalidation_reason: null, targets: [], risk_reward: null,
  patterns: [], positive_evidence: [], negative_evidence: [{component: 'matrix', code: 'matrix_mixed', reason: 'Matrix disagreement', points: 0, polarity: 'neutral'}],
};

test('evaluated WAIT renders independent strengths, exact arcs and legacy score', () => {
  const html = render(decision);
  for (const text of ['WAIT', '78/100', '31/100', '47/100', 'Matrix disagreement', 'not win probability']) assert.ok(html.includes(text));
  assert.match(html, /stroke-dasharray="78 100"/);
  assert.match(html, /stroke-dasharray="31 100"/);
  assert.ok(!html.includes('22/100'));
});
test('initial, old payload, missing inputs and cooldown never render evaluated zero', () => {
  for (const d of [undefined, {...decision, strength_available: undefined}, {...decision, strength_available: false, buy_strength: 0, sell_strength: 0}, {...decision, buy_strength: null, sell_strength: null}]) {
    const html = render(d);
    assert.match(html, /aria-label="BUY Strength: Unavailable"/);
    assert.match(html, /aria-label="SELL Strength: Unavailable"/);
    assert.ok(!html.includes('ring-value'));
  }
  assert.match(render({...decision, buy_strength: 0}), /aria-label="BUY Strength: 0\/100"/);
});
test('nullable and invalid strengths are independent; neither is normalized or clamped', () => {
  for (const value of [null, undefined, -1, 101, NaN, Infinity]) {
    const html = render({...decision, buy_strength: value});
    assert.match(html, /BUY Strength: Unavailable/);
    assert.match(html, /SELL Strength: 31\/100/);
  }
  assert.match(render({...decision, buy_strength: 100, sell_strength: 100}), /SELL Strength: 100\/100/);
});
test('BUY SELL WAIT use backend decision and score regardless of strength ranking', () => {
  for (const action of ['BUY', 'SELL', 'WAIT']) {
    const html = render({...decision, action, score: 65});
    assert.match(html, new RegExp(`Why ${action}\\?`));
    assert.match(html, /Signal Score <strong>65\/100/);
  }
});
test('trade plan and patterns render exact backend values, without inferred levels', () => {
  const html = render({...decision, action: 'SELL', entry_zone: {low: '100.125', high: '101.875', reason: 'Confirmed box'},
    invalidation_price: '104.333', invalidation_reason: 'Confirmed resistance',
    targets: [{name: 'TP1', price: '97.123', method: 'risk_reward'}, {name: 'TP2', price: '93.456', method: 'risk_reward'}], risk_reward: '2.5',
    patterns: [{pattern_type: 'head_and_shoulders', direction: 'bearish', relation: 'confirmation', confirmation_time: '2026-02-03T09:13:00Z'}],
  });
  for (const text of ['100.125', '101.875', '104.333', '97.123', '93.456', '1:2.5', 'head_and_shoulders', 'bearish', 'confirmation', 'Confirmed resistance']) assert.ok(html.includes(text));
  assert.match(html, /<details><summary>Trade Plan · View Setup<\/summary>/);
  assert.match(html, /<details><summary>Why SELL\?<\/summary>/);
  assert.ok(!html.includes('<details open'));
});
test('explainability respects polarity, cautions and WAIT without treating positive as decision support', () => {
  const positive_evidence = [
    {component: 'pnf', code: 'pnf_up', reason: 'Rising boxes', points: 20, polarity: 'bullish'},
    {component: 'structure', code: 'structure_down', reason: 'Lower highs', points: 25, polarity: 'bearish'},
  ];
  const html = render({...decision, action: 'SELL', positive_evidence});
  assert.match(html, /P&amp;F<\/strong><span>Opposes SELL/);
  assert.match(html, /Structure<\/strong><span>Supports SELL/);
  assert.match(html, /Matrix<\/strong><span>Caution \/ conflict/);
  assert.match(html, /20 points.*bullish/);
  assert.match(html, /Pattern<\/strong><span>Not reported/);
  const wait = render({...decision, positive_evidence});
  assert.ok(!wait.includes('Supports WAIT') && !wait.includes('Opposes WAIT'));
  assert.match(wait, /bullish evidence/);
  assert.match(wait, /bearish evidence/);
});
test('readiness and quote status come from backend; bias stays honestly unavailable without a backend decision context', () => {
  const props = {symbol: 'XAUUSD-STD', matrixStatus: 'unavailable', feedStatus: 'stale', quoteTime: '2026-09-18T20:56:59Z',
    matrix: {alignment: 'aligned_bearish', resolutions: [{name: 'fast', direction: 'O', status: 'ready'}, {name: 'medium', direction: 'none', status: 'warmup'}]},
    regime: {label: 'trend', reason: 'trend_slope_threshold'}};
  const html = render(decision, props);
  for (const text of ['XAUUSD-STD', 'Matrix: UNAVAILABLE', 'Feed: stale', 'Recorded / last received', 'FAST', 'warmup', 'not readiness to BUY or SELL', 'not timeframes']) assert.ok(html.includes(text), text);
  // No decisionContext was supplied, even though the raw matrix prop shows a clear
  // aligned_bearish reading: the frontend must not derive bias itself from it.
  assert.match(html, /Bias ⓘ<\/h3>\s*<strong class="">Unavailable<\/strong>/);
  assert.ok(!html.includes('BEARISH'));
  const ready = render(decision, {...props, matrixStatus: 'ready', researchMode: 'live_observation', feedStatus: 'live'});
  assert.match(ready, /Matrix: READY/);
  assert.ok(!ready.includes('Recorded / last received'));
  const disconnected = render(decision, {...props, matrixStatus: 'ready', feedStatus: 'live', connectionError: true});
  assert.ok(!disconnected.includes('Matrix: READY') && !disconnected.includes('Feed: live'));
});
test('decision context renders backend-owned bias, state and alignment without frontend inference', () => {
  const context = {bias: 'BULLISH_LEAN', state: 'DEVELOPING', alignment: {aligned: 2, total: 3}, reasons: [], waiting_for: []};
  const html = render(decision, {decisionContext: context});
  assert.match(html, /Bias ⓘ<\/h3>\s*<strong class="up">Bullish lean<\/strong>/);
  assert.match(html, /State<\/span> <strong>Developing<\/strong>/);
  assert.match(html, /Alignment<\/span> <strong>2 \/ 3<\/strong>/);
  const confirmed = render(decision, {decisionContext: {...context, bias: 'BULLISH', state: 'DIRECTION_CONFIRMED'}});
  assert.match(confirmed, /State<\/span> <strong>Direction confirmed<\/strong>/);
  assert.ok(!confirmed.includes('>Confirmed<'));
});
test('WHY WAIT reasons and waiting-for render only for WAIT decisions, reusing backend text verbatim', () => {
  const context = {bias: 'BULLISH_LEAN', state: 'DEVELOPING', alignment: {aligned: 2, total: 3},
    reasons: ['Matrix disagreement detected.', 'SLOW conflicts with FAST/MEDIUM.'],
    waiting_for: ['Structural confirmation.']};
  const html = render(decision, {decisionContext: context});
  assert.match(html, /<h4>Why WAIT\?<\/h4><ul><li>Matrix disagreement detected\.<\/li><li>SLOW conflicts with FAST\/MEDIUM\.<\/li><\/ul>/);
  assert.match(html, /<h4>Waiting for<\/h4><ul><li>Structural confirmation\.<\/li><\/ul>/);

  const buyHtml = render({...decision, action: 'BUY'}, {decisionContext: {...context, bias: 'BULLISH', state: 'DIRECTION_CONFIRMED'}});
  assert.ok(!buyHtml.includes('Why WAIT?') && !buyHtml.includes('Waiting for'));

  const noReasons = render(decision, {decisionContext: {...context, reasons: [], waiting_for: []}});
  assert.ok(!noReasons.includes('wait-context'));
});
test('dashboard keeps chart first, uses current decision and removes duplicate Matrix views', () => {
  const page = readFileSync(new URL('../app/page.tsx', import.meta.url), 'utf8');
  assert.ok(page.indexOf('<StructureChart output=') < page.indexOf('<SignalIntelligence decision='));
  assert.ok(page.includes('<SignalIntelligence decision={output.signals?.decision}'));
  assert.ok(page.includes('matrixStatus={state?.matrix_status}'));
  assert.ok(page.includes('<MatrixFloat {...panelProps} decision={output.signals?.decision} matrix={output.matrix}'));
  assert.ok(!page.includes('Matrix &amp; Regime'));
});
test('responsive panel provides shrinkable columns, wrapped metadata and keyboard disclosures', () => {
  const css = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');
  assert.match(css, /\.decision-grid > div \{ min-width: 0;/);
  assert.match(css, /\.decision-grid \{ grid-template-columns: minmax\(0, 1fr\);/);
  assert.match(css, /\.decision-details summary:focus-visible/);
  const html = render(decision, {symbol: '<script>long_symbol</script>'});
  assert.ok(html.includes('&lt;script&gt;') && !html.includes('<script>'));
  assert.equal((html.match(/<summary>/g) ?? []).length, 2);
});

test('floating summary uses identical independent current strengths and an honest bias default', () => {
  const html = renderToStaticMarkup(createElement(exports.MatrixSummary, { decision, symbol: 'XAUUSD', matrixStatus: 'ready' }));
  for (const text of ['78/100', '31/100', 'WAIT', 'XAUUSD', 'READY', 'Recorded / last received']) assert.ok(html.includes(text), text);
  assert.match(html, /Bias: <strong class="">Unavailable<\/strong>/);
  const cooldown = renderToStaticMarkup(createElement(exports.MatrixSummary, { decision: {...decision, strength_available: false} }));
  assert.ok(!cooldown.includes('ring-value'));
  const context = { bias: 'BEARISH', state: 'DIRECTION_CONFIRMED', alignment: { aligned: 3, total: 3 }, reasons: [], waiting_for: [] };
  const withBias = renderToStaticMarkup(createElement(exports.MatrixSummary, { decision, decisionContext: context, symbol: 'XAUUSD' }));
  assert.match(withBias, /Bias: <strong class="down">Bearish<\/strong>/);
});
test('full analysis includes deterministic reasons and recent backend signal history', () => {
  const html = render(decision, {history: [{signal_id:'a', side:'SELL', status:'confirmed', decision_time:'2026-09-20T10:00:00Z', reasons:['Recorded reason'], source_refs:['ref-1']}]});
  for (const text of ['AI วิเคราะห์', 'Trade Plan', 'Pattern', 'Recent Signals', 'Recorded reason', 'ref-1', '2026-09-20T10:00:00Z']) assert.ok(html.includes(text), text);
});
