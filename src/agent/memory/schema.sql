-- Phase 4 state schema. DDL only -- no triggers, no views, no logic.
--
-- Two classes of table live here on purpose (PHASE_4_BRIEF.md requirement 5):
--
--   LIVE IN v1   meta, items, seen_urls
--   CREATED, NOT WRITTEN   events, event_timeline, embeddings, signal_events,
--                          speaker_statements, risk_history, scheduled_events,
--                          market_metrics, source_health
--
-- The second group exists because session-3 decision 1 deferred the scorer but
-- CLAUDE.md still requires the schema stay wired, and because the alternative is
-- a schema migration against an age-encrypted state branch in Phase 7. Creating
-- an empty table costs a few hundred bytes; migrating encrypted state costs a
-- restore, a decrypt, a migrate, a re-encrypt and a force-push, unattended, on a
-- runner, with constraint 14 saying a failure there must halt the whole system.
--
-- Every table carries an ISO-8601 UTC timestamp column that retention.py prunes
-- on. The column differs per table (an event is aged from its last update, a
-- market reading from its observation) so the mapping is stated explicitly in
-- retention.py rather than inferred from a naming convention here.
--
-- TIMESTAMPS ARE UTC TEXT, ALWAYS. SQLite has no datetime type and no boolean
-- type. Storing local time would make dedup windows and retention boundaries
-- depend on a display preference (requirement 3). Booleans are INTEGER 0/1 and
-- are mapped back explicitly in models.py.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- LIVE IN v1
-- ---------------------------------------------------------------------------

-- One row per collected article that survived dedup. Phase 6 reduces these to
-- `events` and this table becomes the raw tier behind them; it is NOT the
-- long-term store ARCHITECTURE.md means by "stores events, not articles" --
-- retention prunes it on the same 30-day window as events.
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    source_id    TEXT NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    -- NULL when the source published no date at all. When date_only = 1 this is
    -- midnight UTC as a PLACEHOLDER, not an observation: the source gave a day
    -- and no time. Losing the flag makes 00:00:00Z indistinguishable from a real
    -- midnight publication, and the composer would eventually print an invented
    -- "03:30 Tehran" as fact (hard constraints 10 and 11).
    published_at TEXT,
    date_only    INTEGER NOT NULL DEFAULT 0 CHECK (date_only IN (0, 1)),
    lang         TEXT NOT NULL,
    raw_hash     TEXT NOT NULL,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_collected_at ON items (collected_at);
CREATE INDEX IF NOT EXISTS idx_items_raw_hash     ON items (raw_hash);
CREATE INDEX IF NOT EXISTS idx_items_source       ON items (source_id);

-- Dedup layers 1-3. Hash-only by design: 7-day retention at ~45,000 rows is
-- 3.6 MB (ARCHITECTURE.md section 11) and none of it has any value once the
-- window passes. title_hash is nullable -- see dedup.normalise_title: a title
-- that normalises to empty must NOT hash, or every untitled item collides.
CREATE TABLE IF NOT EXISTS seen_urls (
    url_hash      TEXT PRIMARY KEY,
    norm_url_hash TEXT NOT NULL,
    title_hash    TEXT,
    source_id     TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_norm  ON seen_urls (norm_url_hash);
CREATE INDEX IF NOT EXISTS idx_seen_title ON seen_urls (title_hash);
CREATE INDEX IF NOT EXISTS idx_seen_first ON seen_urls (first_seen_at);

-- ---------------------------------------------------------------------------
-- CREATED, NOT WRITTEN -- Phase 6
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY,
    event_key         TEXT NOT NULL UNIQUE,
    summary           TEXT NOT NULL,
    entities          TEXT,          -- JSON array
    claim_status      TEXT,          -- confirmed | likely | unconfirmed | rumour
    source_count      INTEGER NOT NULL DEFAULT 0,
    independent_count INTEGER NOT NULL DEFAULT 0,
    confidence        REAL,
    first_seen_at     TEXT NOT NULL,
    last_updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_updated ON events (last_updated_at);

CREATE TABLE IF NOT EXISTS event_timeline (
    id         INTEGER PRIMARY KEY,
    event_id   INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    occurred_at TEXT NOT NULL,
    note       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timeline_event ON event_timeline (event_id);
CREATE INDEX IF NOT EXISTS idx_timeline_at    ON event_timeline (occurred_at);

-- float16 x 384 = 768 bytes, stored as a BLOB. No vector database (decision 4
-- in ARCHITECTURE.md section 0); NumPy does brute-force cosine over ~900 rows.
CREATE TABLE IF NOT EXISTS embeddings (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER REFERENCES items (id) ON DELETE CASCADE,
    event_id   INTEGER REFERENCES events (id) ON DELETE CASCADE,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_created ON embeddings (created_at);

-- ---------------------------------------------------------------------------
-- CREATED, NOT WRITTEN -- Phases 7-9 (scoring). No Python touches these in v1.
-- ---------------------------------------------------------------------------

-- state_ended_at is load-bearing, not decoration: stateful posture signals decay
-- from the date the state ENDED, not from first report. Without that rule the
-- Feb 21 2026 backtest scores 40.1 against a 55-70 target (settings.yaml,
-- scoring.stateful_decay_from_state_end).
CREATE TABLE IF NOT EXISTS signal_events (
    id             INTEGER PRIMARY KEY,
    signal_id      TEXT NOT NULL,
    event_id       INTEGER REFERENCES events (id) ON DELETE SET NULL,
    observed_at    TEXT NOT NULL,
    state_ended_at TEXT,
    tier           INTEGER,
    source_group   TEXT,
    confidence     REAL
);
CREATE INDEX IF NOT EXISTS idx_signal_observed ON signal_events (observed_at);
CREATE INDEX IF NOT EXISTS idx_signal_id       ON signal_events (signal_id);

-- D2/D3 need a 30-day rhetoric baseline PER SPEAKER, hence the 45-day window.
CREATE TABLE IF NOT EXISTS speaker_statements (
    id         INTEGER PRIMARY KEY,
    speaker    TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    said_at    TEXT NOT NULL,
    tone       TEXT,
    text_hash  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_speaker_said ON speaker_statements (said_at);
CREATE INDEX IF NOT EXISTS idx_speaker_name ON speaker_statements (speaker);

-- Both capped and uncapped scores are stored (session-2 decision 3). Once a
-- score saturates at the 100 display cap, deltas computed on the capped value
-- are permanently zero and the WARTIME entry rule that depends on them is
-- mathematically dead. Display the capped column; compute deltas on the other.
CREATE TABLE IF NOT EXISTS risk_history (
    id              INTEGER PRIMARY KEY,
    run_at          TEXT NOT NULL,
    strat           REAL,
    tact            REAL,
    mstress         REAL,
    strat_uncapped  REAL,
    tact_uncapped   REAL,
    mstress_uncapped REAL,
    regime          TEXT,
    detail          TEXT           -- JSON per-category breakdown
);
CREATE INDEX IF NOT EXISTS idx_risk_run_at ON risk_history (run_at);

-- The ghost-meeting / countdown registry. Past entries are kept a year because
-- the warning is the NON-event: a talks round that silently fails to convene.
CREATE TABLE IF NOT EXISTS scheduled_events (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    kind          TEXT,
    due_at        TEXT,
    status        TEXT,
    first_seen_at TEXT NOT NULL,
    resolved_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_first ON scheduled_events (first_seen_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_due   ON scheduled_events (due_at);

CREATE TABLE IF NOT EXISTS market_metrics (
    id          INTEGER PRIMARY KEY,
    series      TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    value       REAL,
    fetched_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_observed ON market_metrics (observed_at);
CREATE INDEX IF NOT EXISTS idx_market_series   ON market_metrics (series);

-- One row per source, upserted, never pruned: ~51 rows forever. Pruning it would
-- erase exactly the history that answers "how long has this feed been dead?".
CREATE TABLE IF NOT EXISTS source_health (
    source_id             TEXT PRIMARY KEY,
    last_ok_at            TEXT,
    consecutive_empty_runs INTEGER NOT NULL DEFAULT 0,
    last_error            TEXT,
    updated_at            TEXT
);

-- ---------------------------------------------------------------------------
-- Phase 9 (additive, SCHEMA_VERSION 2): lead outcomes, written silently by
-- pipeline/validate.py so v1.1's earned-trust ladder has data (LEAD_HANDLING.md
-- "v1 -- ship"). No user-visible effect, no demotion -- the rows are the
-- measurement, the ladder is the later decision.
CREATE TABLE IF NOT EXISTS lead_outcomes (
    id             INTEGER PRIMARY KEY,
    lead_source_id TEXT NOT NULL,
    event_key      TEXT NOT NULL,
    outcome        TEXT NOT NULL,  -- raised | confirmed | unconfirmed
    observed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_event ON lead_outcomes (event_key);

-- ---------------------------------------------------------------------------
-- Phase 10 (additive, SCHEMA_VERSION 3): events the owner actually RECEIVED.
-- validate's anti-repetition matches only against delivered events (owner
-- decision 2026-08-30: a story he never saw -- dropped below min_score, as a
-- repeat, or by the Persian output gate -- must not suppress its own
-- follow-ups). Written by compose after the message budget is decided;
-- pruned by retention on events_days. Lead events are deliberately NOT
-- marked: their corroborated confirmation must reach the main feed.
CREATE TABLE IF NOT EXISTS delivered (
    event_key    TEXT PRIMARY KEY,
    delivered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delivered_at ON delivered (delivered_at);
