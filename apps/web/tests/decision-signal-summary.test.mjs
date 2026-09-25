import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';

// UI-DECISION-1: Signal Score + P&F explanation under `Decision · Bias` in MatrixSummary.
const require = createRequire(import.meta.url);
const load = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const exports = {};
  new Function('require', 'exports', outputText)(require, exports);
  return exports;
};
const source = readFileSync(new URL('../app/signal-intelligence.tsx', import.meta.url), 'utf8');
const current = load(source);
// Pre-feature base (origin/main when UI-DECISION-1 started).
const baseline = load(execFileSync('git', ['show', 'ec0aad50c97b67006414f2156b0bbc1b47fb4766:apps/web/app/signal-intelligence.tsx'], { encoding: 'utf8' }));
const summary = (module, props) => renderToStaticMarkup(createElement(module.MatrixSummary, props));
const render = (props) => summary(current, props);

const pnf = (reason, polarity = 'bullish', code = 'pnf_extension_bullish') => ({ component: 'pnf', code, reason, points: 20, polarity });
const structure = { component: 'structure', code: 'structure_higher_low', reason: 'Structure shows higher low support after a pivot trough.', points: 25, polarity: 'bullish' };
const decision = (overrides = {}) => ({
  action: 'WAIT', score: 49, buy_strength: 45, sell_strength: 20, strength_available: true,
  entry_zone: null, invalidation_price: null, invalidation_reason: null, targets: [], risk_reward: null,
  patterns: [], positive_evidence: [pnf('P&F extension remains bullish.')],
  negative_evidence: [{ component: 'matrix', code: 'matrix_mixed', reason: 'Matrix disagreement detected.', points: 0, polarity: 'neutral' }],
  ...overrides,
});
const context = { bias: 'BEARISH_LEAN', state: 'DEVELOPING', alignment: { aligned: 2, total: 3 }, reasons: [], waiting_for: [] };
const decisionLine = /<p>Decision <strong class="decision-[a-z]+">[A-Za-z]+<\/strong> · Bias: <strong class="[a-z]*">[A-Za-z ]+<\/strong><\/p>/;
const scoreLine = (text) => `<p class="signal-summary-score">Signal Score <strong>${text}</strong></p>`;
const reasonLine = (text) => `<p class="signal-summary-reason">${text}</p>`;
const hasSummary = (html) => html.includes('signal-summary-');

test('WAIT + score 49 + bullish P&F reason renders immediately under the unchanged Decision/Bias line', () => {
  const html = render({ decision: decision(), decisionContext: context });
  const line = '<p>Decision <strong class="decision-wait">WAIT</strong> · Bias: <strong class="down">Bearish lean</strong></p>';
  assert.ok(html.includes(line + scoreLine('49/100') + reasonLine('P&amp;F extension remains bullish.') + '<small>Independent evidence strength'));
});

test('BUY renders the backend score and bullish reversal reason verbatim', () => {
  const html = render({ decision: decision({ action: 'BUY', score: 72, positive_evidence: [structure, pnf('P&F reversal O→X confirmed.', 'bullish', 'pnf_reversal_bullish')] }) });
  assert.ok(html.includes('<strong class="decision-buy">BUY</strong>'));
  assert.ok(html.includes(scoreLine('72/100') + reasonLine('P&amp;F reversal O→X confirmed.')));
});

test('SELL renders the backend score and bearish reason verbatim', () => {
  const html = render({ decision: decision({ action: 'SELL', score: 66, positive_evidence: [pnf('P&F extension remains bearish.', 'bearish', 'pnf_extension_bearish')] }) });
  assert.ok(html.includes('<strong class="decision-sell">SELL</strong>'));
  assert.ok(html.includes(scoreLine('66/100') + reasonLine('P&amp;F extension remains bearish.')));
});

test('strength_available false or absent shows Signal Score Unavailable, never the placeholder score', () => {
  for (const d of [decision({ strength_available: false, score: 0 }), decision({ strength_available: false }), decision({ strength_available: undefined })]) {
    const html = render({ decision: d });
    assert.ok(html.includes(scoreLine('Unavailable')));
    assert.ok(!/Signal Score <strong>[^<]*\/100/.test(html));
  }
});

test('missing, non-integer, non-numeric or out-of-range score shows Unavailable', () => {
  for (const score of [undefined, null, 49.5, NaN, Infinity, '49', -1, 101]) {
    const html = render({ decision: decision({ score }) });
    assert.ok(html.includes(scoreLine('Unavailable')), String(score));
    assert.ok(!/Signal Score <strong>[^<]*\/100/.test(html), String(score));
  }
  for (const score of [0, 100]) assert.ok(render({ decision: decision({ score }) }).includes(scoreLine(`${score}/100`)));
});

test('no P&F evidence renders the score line only, with no placeholder sentence', () => {
  for (const positive_evidence of [[], undefined, [pnf('')]]) {
    const html = render({ decision: decision({ positive_evidence }) });
    assert.ok(html.includes(scoreLine('49/100') + '<small>'));
    assert.ok(!html.includes('signal-summary-reason'));
  }
});

test('non-P&F positive or negative evidence is never used as the explanation', () => {
  const html = render({ decision: decision({ positive_evidence: [structure], negative_evidence: [{ ...pnf('Negative P&F text.'), polarity: 'neutral' }] }) });
  assert.ok(!html.includes('signal-summary-reason'));
  assert.ok(!html.includes(structure.reason) && !html.includes('Negative P&amp;F text.'));
  const first = render({ decision: decision({ positive_evidence: [structure, pnf('P&F extension remains bullish.')] }) });
  assert.ok(first.includes(reasonLine('P&amp;F extension remains bullish.')) && !first.includes(structure.reason));
});

test('decision unavailable shows Signal Score Unavailable and no explanation', () => {
  const html = render({});
  assert.ok(html.includes('<p>Decision <strong class="decision-unavailable">Unavailable</strong> · Bias: <strong class="">Unavailable</strong></p>' + scoreLine('Unavailable') + '<small>'));
  assert.ok(!html.includes('signal-summary-reason'));
});

test('feature OFF renders markup identical to the pre-feature MatrixSummary', () => {
  assert.equal(current.DECISION_SIGNAL_SUMMARY_ENABLED, true);
  const cases = [
    { decision: decision(), decisionContext: context, symbol: 'XAUUSD', matrixStatus: 'ready', researchMode: 'live_observation' },
    { decision: decision({ action: 'BUY', score: 72 }) },
    { decision: decision({ strength_available: false }), connectionError: true },
    {},
  ];
  for (const props of cases) {
    const off = render({ ...props, signalSummaryEnabled: false });
    assert.equal(off, summary(baseline, props));
    assert.ok(!hasSummary(off) && !off.includes('Signal Score'));
  }
});

test('feature ON only inserts the summary; all existing MatrixSummary markup is unchanged', () => {
  const cases = [{ decision: decision(), decisionContext: context, symbol: 'XAUUSD' }, { decision: decision({ positive_evidence: [] }) }, {}];
  for (const props of cases) {
    const html = render(props);
    const stripped = html.replace(/<p class="signal-summary-score">.*?<\/p>(<p class="signal-summary-reason">.*?<\/p>)?/, '');
    assert.equal(stripped, summary(baseline, props));
    assert.match(html, decisionLine);
  }
});

test('rendering does not mutate props', () => {
  const deepFreeze = (value) => { if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); } return value; };
  const props = deepFreeze({ decision: decision({ positive_evidence: [structure, pnf('P&F extension remains bullish.')] }), decisionContext: context });
  const before = JSON.stringify(props);
  assert.doesNotThrow(() => render(props));
  assert.equal(JSON.stringify(props), before);
});

test('no score or explanation text is hard-coded in the component', () => {
  assert.ok(!source.includes('49/100'));
  assert.ok(!source.includes('P&F extension remains bullish.'));
  assert.ok(!/extension remains|reversal O→X|reversal X→O/.test(source));
});

test('desktop and mobile share the same MatrixSummary; responsive CSS and full panel are untouched', () => {
  const float = readFileSync(new URL('../app/matrix-float.tsx', import.meta.url), 'utf8');
  assert.ok(float.includes('<MatrixSummary {...props} />'));
  const css = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');
  assert.match(css, /@media\(max-width:700px\) \{\s+\.matrix-float \{ position: static;/);
  assert.ok(!css.includes('signal-summary'));
  const full = (module) => renderToStaticMarkup(createElement(module.SignalIntelligence, { decision: decision(), decisionContext: context }));
  assert.equal(full(current), full(baseline));
});
