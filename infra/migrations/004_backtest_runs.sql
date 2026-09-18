-- P10 backtest run and comparison persistence schema.

CREATE TABLE IF NOT EXISTS backtest_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    dataset_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    dataset_hash TEXT NOT NULL,
    assumptions_hash TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_dataset ON backtest_runs (dataset_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_mode ON backtest_runs (mode);
