-- DQ1 sidecar quality snapshots and transport audit trail.

CREATE TABLE IF NOT EXISTS market_data_quality_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    code TEXT NOT NULL,
    completeness TEXT NOT NULL,
    config_version TEXT NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_data_quality_symbol_sequence
    ON market_data_quality_snapshots (symbol, sequence);
