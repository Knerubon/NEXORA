"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type QuoteSnapshot = {
  sequence: number;
  status: string;
  code: string;
  symbol: string | null;
  quote: {
    bid: string;
    ask: string;
    spread: string;
    event_time: string;
  } | null;
};

type QualitySnapshot = {
  status: string;
  code: string;
  completeness: string;
  counters: {
    observed: number;
    duplicates: number;
    out_of_order: number;
    gaps: number;
    backfills: number;
    reconnects: number;
    disconnects: number;
  };
};

type DashboardState = {
  sequence: number;
  quote: QuoteSnapshot;
  quality: QualitySnapshot;
  matrix_status: string;
  structure_status: string;
  regime_status: string;
  signals_status: string;
  backtest_lab_status: string;
};

type BacktestRunsResponse = {
  runs: Array<{
    run_id: string;
    mode: string;
    status: string;
    metrics: {
      trade_count: number;
      expectancy: string;
    };
  }>;
};

const apiBase = process.env.NEXT_PUBLIC_NEXORA_API_URL ?? "http://127.0.0.1:8000";

function statusLabel(status: string): string {
  if (status === "live") return "Live";
  if (status === "stale") return "Stale";
  if (status === "clock_skew") return "Clock skew";
  if (status === "disconnected") return "Disconnected";
  if (status === "error") return "Error";
  if (status === "pending_p10") return "Pending P10";
  if (status === "unavailable") return "Unavailable";
  return status;
}

export default function Home() {
  const [state, setState] = useState<DashboardState | null>(null);
  const [backtestRuns, setBacktestRuns] = useState<BacktestRunsResponse["runs"]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const pullState = async () => {
      try {
        const [stateResponse, runsResponse] = await Promise.all([
          fetch(`${apiBase}/state`, { cache: "no-store" }),
          fetch(`${apiBase}/backtest/runs`, { cache: "no-store" }),
        ]);
        if (!stateResponse.ok) {
          throw new Error(`state_fetch_failed_${stateResponse.status}`);
        }
        if (!runsResponse.ok) {
          throw new Error(`backtest_runs_fetch_failed_${runsResponse.status}`);
        }
        const data = (await stateResponse.json()) as DashboardState;
        const runs = (await runsResponse.json()) as BacktestRunsResponse;
        if (active) {
          setState(data);
          setBacktestRuns(runs.runs);
          setError(null);
        }
      } catch (fetchError) {
        if (active) {
          const message = fetchError instanceof Error ? fetchError.message : "state_fetch_failed";
          setError(message);
        }
      }
    };

    void pullState();
    const interval = setInterval(() => void pullState(), 2000);
    const ws = new WebSocket(`${apiBase.replace("http", "ws")}/ws/events`);
    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as { event_type: string; payload: unknown };
        if (parsed.event_type === "quote_snapshot") {
          setState((previous) => {
            if (!previous) return previous;
            return { ...previous, quote: parsed.payload as QuoteSnapshot };
          });
        }
        if (parsed.event_type === "quality_snapshot") {
          setState((previous) => {
            if (!previous) return previous;
            return { ...previous, quality: parsed.payload as QualitySnapshot };
          });
        }
      } catch {
        setError("invalid_ws_payload");
      }
    };
    ws.onerror = () => setError("ws_stream_error");

    return () => {
      active = false;
      clearInterval(interval);
      ws.close();
    };
  }, []);

  const cards = useMemo(
    () => [
      {
        title: "Live Structure",
        status: state ? statusLabel(state.quote.status) : "Loading",
        detail: state?.quote.quote
          ? `Bid ${state.quote.quote.bid} / Ask ${state.quote.quote.ask} / Spread ${state.quote.quote.spread}`
          : "No quote snapshot yet",
      },
      {
        title: "Matrix",
        status: statusLabel(state?.matrix_status ?? "unavailable"),
        detail: "Multi-resolution state from core engine.",
      },
      {
        title: "Signals",
        status: statusLabel(state?.signals_status ?? "unavailable"),
        detail: "Explainable research signals only. No order execution.",
      },
      {
        title: "Backtest Lab",
        status: statusLabel(state?.backtest_lab_status ?? "pending_p10"),
        detail:
          backtestRuns.length > 0
            ? `${backtestRuns.length} runs ready for comparison`
            : "No stored runs yet",
      },
      {
        title: "System",
        status: state ? statusLabel(state.quality.status) : "Loading",
        detail: state
          ? `Obs ${state.quality.counters.observed}, gap ${state.quality.counters.gaps}, reconnect ${state.quality.counters.reconnects}`
          : "No health snapshot yet",
      },
    ],
    [backtestRuns.length, state],
  );

  return (
    <main>
      <header>
        <Link href="/" aria-label="NEXORA home" className="brand">
          NEXORA<span> / DASHBOARD</span>
        </Link>
        <span className="badge">LOCAL OBSERVATION ONLY</span>
      </header>

      <section className="intro" aria-labelledby="title">
        <p className="eyebrow">09 / WEB DASHBOARD</p>
        <h1 id="title">
          Observe state in real time.<br />
          <span>Keep research traceable.</span>
        </h1>
        <p className="subtitle">
          Local dashboard for quotes, quality, structure, matrix, and signal surfaces.
          Remote access must be authenticated and encrypted before external use.
        </p>
      </section>

      {error ? (
        <section aria-live="polite">
          <article>
            <h3>Connection warning</h3>
            <p className="status">Error</p>
            <p>{error}</p>
          </article>
        </section>
      ) : null}

      <section aria-labelledby="views-title">
        <div className="section-heading">
          <h2 id="views-title">Views</h2>
          <span>{state ? `Sequence ${state.sequence}` : "Waiting for state stream"}</span>
        </div>
        <div className="grid">
          {cards.map((card, index) => (
            <article key={card.title}>
              <span className="index">0{index + 1}</span>
              <h3>{card.title}</h3>
              <p className="status">{card.status}</p>
              <p>{card.detail}</p>
            </article>
          ))}
        </div>
      </section>

      <footer>
        <span>RESEARCH BOUNDARY</span>
        <p>No broker orders, no live auto-trading, and no secret material in UI output.</p>
      </footer>
    </main>
  );
}
