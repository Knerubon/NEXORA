"use client";

import { environmentDisplay } from "./environment";

import { SignalIntelligence, type SignalDecision, type DecisionContext, type PanelProps } from "./signal-intelligence";

import { MatrixFloat } from "./matrix-float";
import { latestQuote, type QuoteSnapshot } from "./live-quote";

import { useCallback, useEffect, useRef, useState } from "react";

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
  signals?: { history: Signal[]; decision?: SignalDecision };
};
type State = {
  sequence: number; research_mode: string; storage_backend: string; matrix_status?: string;
  quote: QuoteSnapshot;
  quality: { status: string; completeness: string; counters: { observed: number; gaps: number; reconnects: number } };
  research: { event_count: number; error: string | null; output: Output };
  decision_context?: DecisionContext;
};
type Run = { run_id: string; mode: string; status: string; dataset_id: string; config_hash: string;
  metrics: { trade_count: number; win_rate: string; expectancy: string; profit_factor: string | null;
    max_drawdown: string; average_entry_delay_seconds: string }; notes: string[] };
type Paper = { status: string; accepted: number; rejected: number; fills: unknown[];
  state: { cash?: string; realized_pnl?: string }; ledger: { entry_id: string; detail: string; amount: string }[] };

const metric = (value: string | null) => value === null ? "Undefined" : Number(value).toLocaleString("en", { maximumFractionDigits: 4 });

const environment = environmentDisplay(process.env.NEXT_PUBLIC_NEXORA_ENV);
const api = process.env.NEXT_PUBLIC_API_BASE_URL ?? environment.api;

function StructureChart({ output, liveQuote, panelProps }: { panelProps?: PanelProps; output: Output; liveQuote?: { symbol: string; bid: string; ask: string } | null }) {
  const [zoom, setZoom] = useState(120);
  const [focused, setFocused] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
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
  const symbol = liveQuote?.symbol ?? output.event?.symbol ?? "Waiting for feed";
  const scale = zoom / 100;
  function latestPrice() {
    const viewport = scrollRef.current;
    if (!viewport) return;
    viewport.scrollTo({
      left: Math.max(0, (90 + (columns.length - 1) * 30) * scale - viewport.clientWidth / 2),
      top: Math.max(0, y(latest) * scale - viewport.clientHeight / 2),
      behavior: "smooth",
    });
  }
  return <div className={`pnf-workspace${focused ? " is-focused" : ""}`} onKeyDown={(event) => { if (event.key === "Escape") setFocused(false); }}>
    <div className="chart-toolbar">
      <div className="chart-title"><strong>{symbol}</strong><span>Point &amp; Figure · box {transitions.length ? safeStep : "—"}</span></div>
      <div className="chart-controls" role="group" aria-label="Chart controls">
        <button aria-label="Zoom out" disabled={zoom <= 60} onClick={() => setZoom((value) => value - 20)}>−</button>
        <output aria-label="Chart zoom">{zoom}%</output>
        <button aria-label="Zoom in" disabled={zoom >= 200} onClick={() => setZoom((value) => value + 20)}>+</button>
        <button disabled={!prices.length} onClick={latestPrice}>Latest price</button>
        <button aria-pressed={focused} onClick={() => setFocused((value) => !value)}>{focused ? "Exit focus" : "Focus chart"}</button>
      </div>
    </div>
    <div className="chart-body">
    <div ref={scrollRef} className="pnf-scroll" tabIndex={0} aria-label="Scrollable point and figure chart">
      <svg width={width * scale} height={(height + 52) * scale} viewBox={`0 0 ${width} ${height + 52}`} role="img" aria-label="Point and figure: green X rising boxes, red O falling boxes">
        <defs><pattern id="pnf-grid" x="75" y="26" width="30" height="26" patternUnits="userSpaceOnUse"><path d="M 30 0 L 0 0 0 26" fill="none" stroke="#dfe3e7" strokeWidth="1" /></pattern></defs>
        <rect width="100%" height="100%" fill="white" /><rect x="75" y="13" width={width-75} height={height+26} fill="url(#pnf-grid)" />
        {levels.map((l,i) => <g key={i}><rect x="0" y={y(Number(l.price))-13} width={width} height="26" fill={l.side === "support" ? "#527dea" : "#ef5350"} opacity=".34" /><title>{l.side}: {l.price} · confirmed</title></g>)}
        {Array.from({length: rows+1},(_,i) => {const price = top-i*rowStep; return <g key={i}><line x1="0" x2={width} y1={y(price)} y2={y(price)} stroke="#e5e7eb" />{Array.from({ length: Math.ceil(width / 240) }, (_, label) => <text key={label} x={8 + label * 240} y={y(price)+4} fontSize="11" fill="#707780">{price.toFixed(2)}</text>)}</g>;})}
        {cells.map((c,i) => {const x = 90 + columns.findIndex((col) => col.column_id === c.column)*30; return <g key={i} data-pnf-glyph="" data-price={c.price}><title>{`Column ${c.column} · ${c.direction} · ${c.price.toFixed(2)}`}</title>{c.direction === "X" ? <path d={`M ${x-5} ${glyphY(c.price)-5} l 10 10 m 0 -10 l -10 10`} stroke="#09a77a" strokeWidth="2" fill="none" /> : <circle cx={x} cy={glyphY(c.price)} r="5" stroke="#f34b55" strokeWidth="2" fill="none" />}</g>;})}
        {prices.length > 0 && <g><line x1="75" x2={width} y1={y(latest)} y2={y(latest)} stroke="#64748b" strokeDasharray="4 5" /><title>{liveQuote ? `Latest live quote: ${latest.toFixed(2)}` : `Latest observed price: ${latest}`}</title></g>}
      </svg>
    </div>
    <MatrixFloat {...panelProps} decision={output.signals?.decision} matrix={output.matrix} onDetails={() => setFocused(false)} />
    </div>
    <div className="chart-caption">{liveQuote ? `Box ${safeStep} | ${columns.length} columns | ${cells.length} confirmed boxes | Latest live quote ${latest.toFixed(2)} · ${liveQuote.bid} / ${liveQuote.ask}` : cells.length ? `${columns.length} columns · ${cells.length} confirmed boxes · latest box ${safeStep}` : "Waiting for the first confirmed box — no sample data"} · {output.config_version ?? "Unconfigured"}</div>
  </div>;
}

export default function Home() {
  const [state, setState] = useState<State | null>(null);
  const [quote, setQuote] = useState<QuoteSnapshot | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [paper, setPaper] = useState<Paper | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [parameters, setParameters] = useState<string[]>([]);
  const [chosen, setChosen] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<"live" | "reconnecting" | "offline">("offline");
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const refreshControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const socketGenerationRef = useRef(0);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const paths = ["/state", "/backtest/runs", "/paper/replay", "/operations/readiness", "/config"];
    const responses = await Promise.all(paths.map((p) => fetch(api+p, { cache: "no-store", signal })));
    if (responses.some((r) => !r.ok)) throw new Error("Unable to load current research state.");
    const [current, history, session, readiness, config] = await Promise.all(responses.map((r) => r.json()));
    if (signal?.aborted) return;
    setState(current);
    setQuote((prior) => latestQuote(prior, current.quote));
    setRuns(history.runs);
    setPaper(session);
    setReasons(readiness.reasons);
    setParameters(Object.keys(config.parameter_sets));
    setError(null);
    setConnectionStatus((prior) => (prior === "offline" || prior === "reconnecting" ? "live" : prior));
  }, []);

  const connect = useCallback(function openSocket() {
    if (!mountedRef.current) return;
    if (socketRef.current !== null) return;

    const socket = new WebSocket(api.replace(/^http/, "ws") + "/ws/events");
    const generation = ++socketGenerationRef.current;
    socketRef.current = socket;

    const scheduleReconnect = () => {
      if (!mountedRef.current) return;
      if (socketGenerationRef.current !== generation) return;
      if (reconnectTimerRef.current !== null) return;

      const delay = Math.min(5000, 1000 * (2 ** Math.min(reconnectAttemptRef.current, 4)));
      reconnectAttemptRef.current += 1;
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        if (!mountedRef.current) return;
        if (socketGenerationRef.current !== generation) return;
        if (socketRef.current !== null) return;
        openSocket();
      }, delay);
    };

    socket.onopen = () => {
      if (!mountedRef.current || socketRef.current !== socket) return;
      reconnectAttemptRef.current = 0;
      clearReconnectTimer();
      setConnectionStatus("live");
      setError(null);
      if (refreshControllerRef.current) {
        refreshControllerRef.current.abort();
      }
      refreshControllerRef.current = new AbortController();
      void refresh(refreshControllerRef.current.signal).catch(() => {
        if (!mountedRef.current || socketRef.current !== socket) return;
        setConnectionStatus("reconnecting");
      });
    };

    socket.onmessage = (event) => {
      if (socketRef.current !== socket) return;
      try {
        const message = JSON.parse(event.data);
        if (message.schema_version === 2 && message.event_type === "state_snapshot") {
          setState((prior) => !prior || prior.quote.stream_id !== message.stream_id || message.sequence >= prior.sequence ? message.payload : prior);
          setQuote((prior) => latestQuote(prior, message.payload.quote));
          setError(null);
          setConnectionStatus("live");
        } else if (message.schema_version === 2 && message.event_type === "quote_snapshot") {
          setQuote((prior) => latestQuote(prior, message.payload));
          setError(null);
          setConnectionStatus("live");
        }
      } catch {
        if (socketRef.current !== socket) return;
        setError("An invalid update was received. Refreshing from the server.");
        setConnectionStatus("reconnecting");
      }
    };

    socket.onclose = () => {
      if (socketRef.current !== socket) return;
      socketRef.current = null;
      if (!mountedRef.current) return;
      setConnectionStatus("reconnecting");
      scheduleReconnect();
    };

    socket.onerror = () => {
      if (socketRef.current !== socket) return;
      setConnectionStatus("reconnecting");
      setError("Connection interrupted. Showing the last received snapshot.");
      socket.close();
    };
  }, [clearReconnectTimer, refresh]);

  useEffect(() => {
    mountedRef.current = true;

    const bootstrap = async () => {
      try {
        if (refreshControllerRef.current) {
          refreshControllerRef.current.abort();
        }
        refreshControllerRef.current = new AbortController();
        await refresh(refreshControllerRef.current.signal);
        setConnectionStatus("live");
      } catch {
        setConnectionStatus("offline");
        setError("Connection interrupted. Showing the last received snapshot.");
      }
    };

    void bootstrap();
    connect();

    return () => {
      mountedRef.current = false;
      socketGenerationRef.current += 1;
      clearReconnectTimer();
      if (refreshControllerRef.current) {
        refreshControllerRef.current.abort();
        refreshControllerRef.current = null;
      }
      if (socketRef.current) {
        const socket = socketRef.current;
        socketRef.current = null;
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        socket.close();
      }
    };
  }, [clearReconnectTimer, connect, refresh]);

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
    <header><strong className="brand">NEXORA / <span style={{ background: environment.production ? "#164e63" : "#fbbf24", color: environment.production ? "#ffffff" : "#111827", padding: "4px 8px", borderRadius: 4 }}>{environment.label}</span></strong><span className="badge">LOCAL RESEARCH &amp; PAPER ONLY</span></header>
    <section className="intro"><h1>Point &amp; Figure <span> / X · O</span></h1>
      <p className="subtitle">{state?.research_mode === "live_observation" ? "Live observation" : "Recorded research / waiting for a configured feed"}. No broker orders.</p></section>
    {error && <p role="alert" className="warning">{error}</p>}
    <section className="price-structure" aria-label="Price Structure"><p data-testid="live-quote" data-sequence={quote?.sequence}>{quote?.quote ? `Bid ${quote.quote.bid} / Ask ${quote.quote.ask} · ${quote.quote.event_time}` : "No live quote available"}</p>
      <p>Feed: {connectionStatus === "live" ? "LIVE" : connectionStatus === "reconnecting" ? "RECONNECTING" : "OFFLINE"} · {quote?.status ?? "Unavailable"}{quote?.quote?.time_offset_seconds ? ` · Explicit feed time correction: −${quote.quote.time_offset_seconds}s · raw: ${quote.quote.raw_event_time}` : ""}</p><StructureChart output={output} liveQuote={quote?.quote ?? null} panelProps={{ symbol: output.event?.symbol ?? quote?.quote?.symbol ?? quote?.symbol, decisionContext: state?.decision_context, matrixStatus: state?.matrix_status, researchMode: state?.research_mode, connectionError: Boolean(error) }} /></section>
    <SignalIntelligence decision={output.signals?.decision} decisionContext={state?.decision_context}
      symbol={output.event?.symbol ?? quote?.quote?.symbol ?? quote?.symbol}
      matrix={output.matrix} matrixStatus={state?.matrix_status} feedStatus={quote?.status}
      quoteTime={quote?.quote?.event_time} researchMode={state?.research_mode}
      connectionError={Boolean(error)} regime={output.regime?.state} history={output.signals?.history} />
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
      <p>Feed: {quote?.status ?? "Unavailable"} · Coverage: {state?.quality.completeness ?? "Unknown"}</p>
      <p>{reasons.length ? reasons.join(" · ") : "No readiness reasons received"}</p><p>Remote access disabled. Production hardening requires verified deployment and recovery evidence.</p></section>
    <footer><span>RESEARCH BOUNDARY</span><p>Replay and paper results are research artifacts, not live execution approval.</p></footer>
  </main>;
}
