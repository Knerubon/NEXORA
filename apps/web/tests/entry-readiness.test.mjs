import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { appRequire, compile, entryReadiness as er, entryReadinessSource, withoutEntryReadiness as withoutBlock } from './entry-readiness-harness.mjs';

// UI-READINESS-1: backend Entry Readiness (ADR-021) presentation + session-only display toggle.
const require = createRequire(import.meta.url);
const BASE = '816c8d701d9678e2cc78fe46857eb28cf0b19c3f';
const load = (source, resolve) => { const exports = {}; new Function('require', 'exports', compile(source))(resolve, exports); return exports; };
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const current = load(read('../app/signal-intelligence.tsx'), appRequire);
const baseline = load(execFileSync('git', ['show', `${BASE}:apps/web/app/signal-intelligence.tsx`], { encoding: 'utf8' }), require);

const snapshot = (overrides = {}) => ({ schema_version: 1, symbol: 'XAUUSD', state: 'READY', signal_action: 'BUY',
  blockers: [], pending_confirmations: [], config_version: 'cv-7', ...overrides });
const blocker = { code: 'aligned_trendline_broken', side: 'SELL', trendline_kind: 'bearish_resistance', line_id: 'line-abc', reason: 'Aligned bearish resistance line is broken.' };
const pending = { code: 'aligned_trendline_retest_pending', side: 'BUY', trendline_kind: 'bullish_support', line_id: 'line-def', reason: 'Bullish support retest has not yet resolved to RETEST_HELD or RETEST_FAILED.' };
const decision = { action: 'BUY', score: 72, buy_strength: 80, sell_strength: 20, strength_available: true,
  entry_zone: null, invalidation_price: null, invalidation_reason: null, targets: [], risk_reward: null,
  patterns: [], positive_evidence: [{ component: 'pnf', code: 'pnf_up', reason: 'Rising boxes', points: 20, polarity: 'bullish' }], negative_evidence: [] };
const context = { bias: 'BULLISH', state: 'DIRECTION_CONFIRMED', alignment: { aligned: 3, total: 3 }, reasons: [], waiting_for: [] };
const live = { researchMode: 'live_observation', feedStatus: 'live', matrixStatus: 'ready' };

const view = (readiness, props = {}) => renderToStaticMarkup(createElement(er.EntryReadinessView, { readiness, enabled: true, ...props }));
const panel = (module, props) => renderToStaticMarkup(createElement(module.SignalIntelligence, { decision, decisionContext: context, ...live, ...props }));
const deepFreeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); } return value; };
const STATE_WORDS = /READY|DEVELOPING|BLOCKED|Blocker|Pending/;
// Visible content only: drops the static header tooltip and heading text.
const visible = html => html.replace(/title="[^"]*"/g, '').replace(/Entry Readiness/g, '');

test('1. valid backend Entry Readiness renders state, signal action and config verbatim', () => {
  const html = view(snapshot());
  assert.match(html, /data-entry-readiness-state="READY">State <strong class="entry-state-ready">READY<\/strong> · Signal <strong>BUY<\/strong>/);
  assert.ok(html.includes('config cv-7'));
  assert.ok(!html.includes('Blocker') && !html.includes('Pending') && !html.includes('Unavailable'));
});

test('2. backend state is used as-is, including canonical NOT_READY and contradictory combinations', () => {
  for (const state of er.ENTRY_READINESS_STATES) for (const signal_action of ['BUY', 'SELL', 'WAIT']) {
    const html = view(snapshot({ state, signal_action }));
    assert.ok(html.includes(`data-entry-readiness-state="${state}">State <strong class="entry-state-${state.toLowerCase()}">${state}</strong> · Signal <strong>${signal_action}</strong>`), `${state}/${signal_action}`);
  }
  // Not reconciled with the Signal: a WAIT decision panel still shows the backend READY verbatim.
  const html = panel(current, { decision: { ...decision, action: 'WAIT' }, entryReadiness: snapshot({ state: 'READY', signal_action: 'BUY' }) });
  assert.match(html, /entry-state-ready">READY<\/strong> · Signal <strong>BUY<\/strong>/);
  assert.match(view(snapshot({ state: 'NOT_READY', signal_action: 'WAIT' })), />NOT_READY</);
});

test('3. backend blockers render reason and code, with kind and line id inspectable', () => {
  const html = view(snapshot({ state: 'BLOCKED', signal_action: 'SELL', blockers: [blocker] }));
  assert.ok(html.includes('<li><strong>Blocker</strong> Aligned bearish resistance line is broken. <small title="bearish_resistance · line-abc">aligned_trendline_broken</small></li>'));
  assert.ok(!html.includes('Pending'));
});

test('4. backend pending confirmations render reason and code', () => {
  const html = view(snapshot({ state: 'DEVELOPING', pending_confirmations: [pending] }));
  assert.ok(html.includes('<li><strong>Pending</strong> Bullish support retest has not yet resolved to RETEST_HELD or RETEST_FAILED. <small title="bullish_support · line-def">aligned_trendline_retest_pending</small></li>'));
  assert.ok(!html.includes('Blocker'));
});

test('5. missing entry_readiness shows explicit Unavailable', () => {
  for (const value of [undefined, null]) {
    const html = view(value);
    assert.match(html, /data-entry-readiness-state="UNAVAILABLE">State <strong>Unavailable<\/strong><small>Not reported by backend<\/small>/);
    assert.doesNotMatch(visible(html), STATE_WORDS);
  }
});

test('6. malformed or unsupported payloads never produce an inferred readiness', () => {
  const cases = ['READY', 1, true, [], [snapshot()], snapshot({ schema_version: 2 }), snapshot({ schema_version: '1' }),
    snapshot({ state: 'ready' }), snapshot({ state: 'OK' }), snapshot({ state: undefined }), snapshot({ signal_action: 'HOLD' }),
    snapshot({ blockers: undefined }), snapshot({ blockers: [blocker, 'x'] }), snapshot({ pending_confirmations: [{ ...pending, reason: 5 }] }),
    snapshot({ state: 'BLOCKED', blockers: {} }), snapshot({ config_version: null }), snapshot({ symbol: 7 })];
  for (const value of cases) {
    assert.deepEqual(er.parseEntryReadiness(value), { status: 'unsupported' }, JSON.stringify(value));
    const html = view(value);
    assert.ok(html.includes('Unsupported backend payload — not interpreted'));
    assert.doesNotMatch(visible(html), STATE_WORDS, JSON.stringify(value));
  }
  const valid = snapshot();
  assert.equal(er.parseEntryReadiness(valid).snapshot, valid, 'valid payload is passed through by reference, not rebuilt');
});

test('7. toggle ON (default) shows Entry Readiness', () => {
  const html = renderToStaticMarkup(createElement(er.EntryReadiness, { readiness: snapshot() }));
  assert.match(html, /<button type="button" aria-label="Entry Readiness display" aria-pressed="true">ON<\/button>/);
  assert.ok(html.includes('data-entry-readiness="on"') && html.includes('entry-state-ready'));
  assert.ok(panel(current, { entryReadiness: snapshot() }).includes('entry-state-ready'));
});

test('8. toggle OFF hides Entry Readiness but keeps the control to re-enable it', () => {
  for (const html of [renderToStaticMarkup(createElement(er.EntryReadiness, { readiness: snapshot(), initialEnabled: false })),
    view(snapshot(), { enabled: false })]) {
    assert.match(html, /aria-pressed="false">OFF<\/button>/);
    assert.ok(html.includes('data-entry-readiness="off"'));
    assert.ok(!html.includes('entry-readiness-state'));
    assert.doesNotMatch(visible(html), STATE_WORDS);
  }
  const off = panel(current, { entryReadiness: snapshot({ state: 'BLOCKED', blockers: [blocker] }), entryReadinessInitiallyEnabled: false });
  assert.ok(!off.includes('BLOCKED') && !off.includes('aligned_trendline_broken'));
});

test('9. toggle ON/OFF never mutates the received backend data', () => {
  const readiness = deepFreeze(snapshot({ state: 'BLOCKED', signal_action: 'SELL', blockers: [blocker] }));
  const before = structuredClone(readiness);
  for (const enabled of [true, false]) {
    view(readiness, { enabled });
    panel(current, { entryReadiness: readiness, entryReadinessInitiallyEnabled: enabled });
  }
  assert.deepEqual(readiness, before);
});

test('10-11-13. Decision, Bias, Signal Score and the rest of Signal Intelligence are identical to base with ON or OFF', () => {
  const decisions = [decision, { ...decision, action: 'WAIT', score: 47, strength_available: false }, { ...decision, action: 'SELL', score: 66 }, undefined];
  const contexts = [context, undefined, { ...context, bias: 'MIXED', state: 'DEVELOPING', reasons: ['Matrix disagreement detected.'], waiting_for: ['Structural confirmation.'] }];
  const readinesses = [snapshot(), snapshot({ state: 'BLOCKED', blockers: [blocker] }), undefined, { state: 'bogus' }];
  for (const d of decisions) for (const c of contexts) for (const r of readinesses) for (const enabled of [true, false]) for (const extra of [live, { researchMode: 'recorded', connectionError: true }]) {
    const props = { decision: d, decisionContext: c, ...extra };
    assert.equal(withoutBlock(panel(current, { ...props, entryReadiness: r, entryReadinessInitiallyEnabled: enabled })), panel(baseline, props));
  }
  // The floating Matrix card is untouched and carries no Entry Readiness.
  const summary = module => renderToStaticMarkup(createElement(module.MatrixSummary, { decision, decisionContext: context, symbol: 'XAUUSD', entryReadiness: snapshot() }));
  assert.equal(summary(current), summary(baseline));
  assert.ok(!summary(current).includes('entry-readiness'));
});

test('12/17. no frontend readiness derivation: Trendline, P&F and Signal evidence never produce a state', () => {
  // Negative regression: rich BUY evidence with a supportive trendline but no backend entry_readiness stays Unavailable.
  const html = panel(current, { decision: { ...decision, action: 'BUY', score: 95 }, entryReadiness: undefined,
    trendline: { active_bullish: { line_id: 'l1', state: 'active' } }, columns: [{ column_id: 1, direction: 'X' }] });
  assert.match(html, /data-entry-readiness-state="UNAVAILABLE"/);
  assert.ok(!html.includes('entry-state-'));
  const code = entryReadinessSource.replace(/\/\/.*$/gm, '').replace(/title="[^"]*"/g, '');
  for (const forbidden of [/active_bullish|active_bearish/, /retest(ing|_held|_failed)|broken/, /\.columns|transitions|pattern/, /positive_evidence|negative_evidence|buy_strength|sell_strength|\.score|\.action\b/, /decision|trendline\b/i])
    assert.doesNotMatch(code, forbidden, String(forbidden));
  // The only readiness input from the page is the backend field.
  const page = read('../app/page.tsx');
  assert.equal(page.match(/entry_readiness/g).length, 2);
  assert.ok(page.includes('entryReadiness={output.entry_readiness} />'));
  const chart = page.slice(page.indexOf('function StructureChart('), page.indexOf('export default function Home'));
  assert.ok(!/entry_?readiness/i.test(chart), 'P&F chart does not read or render Entry Readiness');
});

test('14. stale / last-received snapshot follows the existing Signal Intelligence convention', () => {
  const readiness = snapshot({ state: 'BLOCKED', signal_action: 'SELL', blockers: [blocker] });
  const fresh = panel(current, { entryReadiness: readiness });
  assert.ok(!fresh.includes('Last received') && !fresh.includes('Recorded / last received'));
  for (const extra of [{ researchMode: 'recorded_or_unavailable' }, { connectionError: true }]) {
    const html = panel(current, { entryReadiness: readiness, ...extra });
    assert.ok(html.includes('Recorded / last received calculation'), 'existing panel note');
    assert.match(html, /entry-state-blocked">BLOCKED<\/strong> · Signal <strong>SELL<\/strong><span class="entry-readiness-stale">Last received<\/span>/);
  }
  assert.ok(!panel(current, { entryReadiness: undefined, connectionError: true }).includes('entry-readiness-stale'));
});

test('15-16. desktop and mobile layout: block shrinks inside the existing responsive grid', () => {
  const css = read('../app/globals.css');
  assert.match(css, /\.entry-readiness \{[^}]*min-width: 0;/);
  assert.match(css, /\.entry-readiness-state \{[^}]*flex-wrap: wrap;/);
  assert.match(css, /\.entry-readiness-items \{[^}]*overflow-wrap: anywhere;/);
  assert.match(css, /@media \(max-width: 540px\) \{ \.entry-readiness-head button \{ min-height: 32px; \} \}/);
  const block = css.slice(css.indexOf('/* UI-READINESS-1'), css.indexOf('\n', css.indexOf('@media (max-width: 540px) { .entry-readiness-head')));
  assert.ok(block.includes('.entry-readiness {') && !block.includes('.analysis-summary'));
  assert.doesNotMatch(block, /position:\s*(absolute|fixed)|(^|[^-])width:\s*\d/, 'no overlay positioning or fixed width');
  // Existing responsive rules the block relies on remain in place.
  assert.match(css, /\.decision-grid \{ grid-template-columns: minmax\(0, 1fr\);/);
  assert.match(css, /\.decision-grid > div \{ min-width: 0;/);
  const html = panel(current, { entryReadiness: snapshot({ state: 'BLOCKED', blockers: [{ ...blocker, reason: '<b>x</b>' }] }) });
  assert.ok(html.includes('&lt;b&gt;x&lt;/b&gt;') && !html.includes('<b>x</b>'));
});

test('18. toggle is session-only: no storage, network or backend persistence', () => {
  const code = entryReadinessSource;
  assert.doesNotMatch(code, /localStorage|sessionStorage|document\.cookie|indexedDB|fetch\(|WebSocket|XMLHttpRequest|sendBeacon/);
  assert.match(code, /useState\(initialEnabled\)/);
  const page = read('../app/page.tsx');
  const base = execFileSync('git', ['show', `${BASE}:apps/web/app/page.tsx`], { encoding: 'utf8' });
  // Same backend requests as base: no new endpoint or POST exists to persist the toggle.
  const paths = src => src.match(/const paths = \[[^\]]*\]/)[0];
  assert.equal(paths(page), paths(base));
  for (const pattern of [/fetch\(/g, /method: "POST"/g])
    assert.equal((page.match(pattern) ?? []).length, (base.match(pattern) ?? []).length, String(pattern));
  for (const line of page.split('\n').filter(l => /entry_?readiness/i.test(l)))
    assert.doesNotMatch(line, /Storage|fetch|cookie|act\(/, line);
});
