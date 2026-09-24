import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { model, modelSource, overlaySource, overlays, renderPageChart, svgOf } from './overlay-harness.mjs';
import { overlayOutput } from './overlay-fixture.mjs';

const page = readFileSync(new URL('../app/page.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');
const render = (output, initialLayers) => renderPageChart(page, { output, initialLayers });
const svg = (output, initialLayers) => svgOf(render(output, initialLayers)).replaceAll('<!-- -->', '');
const overlayGroup = markup => markup.match(/<g data-chart-overlays="">[\s\S]*<\/g><\/svg>/)?.[0] ?? '';
const count = (markup, pattern) => (markup.match(pattern) ?? []).length;
const deepFreeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); } return value; };
const attr = (markup, name) => Number(markup.match(new RegExp(`${name}="(-?[\\d.]+)"`))[1]);

test('defaults: every visual layer is ON and controls reflect layer state', () => {
  assert.deepEqual(model.DEFAULT_LAYERS, { trendline: true, sr: true, patterns: true });
  const html = render(overlayOutput());
  assert.match(html, /role="group" aria-label="Chart layers"/);
  for (const label of ['Trendline', 'S/R', 'Patterns']) assert.match(html, new RegExp(`aria-pressed="true">${label.replace('/', '\\/')}</button>`));
  const off = renderToStaticMarkup(createElement(overlays.LayerControls, { layers: model.LAYERS_OFF, onToggle() {} }));
  assert.equal(count(off, /aria-pressed="false"/g), 3);
});

const trendLine = markup => overlayGroup(markup).match(/<g data-overlay="trendline"[\s\S]*?<\/g>/)?.[0];
const visibleLine = markup => trendLine(markup).match(/<line[^>]*stroke="#2563eb"[^>]*>/)[0];

test('trendline: endpoint is (latest backend P&F column, projected_price_at_latest_column)', () => {
  const output = overlayOutput();
  const [line] = model.trendlineOverlays(output.trendline, output.columns);
  assert.equal(model.latestColumnId(output.columns), output.columns.at(-1).column_id);
  assert.deepEqual([line.from, line.to, line.projectedColumn], [{ column: 2, price: 102 }, { column: 7, price: 107 }, 7]);
  const drawn = trendLine(svg(output));
  assert.match(drawn, /data-kind="bullish_support" data-state="retesting"/);
  const visible = visibleLine(svg(output));
  assert.equal(attr(visible, 'x1'), 90 + 1 * 30);
  assert.equal(attr(visible, 'x2'), 90 + 6 * 30);
  assert.equal(attr(visible, 'y1') - attr(visible, 'y2'), (107 - 102) * 26);
});

test('trendline: age_columns never drives an x coordinate', () => {
  for (const age_columns of [0, 3, 99, -5]) {
    const output = overlayOutput();
    output.trendline.active_bullish.age_columns = age_columns;
    assert.deepEqual(model.trendlineOverlays(output.trendline, output.columns)[0].to, { column: 7, price: 107 });
    assert.equal(attr(visibleLine(svg(output)), 'x2'), 270);
  }
  assert.equal(count(modelSource, /age_columns/g), 1, 'only the TrendlineLine type declaration mentions age_columns');
  assert.doesNotMatch(overlaySource, /age_columns/);
});

test('trendline: a new P&F column moves the endpoint to that actual column', () => {
  const output = overlayOutput();
  output.columns.push({ column_id: 8, direction: 'O', open_price: '107', close_price: '105' });
  output.transitions.push({ column_id: 8, direction: 'O', from_price: '107', to_price: '105', effective_box_size: '1', boxes_moved: 2, identity_key: 'fixture-t8' });
  output.trendline.active_bullish.projected_price_at_latest_column = '108';
  assert.deepEqual(model.trendlineOverlays(output.trendline, output.columns)[0].to, { column: 8, price: 108 });
  assert.equal(attr(visibleLine(svg(output)), 'x2'), 90 + 7 * 30);
});

test('trendline: missing latest column or non-current projection fails closed without extrapolation', () => {
  const output = overlayOutput();
  const anchorOnly = { column: 4, price: 104 };
  for (const columns of [undefined, [], [{ column_id: 'x' }], [{ column_id: 3 }], [{ column_id: 7.5 }]]) {
    const [line] = model.trendlineOverlays(output.trendline, columns);
    assert.deepEqual([line.to, line.projectedColumn], [anchorOnly, null]);
  }
  for (const state of ['retest_held', 'retest_failed', 'replaced']) {
    const resolved = overlayOutput(); resolved.trendline.active_bullish.state = state;
    const [line] = model.trendlineOverlays(resolved.trendline, resolved.columns);
    assert.deepEqual([line.to, line.projectedColumn], [anchorOnly, null], state);
    assert.equal(attr(visibleLine(svg(resolved)), 'x2'), 90 + 3 * 30);
  }
  const bad = overlayOutput(); bad.trendline.active_bullish.projected_price_at_latest_column = 'not-a-price';
  assert.deepEqual(model.trendlineOverlays(bad.trendline, bad.columns)[0].to, anchorOnly);
});

test('trendline: absent evidence draws nothing; hidden when the layer is OFF; historical lines never drawn', () => {
  const output = overlayOutput();
  output.trendline = { ...output.trendline, active_bullish: null, history: [output.trendline.active_bullish] };
  assert.doesNotMatch(svg(output), /data-overlay="trendline/);
  assert.doesNotMatch(svg(overlayOutput(), { ...model.DEFAULT_LAYERS, trendline: false }), /data-overlay="trendline/);
  assert.deepEqual(model.trendlineOverlays(null, overlayOutput().columns), []);
});

test('trendline: anchors left of the 60-column window are clipped; endpoint stays on the latest column', () => {
  const output = overlayOutput();
  output.columns = Array.from({ length: 70 }, (_, i) => ({ column_id: i + 1, direction: i % 2 ? 'O' : 'X', open_price: '100', close_price: '101' }));
  const drawn = overlayGroup(svg(output));
  assert.match(drawn, /<clipPath id="pnf-overlay-clip"><rect x="75" y="13"/);
  assert.match(drawn, /<g clip-path="url\(#pnf-overlay-clip\)">/);
  const visible = visibleLine(svg(output));
  assert.equal(attr(visible, 'x1'), 90 + (2 - 11) * 30);
  assert.ok(attr(visible, 'x1') < 75, 'anchor is outside the clip rect');
  assert.equal(attr(visible, 'x2'), 90 + (70 - 11) * 30);
});

test('layer toggles are render-only: backend output is never mutated by any layer combination', () => {
  const output = deepFreeze(overlayOutput());
  for (const trendline of [true, false]) for (const sr of [true, false]) for (const patterns of [true, false]) render(output, { trendline, sr, patterns });
  assert.deepEqual(output, overlayOutput());
  assert.doesNotMatch(overlaySource + modelSource, /output\.[a-z_]+\s*=[^=]|\.push\(\s*output|delete output/);
});

test('S/R: confirmed levels keep the existing bands; invalidated or missing levels create no level', () => {
  const bands = chart => (chart.match(/<rect x="0" y="[^"]+" width="\d+" height="26" fill="#(527dea|ef5350)" opacity=".34"><\/rect>/g) ?? []);
  const chart = svg(overlayOutput());
  assert.deepEqual(bands(chart).map(b => b.match(/fill="#(\w+)"/)[1]), ['527dea', 'ef5350']);
  assert.equal(bands(svg(overlayOutput(), { ...model.DEFAULT_LAYERS, sr: false })).length, 0);
  const none = overlayOutput(); delete none.structure;
  assert.equal(bands(svg(none)).length, 0);
  const invalidOnly = overlayOutput(); invalidOnly.structure.levels = invalidOnly.structure.levels.filter(l => l.status !== 'confirmed');
  assert.equal(bands(svg(invalidOnly)).length, 0);
});

test('patterns: marker from canonical PatternEvidence via identity lookup, with canonical naming only', () => {
  const [marker] = model.patternOverlays(overlayOutput().signals.decision.patterns, overlayOutput().transitions);
  assert.deepEqual([marker.key, marker.columns, marker.priceLow, marker.priceHigh], ['pattern:pattern_double_bottom:fixture-t4', [4], 102, 108]);
  const group = overlayGroup(svg(overlayOutput()));
  assert.equal(count(group, /data-overlay="pattern"/g), 1);
  assert.match(group, /data-pattern-type="double_bottom"/);
  assert.match(group, />double bottom · bullish<\/text>/);
  assert.doesNotMatch(group, /breakout|breakdown/i);
  const top = overlayOutput();
  top.signals.decision.patterns[0] = { ...top.signals.decision.patterns[0], pattern_type: 'double_top', direction: 'bearish' };
  assert.match(overlayGroup(svg(top)), />double top · bearish<\/text>/);
  assert.doesNotMatch(overlayGroup(svg(top)), /Double Top Breakout/i);
});

test('patterns: unresolved identity references render no marker (no guessing)', () => {
  const output = overlayOutput();
  const base = output.signals.decision.patterns[0];
  for (const source_data_reference of ['missing-transition', 'fixture-t4|missing-transition', '']) {
    assert.deepEqual(model.patternOverlays([{ ...base, source_data_reference }], output.transitions), []);
  }
  const noKeys = output.transitions.map(t => ({ ...t, identity_key: undefined }));
  assert.deepEqual(model.patternOverlays([base], noKeys), []);
  const empty = overlayOutput(); empty.signals.decision.patterns = [];
  assert.doesNotMatch(svg(empty), /data-overlay="pattern"/);
  assert.doesNotMatch(svg(overlayOutput(), { ...model.DEFAULT_LAYERS, patterns: false }), /data-overlay="pattern"/);
});

test('patterns: multi-pivot references span every resolved column; duplicates collapse to one stable key', () => {
  const output = overlayOutput();
  const triangle = { ...output.signals.decision.patterns[0], pattern_type: 'triangle_breakout', evidence_code: 'pattern_triangle_breakout',
    source_data_reference: 'fixture-t2|fixture-t3|fixture-t4|fixture-t5|fixture-t6|fixture-t7' };
  const markers = model.patternOverlays([triangle, triangle, output.signals.decision.patterns[0]], output.transitions);
  assert.equal(markers.length, 2);
  assert.deepEqual(markers[0].columns, [2, 3, 4, 5, 6, 7]);
  assert.equal(new Set(markers.map(m => m.key)).size, 2);
});

test('trendline break: marker at the recorded break transition only, labelled "Trendline break"', () => {
  const [mark] = model.breakOverlays(overlayOutput().trendline, overlayOutput().transitions);
  assert.deepEqual([mark.column, mark.price, mark.transitionId], [6, 103, 'fixture-t6']);
  const group = overlayGroup(svg(overlayOutput()));
  const diamond = group.match(/<g data-overlay="trendline-break"[\s\S]*?<path d="M ([\d.-]+) ([\d.-]+)/);
  assert.equal(Number(diamond[1]), 90 + 5 * 30);
  const levelY = svg(overlayOutput()).match(/<rect x="0" y="([\d.-]+)" width="\d+" height="26" fill="#527dea"/)[1];
  // Break box centre = cy(103) = y(103) - 13; the support band at 104 starts at y(104) - 13 = y(103) - 39.
  assert.equal(Number(diamond[2]) + 9, Number(levelY) + 26);
  assert.equal(count(group, /data-overlay="trendline-break"/g), 1);
  assert.match(group, />Trendline break<\/text>/);
  assert.doesNotMatch(group, /▲|▼|Breakout|Breakdown/);
  const missing = overlayOutput(); missing.trendline.active_bullish.break_transition_id = 'unknown';
  assert.deepEqual(model.breakOverlays(missing.trendline, missing.transitions), []);
  const mismatch = overlayOutput(); mismatch.trendline.active_bullish.break_column = 5;
  assert.deepEqual(model.breakOverlays(mismatch.trendline, mismatch.transitions), []);
  const unbroken = overlayOutput(); Object.assign(unbroken.trendline.active_bullish, { state: 'active', break_column: null, break_transition_id: null });
  assert.doesNotMatch(svg(unbroken), /trendline-break/);
});

test('invalidated structure levels are never rendered as breakout/breakdown markers', () => {
  const output = overlayOutput();
  output.trendline = null;
  output.signals.decision.patterns = [];
  output.structure.levels = [{ side: 'resistance', price: '105', status: 'invalidated' }, { side: 'support', price: '103', status: 'invalidated' }];
  const chart = svg(output);
  assert.doesNotMatch(chart, /data-chart-overlays|Breakout|Breakdown/);
});

test('popup: shows the backend evidence of the inspected item and nothing when that evidence is gone', () => {
  const current = model.buildOverlayModel(overlayOutput());
  const popup = (m, key, layers = model.DEFAULT_LAYERS) => renderToStaticMarkup(createElement(overlays.OverlayPopup,
    { model: m, layers, inspection: { key, left: 10, top: 10, above: false }, onClose() {} }));
  const pattern = popup(current, 'pattern:pattern_double_bottom:fixture-t4');
  for (const text of ['Pattern: double bottom', 'double_bottom', 'bullish', 'confirmation', '102 – 108', 'pattern_double_bottom', 'p8a-pattern-v1', 'interim pattern visualization source'])
    assert.ok(pattern.includes(text), text);
  const line = popup(current, 'trendline:fixture-line-bull-1');
  for (const text of ['Trendline: bullish support', 'retesting', 'column 2 · 102', 'column 4 · 104', '107 at latest column 7', 'break@col6', 'fixture-config-v1'])
    assert.ok(line.includes(text), text);
  assert.match(popup(current, 'break:fixture-line-bull-1:fixture-t6'), /Trendline break[\s\S]*Break column<\/dt><dd>6/);
  assert.doesNotMatch(pattern + line, /\b(BUY|SELL)\b/);
  const next = overlayOutput(); next.signals.decision.patterns = [];
  assert.equal(popup(model.buildOverlayModel(next), 'pattern:pattern_double_bottom:fixture-t4'), '');
  assert.equal(popup(current, 'trendline:fixture-line-bull-1', { ...model.DEFAULT_LAYERS, trendline: false }), '');
});

test('realtime: successive snapshots update overlays with stable keys and no duplicates', () => {
  const first = overlayOutput();
  const next = overlayOutput();
  next.columns.push({ column_id: 8, direction: 'O', open_price: '107', close_price: '105' });
  next.transitions.push({ column_id: 8, direction: 'O', from_price: '107', to_price: '105', effective_box_size: '1', boxes_moved: 2, identity_key: 'fixture-t8' });
  next.trendline.active_bullish.projected_price_at_latest_column = '108';
  const a = model.buildOverlayModel(first), b = model.buildOverlayModel(next);
  assert.deepEqual(model.overlayKeys(a), model.overlayKeys(b));
  assert.equal(new Set(model.overlayKeys(b)).size, model.overlayKeys(b).length);
  assert.deepEqual(b.trendlines[0].to, { column: 8, price: 108 });
  const group = overlayGroup(svg(next));
  assert.equal(count(group, /data-overlay="trendline"/g), 1);
  assert.equal(count(group, /data-overlay="pattern"/g), 1);
  assert.equal(count(group, /data-overlay="trendline-break"/g), 1);
});

test('realtime: overlays open no connection, fetch nothing and poll nothing; page keeps one WebSocket', () => {
  for (const source of [overlaySource, modelSource]) {
    assert.doesNotMatch(source, /WebSocket|fetch\s*\(|EventSource|setInterval|setTimeout|XMLHttpRequest/);
  }
  assert.equal(count(page, /new WebSocket\(/g), 1);
});

test('responsive: popup becomes a full-width sheet on narrow screens and layer controls wrap', () => {
  assert.match(css, /@media \(max-width: 540px\) \{[^}]*\.overlay-popup/);
  assert.match(css, /\.chart-layers \{[^}]*flex-wrap: wrap/);
});
