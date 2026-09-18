export type Column = { column_id: number; direction: string; open_price: string; close_price: string };
export type Transition = {
  column_id: number; direction: string; from_price: string; to_price: string;
  boxes_moved: number; effective_box_size: string;
};
export type Cell = { columnId: number; direction: string; price: string; upperPrice: string };

// Expand recorded boxes only: no reversal/threshold decisions or inferred box size.
// Decimal integer arithmetic keeps tooltip prices exact (including trailing zeros).
export function recordedCells(columns: Column[], transitions: Transition[], limit = 3000): Cell[] {
  const ids = new Set(columns.map((c) => c.column_id));
  const cells: Cell[] = [];
  for (let t = transitions.length - 1; t >= 0 && cells.length < limit; t--) {
    const transition = transitions[t];
    if (!ids.has(transition.column_id)) continue;
    const { from_price: from, to_price: to, effective_box_size: size, boxes_moved: count } = transition;
    if (![from, to, size].every((v) => /^-?\d+(\.\d+)?$/.test(v)) ||
        !Number.isSafeInteger(count) || count < 1 || !["X", "O"].includes(transition.direction)) continue;
    const precision = Math.max(...[from, to, size].map((v) => v.split(".")[1]?.length ?? 0));
    const integer = (v: string) => {
      const [whole, fraction = ""] = v.split(".");
      return BigInt(whole + fraction.padEnd(precision, "0"));
    };
    const format = (value: bigint) => {
      const negative = value < BigInt(0);
      const digits = (negative ? -value : value).toString().padStart(precision + 1, "0");
      return (negative ? "-" : "") + (precision ? `${digits.slice(0, -precision)}.${digits.slice(-precision)}` : digits);
    };
    const step = integer(size), start = integer(from), end = integer(to);
    const sign = BigInt(transition.direction === "X" ? 1 : -1);
    if (step <= BigInt(0) || start + sign * step * BigInt(count) !== end) continue;
    for (let i = count; i > 0 && cells.length < limit; i--) {
      const price = start + sign * step * BigInt(i);
      cells.push({ columnId: transition.column_id, direction: transition.direction,
        price: format(price), upperPrice: format(price + step) });
    }
  }
  cells.reverse();
  // Adjacent recorded price boundaries define each column's visual grid.
  // An adaptive X extension can shrink the next interval: using the previous
  // box size there would overlap cells. Only the top cell needs its own size.
  const byColumn = new Map<number, Cell[]>();
  for (const cell of cells) {
    const group = byColumn.get(cell.columnId) ?? [];
    group.push(cell);
    byColumn.set(cell.columnId, group);
  }
  for (const group of byColumn.values()) {
    group.sort((a, b) => Number(a.price) - Number(b.price));
    for (let i = 0; i < group.length - 1; i++) group[i].upperPrice = group[i + 1].price;
  }
  return cells;
}

export function priceScale(cells: Cell[], levels: string[]) {
  const prices = [...cells.flatMap((c) => [Number(c.price), Number(c.upperPrice)]), ...levels.map(Number)].filter(Number.isFinite);
  const min = Math.min(...prices), max = Math.max(...prices);
  return (price: number) => 240 - ((price - min) / (max - min || 1)) * 210;
}

export function cellGeometry(cell: Cell, y: (price: number) => number, width: number) {
  const bottom = y(Number(cell.price)), top = y(Number(cell.upperPrice));
  return { top, bottom, center: (top + bottom) / 2, radius: Math.min(Math.abs(bottom - top), width) * 0.3 };
}
