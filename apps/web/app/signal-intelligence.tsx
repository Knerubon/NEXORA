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
  positive_evidence: { code: string; reason: string }[];
  negative_evidence: { code: string; reason: string }[];
};

export function SignalIntelligence({ decision: d }: { decision?: SignalDecision }) {
  const strength = (value?: number | null) => d?.strength_available && value != null
    ? `${value}/100` : "Unavailable";
  return <section className="signal-intelligence" aria-label="Signal Intelligence">
    <h2>Signal Intelligence</h2>
    <div className="signal-summary">
      <span>Decision <strong>{d?.action ?? "Unavailable"}</strong></span>
      <span>BUY Strength <strong>{strength(d?.buy_strength)}</strong></span>
      <span>SELL Strength <strong>{strength(d?.sell_strength)}</strong></span>
      <span>Signal Score <strong>{d ? `${d.score}/100` : "Unavailable"}</strong></span>
    </div>
    <small>Independent evidence strengths; not win probability. Historical metrics are in Backtest Lab.</small>
    <dl className="signal-plan">
      <div><dt>Entry</dt><dd>{d?.entry_zone ? `${d.entry_zone.low} – ${d.entry_zone.high}` : "Unavailable"}</dd></div>
      <div><dt>Invalidation</dt><dd>{d?.invalidation_price ?? "Unavailable"}</dd></div>
      {["TP1", "TP2"].map(name => <div key={name}><dt>{name}</dt><dd>{d?.targets.find(t => t.name === name)?.price ?? "Unavailable"}</dd></div>)}
      <div><dt>R:R (TP2)</dt><dd>{d?.risk_reward != null ? `1:${d.risk_reward}` : "Unavailable"}</dd></div>
    </dl>
    <p>Patterns: {d?.patterns.length ? d.patterns.map(p => `${p.pattern_type} · ${p.direction} · ${p.relation}`).join("; ") : "None reported"}</p>
    <details><summary>Evidence and plan basis</summary>
      {!d && <p>Waiting for a backend decision.</p>}
      {d?.positive_evidence.map((e, i) => <p key={`positive-${i}`}>{e.code}: {e.reason}</p>)}
      {d?.negative_evidence.map((e, i) => <p key={`negative-${i}`}>{e.code}: {e.reason}</p>)}
      {d?.entry_zone && <p>{d.entry_zone.reason}</p>}
      {d?.invalidation_reason && <p>{d.invalidation_reason}</p>}
      {d?.targets.map(t => <p key={t.name}>{t.name}: {t.method}</p>)}
      {d?.patterns.map((p, i) => <p key={i}>{p.pattern_type}: confirmed {p.confirmation_time}</p>)}
    </details>
  </section>;
}
