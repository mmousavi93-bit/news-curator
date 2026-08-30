"""Additive schema upgrade scripts, split out of db.py to keep that file
under the ~200-line cap (constraint 12). One script per version being
upgraded FROM. The safety rule lives in db.py's docstring: every statement
must be CREATE TABLE IF NOT EXISTS or otherwise idempotent -- never ALTER,
never DROP, never data rewrite. The moment a change needs to touch existing
rows, that is a real migration and a new owner decision, not an entry here.
"""

ADDITIVE_UPGRADES: dict[int, str] = {
    1: """
CREATE TABLE IF NOT EXISTS lead_outcomes (
    id             INTEGER PRIMARY KEY,
    lead_source_id TEXT NOT NULL,
    event_key      TEXT NOT NULL,
    outcome        TEXT NOT NULL,  -- raised | confirmed | unconfirmed
    observed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_event ON lead_outcomes (event_key);
""",
    2: """
CREATE TABLE IF NOT EXISTS delivered (
    event_key    TEXT PRIMARY KEY,
    delivered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delivered_at ON delivered (delivered_at);
""",
}
