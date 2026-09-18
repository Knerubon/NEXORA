-- P5-P8 snapshot persistence schema (research-only; no execution tables).

CREATE TABLE IF NOT EXISTS matrix_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    watermark_sequence BIGINT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    schema_version INT NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS structure_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    schema_version INT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS regime_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    schema_version INT NOT NULL,
    effective_time TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS research_signals (
    id BIGSERIAL PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    sequence BIGINT NOT NULL,
    occurrence_time TIMESTAMPTZ NOT NULL,
    confirmation_time TIMESTAMPTZ NOT NULL,
    decision_time TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'expired')),
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matrix_snapshots_symbol_sequence
    ON matrix_snapshots (symbol, sequence);
CREATE INDEX IF NOT EXISTS idx_structure_snapshots_symbol_sequence
    ON structure_snapshots (symbol, sequence);
CREATE INDEX IF NOT EXISTS idx_regime_snapshots_symbol_sequence
    ON regime_snapshots (symbol, sequence);
CREATE INDEX IF NOT EXISTS idx_research_signals_symbol_sequence
    ON research_signals (symbol, sequence);
