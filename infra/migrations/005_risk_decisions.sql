-- P11 risk decisions and state audit storage.

CREATE TABLE IF NOT EXISTS risk_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    proposal_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('allow', 'reject')),
    policy_version TEXT NOT NULL,
    effective_time TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_state_snapshots (
    id BIGSERIAL PRIMARY KEY,
    policy_version TEXT NOT NULL,
    trading_day TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_risk_decisions_proposal_id ON risk_decisions (proposal_id);
CREATE INDEX IF NOT EXISTS idx_risk_state_policy_version ON risk_state_snapshots (policy_version);
