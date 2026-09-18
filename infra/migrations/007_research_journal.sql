-- Additive durable event/artifact journal. Existing historical tables are preserved.
-- Apply inside a transaction; rollback drops only this new table after exporting its data.
CREATE TABLE IF NOT EXISTS research_journal (
    sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stream TEXT NOT NULL,
    event_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (stream, event_key)
);
