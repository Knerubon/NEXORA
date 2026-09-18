-- P12 paper trading persistence contract (local simulation only)

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    proposal_id TEXT NOT NULL UNIQUE,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    requested_size NUMERIC NOT NULL,
    approved_size NUMERIC NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('filled', 'rejected')),
    reason TEXT NOT NULL,
    reason_codes JSONB NOT NULL,
    policy_version TEXT NOT NULL,
    source_refs JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES paper_orders(order_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    size NUMERIC NOT NULL,
    price NUMERIC NOT NULL,
    fee NUMERIC NOT NULL,
    realized_pnl NUMERIC NOT NULL,
    filled_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_ledger_entries (
    entry_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('order_rejected', 'fill', 'checkpoint')),
    proposal_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    balance_after NUMERIC NOT NULL,
    detail TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'kill_switch')),
    cash NUMERIC NOT NULL,
    realized_pnl NUMERIC NOT NULL,
    seen_proposals JSONB NOT NULL,
    positions JSONB NOT NULL,
    orders JSONB NOT NULL,
    fills JSONB NOT NULL,
    ledger JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
