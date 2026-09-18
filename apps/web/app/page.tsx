"use client";

import { useCallback, useEffect, useState } from "react";

import { StructureChart } from "./structure-chart";
import type { Column, Transition } from "./pnf-layout";
type Signal = { signal_id: string; side: string; decision_time: string; reasons: string[]; source_refs: string[]; status: string };
type Output = {
  columns?: Column[]; transitions?: Transition[];
  matrix?: { alignment: string; resolutions: { name: string; direction: string; status: string }[] };
  structure?: { levels: { side: string; price: string; status: string }[] };
  regime?: { state: { label: string; reason: string } };
  signals?: { history: Signal[] };
};
type State = {
  sequence: number; research_mode: string; storage_backend: string;
  quote: { stream_id: string; status: string; quote: { bid: string; ask: string; event_time: string } | null };
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
    <section className="intro"><p className="eyebrow">OBSERVE · EXPLAIN · REPLAY</p><h1>Price structure,<br /><span>with evidence.</span></h1>
      <p className="subtitle">{state?.research_mode === "live_observation" ? "Live observation" : "Recorded research / waiting for a configured feed"}. No broker orders.</p></section>
    {error && <p role="alert" className="warning">{error}</p>}
    <section><h2>Price Structure</h2><p>{state?.quote.quote ? `Bid ${state.quote.quote.bid} / Ask ${state.quote.quote.ask} · ${state.quote.quote.event_time}` : "No live quote available"}</p>
      <article><StructureChart output={output} /></article></section>
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
