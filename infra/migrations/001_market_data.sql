CREATE TABLE IF NOT EXISTS market_data_raw_events (
    raw_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    source_sequence INTEGER NOT NULL,
    source_order INTEGER NOT NULL,
    event_time TEXT NOT NULL,
    received_at TEXT NOT NULL,
    price_source TEXT NOT NULL,
    units TEXT NOT NULL,
    precision INTEGER NOT NULL,
    price TEXT NOT NULL,
    open_price TEXT,
    high_price TEXT,
    low_price TEXT,
    close_price TEXT,
    bid_price TEXT,
    ask_price TEXT,
    last_price TEXT,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    is_out_of_order INTEGER NOT NULL DEFAULT 0,
    is_gap INTEGER NOT NULL DEFAULT 0,
    gap_from_sequence INTEGER,
    normalized_identity_key TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_data_normalized_events (
    normalized_id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id INTEGER NOT NULL REFERENCES market_data_raw_events(raw_id) ON DELETE RESTRICT,
    schema_version INTEGER NOT NULL,
    identity_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    source_sequence INTEGER NOT NULL,
    source_order INTEGER NOT NULL,
    event_time TEXT NOT NULL,
    received_at TEXT NOT NULL,
    price_source TEXT NOT NULL,
    units TEXT NOT NULL,
    precision INTEGER NOT NULL,
    price TEXT NOT NULL,
    open_price TEXT,
    high_price TEXT,
    low_price TEXT,
    close_price TEXT,
    bid_price TEXT,
    ask_price TEXT,
    last_price TEXT,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    is_out_of_order INTEGER NOT NULL DEFAULT 0,
    is_gap INTEGER NOT NULL DEFAULT 0,
    gap_from_sequence INTEGER
);

CREATE INDEX IF NOT EXISTS idx_market_data_raw_lookup
    ON market_data_raw_events (source, symbol, source_sequence, event_time, received_at, raw_id);

CREATE INDEX IF NOT EXISTS idx_market_data_normalized_lookup
    ON market_data_normalized_events (source, symbol, event_time, received_at, source_sequence, source_order, normalized_id);

