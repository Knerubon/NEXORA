import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, readdirSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { drawingModel as m, drawingSource, drawings, renderPageChart, svgOf } from './overlay-harness.mjs';
import { overlayOutput } from './overlay-fixture.mjs';

const page = readFileSync(new URL('../app/page.tsx', import.meta.url), 'utf8');
const modelSource = readFileSync(new URL('../app/manual-drawing-model.ts', import.meta.url), 'utf8');
const h = (price, id = `h${price}`) => ({ id, kind: 'horizontal', price, created_at: '2026-09-25T00:00:00.000Z' });
const t = (a, b, id = 't1') => ({ id, kind: 'trend', a, b, created_at: '2026-09-25T00:00:00.000Z' });
const doc = (items, visible = true) => ({ version: 1, visible, drawings: items });

// StructureChart geometry (page.tsx): y(p) = 26 + (top - p) / rowStep * 26; glyph centre = y(p) - 13.
const grid = { top: 110, rowStep: 1, visibleColumnIds: [5, 6, 7, 8, 9, 10] };
const y = (price, g = grid) => 26 + (g.top - price) / g.rowStep * 26;
const centre = (column, price, g = grid) => [90 + (column - g.visibleColumnIds[0]) * 30, y(price, g) - 13];

test('parse: missing, malformed or unknown-version storage yields an empty visible document', () => {
  for (const raw of [null, '', '{', '[]', 'null', '{"version":2,"drawings":[]}', '{"version":1}']) {
    assert.deepEqual(m.parseDrawings(raw), m.EMPTY_DOC, String(raw));
  }
  assert.deepEqual(m.EMPTY_DOC, { version: 1, visible: true, drawings: [] });
});

test('parse: invalid, duplicate and degenerate drawings are dropped; valid ones and visibility survive', () => {
  const valid = [h(101), t({ column: 5, price: 100 }, { column: 9, price: 104 })];
  const raw = JSON.stringify(doc([
    ...valid,
    h(102, 'h101'), { ...h(103), id: '' }, { ...h(104), price: 'NaN' }, { ...h(105), kind: 'ray' },
    t({ column: 5, price: 100 }, { column: 5, price: 100 }, 'same'), t({ column: 5.5, price: 100 }, { column: 6, price: 101 }, 'frac'),
    t({ column: 5, price: 100 }, null, 'nob'), null, 7,
  ], false));
  assert.deepEqual(m.parseDrawings(raw), doc(valid, false));
  const many = JSON.stringify(doc(Array.from({ length: m.MAX_DRAWINGS + 5 }, (_, i) => h(i))));
  assert.equal(m.parseDrawings(many).drawings.length, m.MAX_DRAWINGS);
});

test('edits are immutable; add enforces the cap and unique ids', () => {
  const start = Object.freeze(doc(Object.freeze([h(100)])));
  const added = m.addDrawing(start, h(101));
  assert.deepEqual(added.drawings.map((d) => d.id), ['h100', 'h101']);
  assert.equal(m.addDrawing(start, h(100)), start, 'duplicate id is ignored');
  const full = doc(Array.from({ length: m.MAX_DRAWINGS }, (_, i) => h(i)));
  assert.equal(m.addDrawing(full, h(999)), full);
  assert.deepEqual(m.removeDrawing(added, 'h100').drawings, [h(101)]);
  assert.deepEqual(m.clearDrawings(added), doc([]));
  assert.deepEqual(m.setVisible(added, false), { ...added, visible: false });
  assert.deepEqual(start.drawings, [h(100)]);
});

test('snap: a click anywhere inside a P&F cell returns that cell (column_id, price)', () => {
  for (const column of [5, 7, 10]) for (const price of [100, 104, 110]) {
    const [cx, cy] = centre(column, price);
    for (const [dx, dy] of [[0, 0], [-14, -12], [14, 12], [0, -12.9], [0, 12.9]]) {
      assert.deepEqual(m.snapPoint(grid, cx + dx, cy + dy), { column, price }, `${column}/${price} ${dx},${dy}`);
    }
  }
});

test('snap: empty slots right of the latest column map to the next column ids; axis and empty chart give null', () => {
  const [cx, cy] = centre(10, 100);
  assert.deepEqual(m.snapPoint(grid, cx + 60, cy), { column: 12, price: 100 });
  assert.deepEqual(m.snapPoint(grid, 76, cy), { column: 5, price: 100 });
  assert.equal(m.snapPoint(grid, 60, cy), null, 'price axis');
  assert.equal(m.snapPoint({ ...grid, visibleColumnIds: [] }, cx, cy), null);
  assert.equal(m.snapPoint({ ...grid, rowStep: 0 }, cx, cy), null);
  assert.equal(m.snapPoint(grid, NaN, cy), null);
});

test('snap: fractional box sizes produce clean prices without floating-point noise', () => {
  const fine = { top: 2345.7, rowStep: 0.1, visibleColumnIds: [1, 2, 3] };
  for (const price of [2345.6, 2340.1, 2333.3]) {
    const [cx, cy] = centre(2, price, fine);
    assert.equal(m.snapPoint(fine, cx, cy).price, price);
  }
});

test('store: persists per symbol, survives a reload and ignores other symbols', () => {
  const data = new Map();
  const storage = { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) };
  const store = m.createDrawingStore(() => storage);
  const key = m.storageKey('XAUUSD');
  let calls = 0;
  const off = store.subscribe(() => calls++);
  assert.equal(store.get(key), store.get(key), 'stable snapshot');
  store.set(key, m.addDrawing(store.get(key), h(2400)));
  assert.equal(calls, 1);
  assert.equal(store.saved(key), true);
  assert.deepEqual([...data.keys()], ['nexora:manual-drawings:v1:XAUUSD']);
  assert.deepEqual(m.createDrawingStore(() => storage).get(key).drawings, [h(2400)]);
  assert.deepEqual(store.get(m.storageKey('EURUSD')), m.EMPTY_DOC);
  data.set(key, JSON.stringify(doc([h(1)])));
  store.reload(key);
  assert.deepEqual(store.get(key).drawings, [h(1)], 'another tab changed the key');
  off();
});

test('store: unavailable or throwing storage keeps drawings in memory and reports not saved', () => {
  for (const getStorage of [() => null, () => { throw new Error('denied'); },
    () => ({ getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); } })]) {
    const store = m.createDrawingStore(getStorage);
    const key = m.storageKey('XAUUSD');
    assert.deepEqual(store.get(key), m.EMPTY_DOC);
    store.set(key, m.addDrawing(store.get(key), h(2400)));
    assert.deepEqual(store.get(key).drawings, [h(2400)]);
    assert.equal(store.saved(key), false);
  }
});

const controller = (over = {}) => ({ key: 'k', doc: doc([]), tool: 'none', pending: null, hover: null, selection: null, enabled: true, saved: true,
  choose() {}, cancel() {}, place() {}, hoverAt() {}, select() {}, close() {}, toggleVisible() {}, remove() {}, clear() {}, ...over });
const layer = (c, g = grid) => renderToStaticMarkup(createElement('svg', null,
  createElement(drawings.DrawingLayer, { drawings: c, grid: g, y: (p) => y(p, g), width: 1400, height: 600 })));
const num = (markup, name) => Number(markup.match(new RegExp(` ${name}="(-?[\\d.]+)"`))[1]);

test('layer: renders nothing without drawings, when hidden, or without columns', () => {
  const empty = '<svg></svg>';
  assert.equal(layer(controller()), empty);
  assert.equal(layer(controller({ doc: doc([h(100)], false) })), empty);
  assert.equal(layer(controller({ doc: doc([h(100)]) }), { ...grid, visibleColumnIds: [] }), empty);
});

test('layer: horizontal and trend lines are drawn through the snapped cell centres', () => {
  const svg = layer(controller({ doc: doc([h(104), t({ column: 6, price: 101 }, { column: 9, price: 107 })]) }));
  const horizontal = svg.match(/<g data-drawing="horizontal"[\s\S]*?<\/g>/)[0];
  assert.equal(num(horizontal, 'y1'), y(104) - 13);
  assert.match(horizontal, /Manual · 104/);
  const trend = svg.match(/<g data-drawing="trend"[\s\S]*?<\/g>/)[0];
  assert.deepEqual(['x1', 'y1', 'x2', 'y2'].map((a) => num(trend, a)), [...centre(6, 101), ...centre(9, 107)]);
  assert.match(svg, /clip-path="url\(#pnf-drawing-clip\)"/);
  assert.doesNotMatch(svg, /data-drawing-capture/, 'no capture surface outside draw mode');
});

test('layer: an active tool adds the capture surface and the pending/preview marks', () => {
  const svg = layer(controller({ tool: 'trend', pending: { column: 6, price: 101 }, hover: { column: 8, price: 103 } }));
  assert.match(svg, /data-drawing-capture=""/);
  assert.match(svg, /<g data-drawing-preview=""[\s\S]*stroke-dasharray="5 4"/);
});

test('popup: labels the drawing as a browser-only user annotation with a delete action', () => {
  const item = t({ column: 6, price: 101 }, { column: 9, price: 107 });
  const html = renderToStaticMarkup(createElement(drawings.DrawingPopup, { drawings: controller({ doc: doc([item]), selection: { key: 't1', left: 10, top: 10, above: false } }) }));
  assert.match(html, /aria-label="Manual drawing"/);
  assert.match(html, /column 6 · 101/);
  assert.match(html, /Delete drawing/);
  assert.match(html, /Not system evidence; never used by Entry Readiness, signals or backtests/);
  assert.equal(renderToStaticMarkup(createElement(drawings.DrawingPopup, { drawings: controller({ doc: doc([item]), selection: { key: 'gone', left: 0, top: 0, above: false } }) })), '');
});

test('page: toolbar shows the drawing toggle and tools; the P&F SVG gains nothing without drawings', () => {
  const html = renderPageChart(page, { output: overlayOutput() });
  assert.match(html, /role="group" aria-label="Manual drawing"/);
  assert.match(html, /aria-pressed="true"[^>]*>Drawings<\/button>/);
  assert.match(html, />H-line<\/button>/);
  assert.match(html, />Trend line<\/button>/);
  assert.doesNotMatch(svgOf(html), /data-manual-drawings/);
  const waiting = renderPageChart(page, { output: {} });
  assert.match(waiting, /<button aria-pressed="false" disabled="">H-line<\/button>/, 'no symbol, no drawing');
});

test('boundary: drawings never leave the browser and no decision/evidence module reads them', () => {
  for (const source of [modelSource, drawingSource]) {
    assert.doesNotMatch(source, /fetch\(|WebSocket|\/api\/|XMLHttpRequest|sendBeacon/);
  }
  const app = new URL('../app/', import.meta.url);
  const importers = readdirSync(app).filter((f) => /manual-drawing/.test(readFileSync(new URL(f, app), 'utf8')) && !f.startsWith('manual-drawing'));
  assert.deepEqual(importers, ['page.tsx']);
  assert.doesNotMatch(readFileSync(new URL('../app/signal-intelligence.tsx', import.meta.url), 'utf8'), /drawing/i);
});
