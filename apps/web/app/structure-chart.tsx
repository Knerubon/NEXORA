import { cellGeometry, priceScale, recordedCells } from "./pnf-layout";
import type { Column, Transition } from "./pnf-layout";

export function StructureChart({ output }: { output: {
  columns?: Column[]; transitions?: Transition[];
  quote?: { bid?: string; ask?: string } | null;
  structure?: { levels: { side: string; price: string; status: string }[] };
} }) {
  const columns = (output.columns ?? []).slice(-60);
  if (!columns.length) return <p>No calculated P&amp;F columns yet. Configure research and ingest recorded data or observe the local feed.</p>;
  const cells = recordedCells(columns, output.transitions ?? []);
  if (!cells.length) return <p>P&amp;F cell geometry unavailable. Recorded box transitions are required. Latest column: {columns.at(-1)?.open_price} → {columns.at(-1)?.close_price}.</p>;
  const levels = (output.structure?.levels ?? []).filter((l) => l.status === "confirmed").slice(-12);
  const y = priceScale(cells, levels.map((l) => l.price));
  const width = 760 / columns.length;
  const latestLivePrice = output.quote && Number.isFinite(Number(output.quote.bid)) && Number.isFinite(Number(output.quote.ask))
    ? (Number(output.quote.bid) + Number(output.quote.ask)) / 2
    : null;
  return <div className="pnf-chart" tabIndex={0} role="region" aria-label="P&F chart; scroll horizontally on small screens"><svg viewBox="0 0 900 280" role="img" aria-label="Calculated P&F cells and confirmed support/resistance">
    {cells.map((cell, i) => {
      const left = 35 + columns.findIndex((c) => c.column_id === cell.columnId) * width;
      const x = left + width / 2;
      const { top, bottom, center, radius } = cellGeometry(cell, y, width);
      const color = cell.direction === "X" ? "#a5e5d0" : "#edac9e";
      return <g key={`${cell.columnId}-${i}`} data-pnf-cell="" data-price={cell.price}>
        <title>{`${cell.direction} · column ${cell.columnId} · P&F price ${cell.price} · cell ${cell.price}–${cell.upperPrice}`}</title>
        <rect x={left} y={top} width={width} height={bottom-top} fill="#131e25" stroke="#344650" strokeWidth="0.5" />
        {cell.direction === "X"
          ? <path d={`M ${x-radius} ${center-radius} L ${x+radius} ${center+radius} M ${x-radius} ${center+radius} L ${x+radius} ${center-radius}`} fill="none" stroke={color} strokeWidth={Math.min(1.5, radius / 3)} />
          : <circle cx={x} cy={center} r={radius} fill="none" stroke={color} strokeWidth={Math.min(1.5, radius / 3)} />}
      </g>;
    })}
    {levels.map((l, i) => <g key={`${l.side}-${i}`}><line x1="35" x2="795" y1={y(Number(l.price))} y2={y(Number(l.price))} stroke="#7998ac" strokeDasharray="4 6" />
      <text x="800" y={y(Number(l.price))} fill="#afc3d0" fontSize="11">{l.price}</text></g>)}
    {latestLivePrice !== null && <text x="35" y="255" fill="#dff7ef" fontSize="11">Latest live quote: {latestLivePrice.toFixed(2)} ({output.quote?.bid} / {output.quote?.ask})</text>}
    <text x="35" y="272" fill="#91a5b0" fontSize="11">Last {columns.length} columns · up to 3000 recorded boxes · dashed lines: confirmed S/R</text>
  </svg></div>;
}
