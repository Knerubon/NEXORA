import assert from 'node:assert/strict';
import test from 'node:test';
import { renderChart } from './render-chart.mjs';

test('actual SVG renders centered X/O shapes with original prices and confirmed S/R labels', () => {
  const html = renderChart({
    columns: [{ column_id: 1 }, { column_id: 2 }],
    transitions: [
      { column_id: 1, direction: 'X', from_price: '100', to_price: '103', boxes_moved: 3, effective_box_size: '1' },
      { column_id: 2, direction: 'O', from_price: '103', to_price: '100', boxes_moved: 3, effective_box_size: '1' },
    ],
    structure: { levels: [{ side: 'support', price: '100', status: 'confirmed' }] },
  });
  assert.equal((html.match(/data-pnf-cell/g) ?? []).length, 6);
  assert.equal((html.match(/<path /g) ?? []).length, 3);
  assert.equal((html.match(/<circle /g) ?? []).length, 3);
  assert.match(html, /P&amp;F price 103/);
  assert.match(html, /P&amp;F price 100/);
  assert.match(html, /stroke-dasharray="4 6"/);
});

test('empty and missing transition states are explicit', () => {
  assert.match(renderChart({}), /No calculated/);
  assert.match(renderChart({ columns: [{ column_id: 1, open_price: '100', close_price: '103' }] }), /geometry unavailable/);
});
