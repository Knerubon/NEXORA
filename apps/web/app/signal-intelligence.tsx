export type SignalEvidence = {
  code: string; reason: string;
  component?: string; points?: number; polarity?: "bullish" | "bearish" | "neutral";
};

export type SignalDecision = {
  action: "BUY" | "SELL" | "WAIT";
  score: number;
  buy_strength?: number | null;
  sell_strength?: number | null;
  strength_available?: boolean;
  entry_zone: { low: string; high: string; reason: string } | null;
  invalidation_price: string | null;
  invalidation_reason: string | null;
  targets: { name: string; price: string; method: string }[];
  risk_reward: string | null;
  patterns: { pattern_type: string; direction: string; relation: string; confirmation_time: string }[];
  positive_evidence: SignalEvidence[];
  negative_evidence: SignalEvidence[];
};

// Backend-derived only (Decision Clarity + Bias V1). The frontend never
// computes bias/state/alignment itself; it renders these fields verbatim.
export type DecisionContext = {
  bias: "BULLISH" | "BULLISH_LEAN" | "MIXED" | "BEARISH_LEAN" | "BEARISH" | "UNAVAILABLE";
  state: "DEVELOPING" | "DIRECTION_CONFIRMED" | "UNAVAILABLE";
  alignment: { aligned: number; total: number };
  reasons: string[];
  waiting_for: string[];
};

export type PanelProps = {
  history?: { signal_id: string; side: string; status: string; decision_time: string; reasons: string[]; source_refs: string[] }[];
  decision?: SignalDecision;
  decisionContext?: DecisionContext;
  symbol?: string;
  matrix?: { alignment: string; resolutions: { name: string; direction: string; status: string }[] };
  matrixStatus?: string;
  feedStatus?: string;
  quoteTime?: string;
  researchMode?: string;
  connectionError?: boolean;
  regime?: { label: string; reason: string };
};

const biasLabels: Record<DecisionContext["bias"], string> = {
  BULLISH: "Bullish", BULLISH_LEAN: "Bullish lean", MIXED: "Mixed",
  BEARISH_LEAN: "Bearish lean", BEARISH: "Bearish", UNAVAILABLE: "Unavailable",
};
const stateLabels: Record<DecisionContext["state"], string> = {
  DEVELOPING: "Developing", DIRECTION_CONFIRMED: "Direction confirmed", UNAVAILABLE: "Unavailable",
};
function biasClass(bias?: DecisionContext["bias"]) {
  if (bias === "BULLISH" || bias === "BULLISH_LEAN") return "up";
  if (bias === "BEARISH" || bias === "BEARISH_LEAN") return "down";
  return "";
}

const components: Record<string, string> = {
  pnf: "P&F", structure: "Structure", support_resistance: "S&R",
  matrix: "Matrix", pattern: "Pattern", regime: "Regime",
};

// Presentation of backend polarity only. Never sum evidence or infer a decision.
function relationship(e: SignalEvidence, action?: SignalDecision["action"]) {
  if (!e.polarity || e.polarity === "neutral") return "Context";
  if (!action || action === "WAIT") return `${e.polarity} evidence`;
  return e.polarity === (action === "BUY" ? "bullish" : "bearish")
    ? `Supports ${action}` : `Opposes ${action}`;
}

function Strength({ side, value, available }: { side: "BUY" | "SELL"; value?: number | null; available?: boolean }) {
  const valid = available && value != null && Number.isFinite(value) && value >= 0 && value <= 100;
  return <div className={`strength strength-${side.toLowerCase()}`}>
    <span>{side} Strength</span>
    <div className="strength-ring" role="img" aria-label={`${side} Strength: ${valid ? `${value}/100` : "Unavailable"}`}>
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle className="ring-track" cx="50" cy="50" r="43" />
        {valid && <circle className="ring-value" cx="50" cy="50" r="43" pathLength="100" strokeDasharray={`${value} 100`} transform="rotate(-90 50 50)" />}
      </svg>
      <strong>{valid ? <>{value}<small>/100</small></> : <span className="strength-unavailable">Unavailable</span>}</strong>
    </div>
  </div>;
}

export function MatrixResolutions({ matrix }: Pick<PanelProps, "matrix">) {
  return (<div className="resolution-list">
          {matrix?.resolutions.length ? matrix.resolutions.map(r => <div key={r.name}>
            <span>{r.name.toUpperCase()}</span><b className={r.direction === "X" ? "up" : r.direction === "O" ? "down" : ""}>{r.direction === "X" || r.direction === "O" ? r.direction : "—"}</b><small>{r.status}</small>
          </div>) : <p>Resolutions unavailable</p>}
        </div>);
}

export function MatrixSummary({ decision: d, decisionContext: c, symbol, matrix, matrixStatus, connectionError, researchMode }: PanelProps) {
  return <div className="matrix-summary">
    <header><strong>{symbol ?? "Symbol unavailable"}</strong><span>{connectionError ? "Snapshot only" : (matrixStatus ?? "unavailable").toUpperCase()}</span></header>
    {(researchMode !== "live_observation" || connectionError) && <small>Recorded / last received calculation</small>}
    <MatrixResolutions matrix={matrix} />
    <div className="strength-pair"><Strength side="BUY" value={d?.buy_strength} available={d?.strength_available} /><Strength side="SELL" value={d?.sell_strength} available={d?.strength_available} /></div>
    <p>Decision <strong className={`decision-${d?.action.toLowerCase() ?? "unavailable"}`}>{d?.action ?? "Unavailable"}</strong> · Bias: <strong className={biasClass(c?.bias)}>{biasLabels[c?.bias ?? "UNAVAILABLE"]}</strong></p>
    <small>Independent evidence strength · not win probability</small>
  </div>;
}

export function SignalIntelligence({ decision: d, decisionContext: c, symbol, matrix, matrixStatus, feedStatus, quoteTime,
  researchMode, connectionError, regime, history }: PanelProps) {
  const positive = d?.positive_evidence ?? [];
  const negative = d?.negative_evidence ?? [];
  return <section id="signal-analysis" className="signal-intelligence" aria-label="Matrix decision panel">
    <div className="decision-heading">
      <div><span className="panel-eyebrow">MATRIX / SIGNAL INTELLIGENCE</span><h2>{symbol ?? "Symbol unavailable"}</h2></div>
      <div className="panel-health">
        <span className="health-chip" title="READY means data/calculation readiness, not readiness to BUY or SELL.">Matrix: {connectionError ? "Snapshot only" : (matrixStatus ?? "unavailable").toUpperCase()}</span>
        <span className="health-chip">Feed: {connectionError ? "Connection interrupted" : feedStatus ?? "Unavailable"}</span>
        {quoteTime && <time dateTime={quoteTime}>Quote time: {quoteTime}</time>}
      </div>
    </div>
    {(researchMode !== "live_observation" || connectionError) && <p className="snapshot-note">Recorded / last received calculation · not live readiness</p>}
    <div className="decision-grid">
      <div className="resolution-panel">
        <h3 title="FAST, MEDIUM and SLOW are independently configured structure resolutions, not timeframes or assumed box sizes.">Resolution (Matrix) ⓘ</h3>
        <MatrixResolutions matrix={matrix} />
        <small>Structure resolutions · calculation status</small>
      </div>
      <div className="strength-panel">
        <div className="strength-pair"><Strength side="BUY" value={d?.buy_strength} available={d?.strength_available} /><Strength side="SELL" value={d?.sell_strength} available={d?.strength_available} /></div>
        <div className="decision-line"><span>Decision <strong className={`decision-${d?.action.toLowerCase() ?? "unavailable"}`}>{d?.action ?? "Unavailable"}</strong></span><span>Signal Score <strong>{d ? `${d.score}/100` : "Unavailable"}</strong></span></div>
        <small>Independent evidence strengths; not win probability. They need not sum to 100.</small>
      </div>
      <div className="market-panel">
        <h3 title="Backend-derived from matrix resolution directions only; never computed in the browser.">Bias ⓘ</h3>
        <strong className={biasClass(c?.bias)}>{biasLabels[c?.bias ?? "UNAVAILABLE"]}</strong>
        <p><span>State</span> <strong>{stateLabels[c?.state ?? "UNAVAILABLE"]}</strong></p>
        <p><span>Alignment</span> <strong>{c ? `${c.alignment.aligned} / ${c.alignment.total}` : "Unavailable"}</strong></p>
        <p><span>Regime</span> <strong>{regime?.label ?? "Unavailable"}</strong></p>
        {regime && <small>{regime.reason}</small>}
        <p className="pattern-summary">Pattern: {d?.patterns.length ? <>{d.patterns[0].pattern_type.replaceAll("_", " ")} · {d.patterns[0].direction} · {d.patterns[0].relation}{d.patterns.length > 1 ? ` (+${d.patterns.length - 1} in setup)` : ""}</> : d ? "None reported" : "Unavailable"}</p>
        {d?.action === "WAIT" && c && (c.reasons.length > 0 || c.waiting_for.length > 0) && <div className="wait-context">
          {c.reasons.length > 0 && <><h4>Why WAIT?</h4><ul>{c.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul></>}
          {c.waiting_for.length > 0 && <><h4>Waiting for</h4><ul>{c.waiting_for.map((item, i) => <li key={i}>{item}</li>)}</ul></>}
        </div>}
      </div>
    </div>
    <div className="analysis-summary">
      <h3>AI วิเคราะห์ <span>· Evidence summary</span></h3>
      <p className="analysis-note">Deterministic evidence summary · no price prediction</p>
      <p>{d ? `Decision ${d.action} · Signal Score ${d.score}/100. ${positive.slice(0, 2).map(e => e.reason).join(" · ") || "No supporting evidence supplied."}` : "Unavailable — waiting for engine evidence."}</p>
      <div className="evidence-overview">{Object.entries(components).map(([key, label]) => {
        const items = positive.filter(e => e.component === key);
        const cautions = negative.filter(e => e.component === key);
        const labels = [...new Set([...items.map(e => relationship(e, d?.action)), ...cautions.map(() => "Caution / conflict")])];
        return <div key={key}><strong>{label}</strong><span>{labels.length ? labels.join(" · ") : "Not reported"}</span></div>;
      })}</div>
      {negative[0] && <p className="analysis-caution">Caution: {negative[0].reason}</p>}
    </div>
    <div className="decision-details">
      <article><h3>Pattern</h3>{d?.patterns.length ? d.patterns.map((p, i) => <p key={i}>{p.pattern_type.replaceAll("_", " ")} · {p.direction} · {p.relation}</p>) : <p>{d ? "None reported" : "Unavailable"}</p>}</article>
      <details><summary>{d ? `Why ${d.action}?` : "Why unavailable?"}</summary>
        <h3>Evidence Breakdown</h3>
        {!positive.length && !negative.length && <p>No evidence supplied.</p>}
        {[...positive.map(e => ({ e, caution: false })), ...negative.map(e => ({ e, caution: true }))].map(({ e, caution }, i) => <div className="evidence-row" key={i}>
          <strong>{components[e.component ?? ""] ?? "Evidence"} · {caution ? "Caution / conflict" : relationship(e, d?.action)}</strong>
          <span>{e.points != null ? `${e.points} points` : "Points unavailable"}{e.polarity ? ` · ${e.polarity}` : ""}</span>
          <p>{e.reason}</p><small>{e.code}</small>
        </div>)}
        <small>Points and polarity are backend evidence fields, not a net score. Historical win rate and sample size remain in Backtest Lab.</small>
      </details>
      <details><summary>Trade Plan · View Setup</summary>
        <dl className="signal-plan">
          <div><dt>Entry Zone</dt><dd>{d?.entry_zone ? `${d.entry_zone.low} – ${d.entry_zone.high}` : "Unavailable"}</dd></div>
          <div><dt>Invalidation</dt><dd>{d?.invalidation_price ?? "Unavailable"}</dd></div>
          {["TP1", "TP2"].map(name => <div key={name}><dt>{name}</dt><dd>{d?.targets.find(t => t.name === name)?.price ?? "Unavailable"}</dd></div>)}
          <div><dt>R:R (TP2)</dt><dd>{d?.risk_reward != null ? `1:${d.risk_reward}` : "Unavailable"}</dd></div>
        </dl>
        {d?.entry_zone && <p>{d.entry_zone.reason}</p>}
        {d?.invalidation_reason && <p>{d.invalidation_reason}</p>}
        {d?.targets.map(t => <p key={t.name}>{t.name}: {t.method}</p>)}
        {d?.patterns.map((p, i) => <p key={i}>{p.pattern_type} · {p.direction} · {p.relation} · confirmed {p.confirmation_time}</p>)}
        <small>Plan and R:R are supplied by the engine, not recalculated at an assumed fill price.</small>
      </details>
      <article><h3>Recent Signals</h3>{history?.length ? history.slice(-5).reverse().map(s => <details key={s.signal_id}><summary>{s.side} · {s.status}</summary><time>{s.decision_time}</time><p>{s.reasons.join(" · ")}</p><small>{s.source_refs.join(", ")}</small></details>) : <p>No research signals produced yet.</p>}</article>
    </div>
  </section>;
}
