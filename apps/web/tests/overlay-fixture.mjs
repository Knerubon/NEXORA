// Synthetic research output for overlay tests and visual validation.
// Shaped like research.output from /state; not market data and not a trading result.
const moves = [
  [1, 'X', '100', '106'], [2, 'O', '106', '102'], [3, 'X', '102', '108'],
  [4, 'O', '108', '104'], [5, 'X', '104', '110'], [6, 'O', '110', '103'], [7, 'X', '103', '107'],
];
export const transitions = moves.map(([column_id, direction, from_price, to_price]) => ({
  column_id, direction, from_price, to_price, effective_box_size: '1',
  boxes_moved: Math.abs(Number(to_price) - Number(from_price)), identity_key: `fixture-t${column_id}`,
}));
export const columns = moves.map(([column_id, direction, open_price, close_price]) => ({ column_id, direction, open_price, close_price }));

const anchor = (column_id, price) => ({ pivot_kind: 'low', price, column_id, source_pivot_id: `fixture-t${column_id}`, event_time: '2026-01-01T00:00:00+00:00' });
export const bullishLine = {
  line_id: 'fixture-line-bull-1', kind: 'bullish_support', state: 'retesting',
  anchor_a: anchor(2, '102'), anchor_b: anchor(4, '104'),
  slope_price_per_column: '1', projected_price_at_latest_column: '107',
  touch_columns: [], break_column: 6, break_transition_id: 'fixture-t6',
  retest_column: 7, retest_resolved_column: null, retest_resolved_sequence: null, retest_outcome: 'none',
  replaced_by_line_id: null, age_columns: 3, config_version: 'fixture-config-v1',
  evidence: ['formed@col4', 'break@col6', 'retest_entry@col7'],
};
export const doubleBottom = {
  pattern_type: 'double_bottom', direction: 'bullish', relation: 'confirmation',
  start_time: '2026-01-01T00:00:00+00:00', confirmation_time: '2026-01-01T00:05:00+00:00',
  price_low: '102', price_high: '108', evidence_code: 'pattern_double_bottom',
  source_data_reference: 'fixture-t4', algorithm_version: 'p8a-pattern-v1',
};

export const overlayOutput = () => ({
  columns: structuredClone(columns), transitions: structuredClone(transitions),
  event: { symbol: 'FIXTURE', price: '107' },
  config_version: 'fixture-config-v1',
  structure: { pivots: [], levels: [
    { side: 'support', price: '104', status: 'confirmed' },
    { side: 'resistance', price: '110', status: 'confirmed' },
    { side: 'support', price: '99', status: 'invalidated' },
  ] },
  trendline: { schema_version: 1, symbol: 'FIXTURE', sequence: 7, active_bullish: structuredClone(bullishLine), active_bearish: null, history: [] },
  signals: { history: [], decision: { action: 'WAIT', patterns: [structuredClone(doubleBottom)] } },
});
