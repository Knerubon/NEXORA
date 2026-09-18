import assert from 'node:assert/strict';
import test from 'node:test';
import { recordedCells, priceScale, cellGeometry } from '../app/pnf-layout.ts';

const columns = [
  { column_id: 1, direction: 'X', open_price: '100.00', close_price: '103.00' },
  { column_id: 2, direction: 'O', open_price: '102.00', close_price: '100.00' },
];
const transitions = [
  { column_id: 1, direction: 'X', from_price: '100.00', to_price: '102.00', boxes_moved: 2, effective_box_size: '1.00' },
  { column_id: 1, direction: 'X', from_price: '102.00', to_price: '103.00', boxes_moved: 1, effective_box_size: '1.00' },
  { column_id: 2, direction: 'O', from_price: '103.00', to_price: '100.00', boxes_moved: 3, effective_box_size: '1.00' },
];

test('recorded seed/extension/reversal boxes retain exact prices and source state', () => {
  const before = JSON.stringify({ columns, transitions });
  const cells = recordedCells(columns, transitions);
  assert.deepEqual(cells.map(c => c.price), ['101.00', '102.00', '103.00', '102.00', '101.00', '100.00']);
  assert.deepEqual(cells.map(c => c.direction), ['X', 'X', 'X', 'O', 'O', 'O']);
  assert.equal(JSON.stringify({ columns, transitions }), before);
});

test('every X/O fits strictly inside its cell across rows, columns, resize and zoom', () => {
  const cells = recordedCells(columns, transitions);
  const y = priceScale(cells, []);
  for (const scale of [390 / 900, 1, 1.5, 2]) {
    for (const cell of cells) {
      const g = cellGeometry(cell, p => y(p) * scale, 380 * scale);
      assert.ok(Math.abs(g.center - (g.top + g.bottom) / 2) < 1e-9);
      assert.ok(g.center - g.radius > g.top);
      assert.ok(g.center + g.radius < g.bottom);
      assert.ok(g.top >= 30 * scale - 1e-9 && g.bottom <= 240 * scale + 1e-9);
    }
  }
  assert.equal(cellGeometry(cells[0], y, 380).center, 161.25);
  assert.equal(cellGeometry(cells[3], y, 380).center, 108.75);
});

test('adaptive sizes use each recorded transition, never average column spans', () => {
  const cells = recordedCells(columns, [...transitions, {
    column_id: 2, direction: 'O', from_price: '100.00', to_price: '99.50', boxes_moved: 2, effective_box_size: '0.25',
  }]);
  assert.deepEqual(cells.slice(-2).map(c => [c.price, c.upperPrice]), [['99.75', '100.00'], ['99.50', '99.75']]);
});

test('single box has a finite center; distant support/resistance stays on the price scale', () => {
  const cells = recordedCells(columns, [transitions[1]]);
  assert.equal(cellGeometry(cells[0], priceScale(cells, []), 760).center, 135);
  const y = priceScale(cells, ['90.00', '110.00']);
  assert.equal(y(90), 240);
  assert.equal(y(110), 30);
  assert.equal(cellGeometry(cells[0], y, 760).center, (y(103) + y(104)) / 2);
});

test('shrinking adaptive X intervals meet at recorded boundaries without overlapping', () => {
  const cells = recordedCells(columns, [...transitions, {
    column_id: 1, direction: 'X', from_price: '103.00', to_price: '103.50', boxes_moved: 2, effective_box_size: '0.25',
  }]).filter(c => c.columnId === 1);
  assert.deepEqual(cells.map(c => [c.price, c.upperPrice]), [
    ['101.00', '102.00'], ['102.00', '103.00'], ['103.00', '103.25'],
    ['103.25', '103.50'], ['103.50', '103.75'],
  ]);
});

test('missing/invalid geometry is not invented; bounded history retains newest boxes', () => {
  assert.deepEqual(recordedCells(columns, []), []);
  assert.deepEqual(recordedCells(columns, [{ ...transitions[0], effective_box_size: '0' }]), []);
  assert.deepEqual(recordedCells(columns, [{ ...transitions[0], to_price: 'NaN' }]), []);
  assert.deepEqual(recordedCells(columns.slice(1), transitions).map(c => c.price), ['102.00', '101.00', '100.00']);
  assert.deepEqual(recordedCells(columns, transitions, 2).map(c => c.price), ['101.00', '100.00']);
});
