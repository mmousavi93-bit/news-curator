"""Retention pruning. The clock is a parameter, never a wall-clock read.

No wall-clock read may appear in this file, asserted by a grep test -- which is
why the forbidden call is described rather than spelled out here. A
pruner that reads the clock internally cannot be tested at its boundary -- you
would have to sleep, or freeze time globally, or trust it -- and it WILL be
trusted anyway, because pruning is the one operation whose bug looks identical
to correct behaviour: rows are gone either way.

Windows are read from `settings.yaml`, never hardcoded. They differ per table
for reasons that are load-bearing rather than arbitrary (session-2 decision 2):
signal rows feed 30-day half-life decay so 180 days of them still contribute;
speaker statements need a 30-day per-speaker baseline plus headroom before D2/D3
work at all; score history feeds trailing means and calibration. URL hashes are
the opposite case -- pure dedup, worthless after a week, and the largest table
by row count.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from agent.memory import models
from agent.memory.db import StateError

# (table, timestamp column, RetentionSettings attribute).
#
# The column differs per table on purpose. An event ages from its last update,
# not its first sighting -- a story still being updated on day 29 is not stale.
# A market reading ages from observation. A signal ages from when it was
# observed, and its DECAY separately runs from `state_ended_at`, which is a
# scoring concern and not this file's business.
#
# Table and column names are interpolated into SQL below. They come from this
# constant and nowhere else -- no caller, config file or database value ever
# reaches that interpolation.
_WINDOWS: tuple[tuple[str, str, str], ...] = (
    ("seen_urls", "first_seen_at", "url_hashes_days"),
    ("items", "collected_at", "events_days"),
    ("events", "last_updated_at", "events_days"),
    ("event_timeline", "occurred_at", "events_days"),
    ("embeddings", "created_at", "embeddings_days"),
    ("signal_events", "observed_at", "signal_events_days"),
    ("speaker_statements", "said_at", "speaker_statements_days"),
    ("risk_history", "run_at", "score_history_days"),
    ("scheduled_events", "first_seen_at", "scheduled_events_days"),
    # ARCHITECTURE.md section 11 specifies 30-day retention for market_metrics
    # and settings.yaml has no separate key for it. Bound to events_days (30)
    # rather than hardcoding 30 here, so the two move together if the owner
    # ever changes the window.
    ("market_metrics", "observed_at", "events_days"),
)

# source_health is deliberately absent: ~51 rows, one per source, upserted
# forever. Pruning it would erase exactly the history that answers "how long has
# this feed been dead?" -- which is the only question the table exists to answer.


def validate(retention) -> None:
    """Fail loudly on a settings combination that would silently corrupt state.

    `embeddings_days != events_days` leaves embeddings whose event has been
    pruned (or events with no vector). Phase 6's cosine pass would then compare
    against vectors pointing at nothing, and nothing raises -- the similarity
    numbers just quietly stop meaning what they say.
    """
    if retention.embeddings_days != retention.events_days:
        raise StateError(
            f"retention.embeddings_days ({retention.embeddings_days}) must equal "
            f"retention.events_days ({retention.events_days}); an embedding whose "
            "event has been pruned is silent corruption, not a tidy-up problem."
        )
    for _table, _column, attr in _WINDOWS:
        days = getattr(retention, attr)
        if days <= 0:
            raise StateError(f"retention.{attr} must be positive, got {days}")


def cutoff(now: datetime, days: int) -> str:
    """The boundary timestamp, as stored text. Rows STRICTLY older are deleted,
    so a row exactly `days` old is kept -- deleting on the boundary would make
    the outcome depend on sub-second timing of when the run started."""
    return models.to_utc_iso(now - timedelta(days=days))


def prune(conn: sqlite3.Connection, retention, now: datetime) -> dict[str, int]:
    """Delete aged rows from every table in `_WINDOWS`. Returns per-table counts.

    One transaction: a half-pruned database is not a partial success, it is a
    state whose retention windows disagree with each other.
    """
    validate(retention)
    deleted: dict[str, int] = {}
    conn.execute("BEGIN")
    try:
        for table, column, attr in _WINDOWS:
            boundary = cutoff(now, getattr(retention, attr))
            cur = conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (boundary,))
            deleted[table] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return deleted
