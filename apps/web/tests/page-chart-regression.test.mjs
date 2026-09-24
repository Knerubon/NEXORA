import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { model, renderPageChart, svgOf } from './overlay-harness.mjs';
import { overlayOutput } from './overlay-fixture.mjs';

const source = readFileSync(new URL('../app/page.tsx', import.meta.url), 'utf8');
const baseline = execFileSync('git', ['show', '8dd31a2:apps/web/app/page.tsx'], { encoding: 'utf8' });
const chart = (src, output, props = {}) => svgOf(renderPageChart(src, { output, ...props }));
const overlayLayer = /<g data-chart-overlays="">[\s\S]*<\/g><\/svg>$/;

test('live page P&F SVG remains identical to pre-UI HEAD for confirmed, adaptive and empty data', () => {
  for (const output of [{}, {columns:[{column_id:1,direction:'X'},{column_id:2,direction:'O'}],transitions:[{column_id:1,direction:'X',to_price:'103',effective_box_size:'1',boxes_moved:3},{column_id:2,direction:'O',to_price:'102.5',effective_box_size:'0.25',boxes_moved:2}],structure:{levels:[{status:'confirmed',side:'support',price:'100'}]}}]) {
    assert.equal(chart(source,output),chart(baseline,output));
  }
});

test('overlay layers OFF: chart with overlay evidence is identical to the pre-feature rendering', () => {
  const output = overlayOutput();
  assert.equal(chart(source, output, { initialLayers: { trendline: false, sr: true, patterns: false } }), chart(baseline, output));
});

test('all layers OFF: only the existing S/R bands are removed, nothing else changes', () => {
  const output = overlayOutput();
  const withoutLevels = { ...output, structure: { ...output.structure, levels: [] } };
  assert.equal(chart(source, output, { initialLayers: model.LAYERS_OFF }), chart(baseline, withoutLevels));
});

test('layers ON: the only addition is one overlay group appended to the unchanged chart', () => {
  const output = overlayOutput();
  const svg = chart(source, output);
  assert.match(svg, overlayLayer);
  assert.equal(svg.match(/data-chart-overlays/g).length, 1);
  assert.equal(svg.replace(overlayLayer, '</svg>'), chart(baseline, output));
});
