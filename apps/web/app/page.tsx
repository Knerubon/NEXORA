import Link from "next/link";

const components = [
  ["Market data", "Not configured", "A data source will be added in the market-data phase."],
  ["Structure engine", "Not implemented", "Price-structure research begins after data contracts are validated."],
  ["Database", "Not configured", "Local PostgreSQL setup is documented; no connection is active."],
  ["API", "Not checked", "This local shell does not poll the API. Check /health separately."],
];

export default function Home() {
  return (
    <main>
      <header>
        <Link href="/" aria-label="NEXORA home" className="brand">
          NEXORA<span> / RESEARCH</span>
        </Link>
        <span className="badge">LOCAL WORKSPACE</span>
      </header>
      <section className="intro" aria-labelledby="title">
        <p className="eyebrow">01 / FOUNDATION</p>
        <h1 id="title">
          Understand the structure.<br /><span>Build on evidence.</span>
        </h1>
        <p className="subtitle">
          Your price-structure research workspace. Set up locally to prepare for
          data, replay and explainable analysis.
        </p>
      </section>
      <section aria-labelledby="system-title">
        <div className="section-heading">
          <h2 id="system-title">System overview</h2>
          <span>Local scaffold / no live data</span>
        </div>
        <div className="grid">
          {components.map(([name, status, detail], index) => (
            <article key={name}>
              <span className="index">0{index + 1}</span>
              <h3>{name}</h3>
              <p className="status">{status}</p>
              <p>{detail}</p>
            </article>
          ))}
        </div>
      </section>
      <footer>
        <span>RESEARCH & OBSERVATION</span>
        <p>Research signals, backtests and paper simulation only. No broker orders.</p>
      </footer>
    </main>
  );
}
