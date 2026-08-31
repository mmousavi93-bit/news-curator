-- Flash monitor state schema. DDL only -- no triggers, no views, no
-- logic. Lives beside store.py the same way memory/schema.sql does.
-- TIMESTAMPS ARE UTC TEXT, ALWAYS (repo convention).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_urls (
    url_hash      TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bursts (
    id             INTEGER PRIMARY KEY,
    class_name     TEXT NOT NULL,
    signature      TEXT NOT NULL,
    term_bucket    TEXT NOT NULL,
    location_ring  TEXT NOT NULL,
    location_token TEXT NOT NULL,
    headline       TEXT NOT NULL,
    first_source   TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    source_ids     TEXT NOT NULL,     -- JSON list, distinct
    requires_sources INTEGER NOT NULL DEFAULT 0,  -- quiet/momentum-held
    alert_sent     INTEGER NOT NULL DEFAULT 0,
    alert_sent_at  TEXT,
    followups_sent INTEGER NOT NULL DEFAULT 0,
    closed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_bursts_open ON bursts (signature, closed_at);
CREATE TABLE IF NOT EXISTS flash_log (
    id             INTEGER PRIMARY KEY,
    run_at         TEXT NOT NULL,
    kind           TEXT NOT NULL,  -- fired|followup|deferred|quiet_held|killed|stale
    class_name     TEXT,
    signature      TEXT,
    source_id      TEXT,
    detail         TEXT
);
