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
    # Delivered markers only matter inside the 72h repeat window; events_days
    # is a superset, so they age out harmlessly with their events.
    ("delivered", "delivered_at", "events_days"),
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
        _prune_signal_state(conn, retention, now)
        for table, column, attr in _WINDOWS:
            boundary = cutoff(now, getattr(retention, attr))
            protected = {
                "signal_events": " AND id NOT IN (SELECT signal_event_id FROM signal_occurrences)",
                "speaker_statements": " AND id NOT IN (SELECT statement_id FROM signal_baselines "
                "WHERE statement_id IS NOT NULL) AND id NOT IN (SELECT statement_id FROM "
                "signal_calendar_rows WHERE statement_id IS NOT NULL)",
                "scheduled_events": " AND id NOT IN (SELECT schedule_id FROM signal_calendar_rows "
                "WHERE schedule_id IS NOT NULL)",
            }.get(table, "")
            cur = conn.execute(f"DELETE FROM {table} WHERE {column} < ?{protected}", (boundary,))
            deleted[table] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return deleted


def _prune_signal_state(conn, retention, now):
    """Keep open episodes, referenced evidence and novelty/checkpoints forever.

    Collector item expiry never cascades into the extraction evidence store.
    Baselines have at least 30 complete days of headroom even under shorter
    user settings. The caller owns the single pruning transaction.
    """
    from agent.memory.signal_models import pack, unpack
    from agent.pipeline.signal_contract import STATE_ENDS

    rows = {r[0]: unpack(r[1])[0] for r in conn.execute(
        "SELECT occurrence_key, payload FROM signal_occurrences")}
    needed = set()
    for record in rows.values():
        needed.update(record.candidate.prior_ids)
        needed.update(record.linked_consequences)
        for candidate, _items in record.end_evidence:
            needed.update(candidate.prior_ids)
    boundary = (now - timedelta(days=max(180, retention.signal_events_days))).date()
    for key, record in rows.items():
        active = record.event.signal_id in STATE_ENDS and record.event.state_end_date is None
        last = max(record.event.state_end_date or record.event.event_date,
                   record.last_confirmed_at)
        if not active and last < boundary and key not in needed:
            conn.execute("DELETE FROM signal_occurrences WHERE occurrence_key=?", (key,))
    baseline_boundary = (now - timedelta(days=max(45, retention.speaker_statements_days))).date()
    for key, payload in conn.execute("SELECT baseline_key, payload FROM signal_baselines").fetchall():
        (baseline, _), _sources = unpack(payload)
        if (baseline.observed < baseline_boundary and key not in needed
                and (baseline.due is None or baseline.due < now.date())):
            conn.execute("DELETE FROM signal_baselines WHERE baseline_key=?", (key,))
    conn.execute("DELETE FROM signal_raw_candidates WHERE observed_at < ?",
                 (cutoff(now, max(180, retention.signal_events_days)),))
    row = conn.execute("SELECT calendar FROM signal_history_meta WHERE singleton=1").fetchone()
    if row:
        calendar = unpack(row[0])
        for key, (observed, _) in list(calendar["statements"].items()):
            if observed.date() < baseline_boundary:
                del calendar["statements"][key]
                conn.execute("DELETE FROM signal_calendar_rows WHERE calendar_key=?", ("statements:" + key,))
        for kind in ("deadlines", "schedules"):
            for key, entry in list(calendar[kind].items()):
                if entry.cancelled and entry.observed < boundary:
                    del calendar[kind][key]
                    conn.execute("DELETE FROM signal_calendar_rows WHERE calendar_key=?", (kind + ":" + key,))
        calendar["updates"] = [e for e in calendar["updates"]
                               if not e.cancelled or e.observed >= boundary]
        conn.execute("UPDATE signal_history_meta SET calendar=? WHERE singleton=1", (pack(calendar),))
