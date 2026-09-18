"use client";

import { useCallback, useEffect, useState } from "react";

type Column = { column_id: number; direction: string; open_price: string; close_price: string };
type Signal = { signal_id: string; side: string; decision_time: string; reasons: string[]; source_refs: string[]; status: string };
type Output = {
  columns?: Column[];
  event?: { symbol: string; price: string };
  config_version?: string;
  transitions?: { column_id: number; direction: string; to_price: string; effective_box_size: string; boxes_moved: number }[];
  matrix?: { alignment: string; resolutions: { name: string; direction: string; status: string }[] };
  structure?: { levels: { side: string; price: string; status: string }[] };
  regime?: { state: { label: string; reason: string } };
  signals?: { history: Signal[] };
};
type State = {
  sequence: number; research_mode: string; storage_backend: string;
  quote: { stream_id: string; status: string; quote: { symbol: string; bid: string; ask: string; event_time: string; raw_event_time?: string; time_offset_seconds?: number } | null };
  quality: { status: string; completeness: string; counters: { observed: number; gaps: number; reconnects: number } };
  research: { event_count: number; error: string | null; output: Output };
};
type Run = { run_id: string; mode: string; status: string; dataset_id: string; config_hash: string;
  metrics: { trade_count: number; win_rate: string; expectancy: string; profit_factor: string | null;
    max_drawdown: string; average_entry_delay_seconds: string }; notes: string[] };
type Paper = { status: string; accepted: number; rejected: number; fills: unknown[];
  state: { cash?: string; realized_pnl?: string }; ledger: { entry_id: string; detail: string; amount: string }[] };

const metric = (value: string | null) => value === null ? "Undefined" : Number(value).toLocaleString("en", { maximumFractionDigits: 4 });

const api = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

function StructureChart({ output, liveQuote }: { output: Output; liveQuote?: { bid: string; ask: string } | null }) {
  const columns = (output.columns ?? []).slice(-60);
  const ids = new Set(columns.map((c) => c.column_id));
  const transitions = (output.transitions ?? []).filter((t) => ids.has(t.column_id));
  const livePrice = liveQuote && Number.isFinite(Number(liveQuote.bid)) && Number.isFinite(Number(liveQuote.ask))
    ? (Number(liveQuote.bid) + Number(liveQuote.ask)) / 2
    : Number(output.event?.price ?? 0);
  // Render confirmed transition cells, including each transition's actual box size.
  // No trading rules or column directions are calculated in the browser.
  const cells = transitions.flatMap((t) => {
    const count = Math.min(t.boxes_moved, 500);
    const sign = t.direction === "X" ? 1 : -1;
    return Array.from({ length: count }, (_, i) => ({
      column: t.column_id, direction: t.direction,
      price: Number(t.to_price) - sign * Number(t.effective_box_size) * (count - 1 - i),
    }));
  }).slice(-2500);
  const step = Number(transitions.at(-1)?.effective_box_size ?? 1);
  const safeStep = Number.isFinite(step) && step > 0 ? step : 1;
  const latest = Number.isFinite(livePrice) ? livePrice : Number(output.event?.price ?? 0);
  const prices = cells.map((c) => c.price);
  const high = prices.length ? Math.max(...prices, latest) : 10;
  const low = prices.length ? Math.min(...prices, latest) : 0;
  // Align display grid to whole box multiples; retain recorded X/O prices.
  const anchor = 0;
  const min = anchor + Math.floor((low - anchor) / safeStep - 3) * safeStep;
  const max = anchor + Math.ceil((high - anchor) / safeStep + 3) * safeStep;
  const rows = Math.min(160, Math.max(20, Math.ceil((max - min) / safeStep)));
  const rowStep = Math.max(1, Math.ceil((max - min) / rows / safeStep)) * safeStep;
  const width = Math.max(1400, columns.length * 30 + 420), height = Math.max(600, rows * 26);
  const top = min + rows * rowStep;
  const y = (price: number) => 26 + (top - price) / rowStep * 26;
  // Center glyphs in their display-grid cell; data-price retains the exact engine price.
  const glyphY = (price: number) => {
    const cellBoundary = min + Math.floor((price - min) / rowStep + 1e-9) * rowStep;
    return y(cellBoundary) - 13;
  };
  const levels = (output.structure?.levels ?? []).filter((l) => l.status === "confirmed").slice(-12);
  return <div className="pnf-workspace">
    <div className="pnf-scroll" tabIndex={0} aria-label="Scrollable point and figure chart">
      <svg width={width} height={height + 52} role="img" aria-label="Point and figure: green X rising boxes, red O falling boxes">
        <defs><pattern id="pnf-grid" x="75" y="26" width="30" height="26" patternUnits="userSpaceOnUse"><path d="M 30 0 L 0 0 0 26" fill="none" stroke="#dfe3e7" strokeWidth="1" /></pattern></defs>
        <rect width="100%" height="100%" fill="white" /><rect x="75" y="13" width={width-75} height={height+26} fill="url(#pnf-grid)" />
        {levels.map((l,i) => <g key={i}><rect x="0" y={y(Number(l.price))-13} width={width} height="26" fill={l.side === "support" ? "#527dea" : "#ef5350"} opacity=".34" /><title>{l.side}: {l.price} · confirmed</title></g>)}
        {Array.from({length: rows+1},(_,i) => {const price = top-i*rowStep; return <g key={i}><line x1="0" x2={width} y1={y(price)} y2={y(price)} stroke="#e5e7eb" /><text x="8" y={y(price)+4} fontSize="11" fill="#707780">{price.toFixed(2)}</text></g>;})}
        {cells.map((c,i) => {const x = 90 + columns.findIndex((col) => col.column_id === c.column)*30; return <g key={i} data-pnf-glyph="" data-price={c.price}><title>{`Column ${c.column} · ${c.direction} · ${c.price.toFixed(2)}`}</title>{c.direction === "X" ? <path d={`M ${x-5} ${glyphY(c.price)-5} l 10 10 m 0 -10 l -10 10`} stroke="#09a77a" strokeWidth="2" fill="none" /> : <circle cx={x} cy={glyphY(c.price)} r="5" stroke="#f34b55" strokeWidth="2" fill="none" />}</g>;})}
        {prices.length > 0 && <g><line x1="75" x2={width} y1={y(latest)} y2={y(latest)} stroke="#64748b" strokeDasharray="4 5" /><title>{liveQuote ? `Latest live quote: ${latest.toFixed(2)}` : `Latest observed price: ${latest}`}</title></g>}
      </svg>
    </div>
    <aside className="matrix-float"><strong>Matrix: {output.event?.symbol ?? liveQuote ? (liveQuote ? "Live MT5 quote" : "Waiting for feed") : "Waiting for feed"}</strong><div className="matrix-mini">{(output.matrix?.resolutions ?? []).map((r) => <div key={r.name}><span>{r.name}</span><b className={r.direction === "X" ? "up" : "down"}>{r.direction === "X" || r.direction === "O" ? r.direction : "—"}</b><small>{r.status}</small></div>)}</div><small>Actual configured resolutions · observation</small></aside>
    <div className="chart-caption">{liveQuote ? `Box ${safeStep} | ${columns.length} columns | ${cells.length} confirmed boxes | Latest live quote ${latest.toFixed(2)} · ${liveQuote.bid} / ${liveQuote.ask}` : cells.length ? `${columns.length} columns · ${cells.length} confirmed boxes · latest box ${safeStep}` : "Waiting for the first confirmed box — no sample data"} · {output.config_version ?? "Unconfigured"}</div>
  </div>;
}

export default function Home() {
  const [state, setState] = useState<State | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [paper, setPaper] = useState<Paper | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [parameters, setParameters] = useState<string[]>([]);
  const [chosen, setChosen] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const paths = ["/state", "/backtest/runs", "/paper/replay", "/operations/readiness", "/config"];
    const responses = await Promise.all(paths.map((p) => fetch(api+p, { cache: "no-store", signal })));
    if (responses.some((r) => !r.ok)) throw new Error("Unable to load current research state.");
    const [current, history, session, readiness, config] = await Promise.all(responses.map((r) => r.json()));
    if (signal?.aborted) return;
    setState(current); setRuns(history.runs); setPaper(session); setReasons(readiness.reasons);
    setParameters(Object.keys(config.parameter_sets)); setError(null);
  }, []);

  useEffect(() => {
    let stopped = false, socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let poll: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const pull = async () => {
      try { await refresh(controller.signal); }
      catch { if (!stopped) setError("Connection interrupted. Showing the last received snapshot."); }
      finally { if (!stopped) poll = setTimeout(() => void pull(), 2000); }
    };
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(api.replace(/^http/, "ws")+"/ws/events");
      socket.onopen = () => { void refresh(controller.signal).catch(() => undefined); };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.schema_version === 2 && message.event_type === "state_snapshot") {
            setState((prior) => !prior || prior.quote.stream_id !== message.stream_id || message.sequence >= prior.sequence ? message.payload : prior);
          }
        } catch { setError("An invalid update was received. Refreshing from the server."); }
      };
      socket.onclose = () => { if (!stopped) retry = setTimeout(connect, 2000); };
      socket.onerror = () => socket?.close();
    };
    void pull(); connect();
    return () => { stopped = true; controller.abort(); clearTimeout(retry); clearTimeout(poll); socket?.close(); };
  }, [refresh]);

  async function act(path: string, payload: object) {
    setBusy(true);
    try {
      const response = await fetch(api+path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!response.ok) { const body = await response.json(); throw new Error(body.detail ?? "Action failed."); }
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Action failed."); }
    finally { setBusy(false); }
  }
  const output = state?.research.output ?? {};
  const compared = selected.length ? runs.filter((r) => selected.includes(r.run_id)) : runs;
  return <main>
    <header><strong className="brand">NEXORA / RESEARCH</strong><span className="badge">LOCAL RESEARCH &amp; PAPER ONLY</span></header>
    <section className="intro"><h1>Point &amp; Figure <span> / X · O</span></h1>
      <p className="subtitle">{state?.research_mode === "live_observation" ? "Live observation" : "Recorded research / waiting for a configured feed"}. No broker orders.</p></section>
    {error && <p role="alert" className="warning">{error}</p>}
    <section><h2>Price Structure</h2><p>{state?.quote.quote ? `${state.quote.quote.symbol} | Bid ${state.quote.quote.bid} / Ask ${state.quote.quote.ask} · ${state.quote.quote.event_time}` : "No live quote available"}</p>
      <p>Feed: {state?.quote.status ?? "Unavailable"}{state?.quote.quote?.time_offset_seconds ? ` · Explicit feed time correction: −${state.quote.quote.time_offset_seconds}s · raw: ${state.quote.quote.raw_event_time}` : ""}</p><StructureChart output={output} liveQuote={state?.quote.quote ?? null} /></section>
    <section><h2>Matrix &amp; Regime</h2><p>{state?.research_mode === "live_observation" ? "Current observation" : "Last recorded calculation; not a live readiness indicator"}</p><div className="grid">
      {(output.matrix?.resolutions ?? []).map((r) => <article key={r.name}><h3>{r.name}</h3><p className="status">{r.direction} · {r.status}</p></article>)}
      <article><h3>Regime</h3><p>{output.regime?.state.label ?? "Unavailable"}</p><p>{output.regime?.state.reason ?? "Waiting for confirmed structure"}</p></article>
    </div></section>
    <section><h2>Signals</h2>{!(output.signals?.history.length) && <p>No research signals produced yet.</p>}
      {(output.signals?.history ?? []).slice(-20).reverse().map((s) => <article key={s.signal_id}><h3>{s.side} · {s.status}</h3><p>{s.decision_time}</p><p>{s.reasons.join(" · ")}</p><details><summary>Evidence</summary><p>{s.source_refs.join(", ")}</p><p>{s.signal_id}</p></details></article>)}</section>
    <section><h2>Backtest Lab</h2><p>Results use recorded event prices. Compare runs only with matching data, costs and evaluation assumptions.</p>
      <label>Saved parameter set <select value={chosen} onChange={(e) => setChosen(e.target.value)}><option value="">Choose a configured set</option>{parameters.map((p) => <option key={p}>{p}</option>)}</select></label>
      <button disabled={busy || !chosen || !state?.research.event_count} onClick={() => void act("/backtest/runs", { parameter_set: chosen })}>Run research</button>
      {!parameters.length && <p>No parameter sets configured. Existing verified runs remain available below.</p>}
      {!runs.length ? <p>No stored runs. No sample results are shown.</p> : <>
        <fieldset><legend>Compare stored runs</legend>{runs.map((r) => <label key={r.run_id}><input type="checkbox" checked={selected.includes(r.run_id)} onChange={(e) => setSelected((s) => e.target.checked ? [...s, r.run_id] : s.filter((id) => id !== r.run_id))} />{r.mode} · {r.run_id.slice(-8)}</label>)}</fieldset>
        <div className="table-scroll"><table><thead><tr><th>Mode / status</th><th>Trades</th><th>Win rate</th><th>Expectancy</th><th>Profit factor</th><th>Drawdown</th><th>Entry delay (s)</th></tr></thead><tbody>{compared.map((r) => <tr key={r.run_id}><td>{r.mode}<br />{r.status}</td><td>{r.metrics.trade_count}</td><td>{metric(r.metrics.win_rate)}</td><td>{metric(r.metrics.expectancy)}</td><td>{metric(r.metrics.profit_factor)}</td><td>{metric(r.metrics.max_drawdown)}</td><td>{metric(r.metrics.average_entry_delay_seconds)}</td></tr>)}</tbody></table></div>
        {compared.map((r) => <details key={r.run_id}><summary>{r.mode} provenance</summary><p>Dataset: {r.dataset_id}</p><p>Configuration: {r.config_hash}</p><p>{r.notes.join(" · ")}</p></details>)}
      </>}</section>
    <section><h2>Paper Trading</h2><p>{paper?.status ?? "Unavailable"} · {paper?.accepted ?? 0} filled · {paper?.rejected ?? 0} rejected</p>
      {paper?.state.cash && <p>Cash {paper.state.cash} · Realized P&amp;L before fees {paper.state.realized_pnl}</p>}
      {paper && paper.status !== "unavailable" && <div>{["pause", "resume", "kill"].map((action) => <button disabled={busy} key={action} onClick={() => void act("/paper/control", { action })}>{action === "kill" ? "Stop paper execution" : `${action} paper`}</button>)}</div>}
      {(paper?.ledger ?? []).slice(-10).map((l) => <p key={l.entry_id}>{l.detail} · {l.amount}</p>)}</section>
    <section><h2>System</h2><p>Storage: {state?.storage_backend ?? "Unavailable"} · Recorded events: {state?.research.event_count ?? 0}</p>
      <p>Feed: {state?.quote.status ?? "Unavailable"} · Coverage: {state?.quality.completeness ?? "Unknown"}</p>
      <p>{reasons.length ? reasons.join(" · ") : "No readiness reasons received"}</p><p>Remote access disabled. Production hardening requires verified deployment and recovery evidence.</p></section>
    <footer><span>RESEARCH BOUNDARY</span><p>Replay and paper results are research artifacts, not live execution approval.</p></footer>
  </main>;
}
