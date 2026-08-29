"""Row <-> dataclass mapping for `events`. Split out of models.py to keep
both files under the ~200-line cap (constraint 12): models.py owns `items`,
this owns `events` -- the post-understand store, written by Phase 6's
understand stage.

The events table was CREATED by Phase 4 and written by nothing until now.
claim_status / independent_count / confidence are Phase 9's job; Phase 6
writes 'unconfirmed', 0 and NULL -- honest placeholders, not guesses.

Same UTC discipline as models.py: aware datetimes only, naive input raises.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from agent.memory.models import from_utc_iso, to_utc_iso

_COLUMNS = (
    "event_key",
    "summary",
    "entities",
    "claim_status",
    "source_count",
    "independent_count",
    "confidence",
    "first_seen_at",
    "last_updated_at",
)

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO events ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)

_SELECT_SQL = f"SELECT {', '.join(_COLUMNS)} FROM events"


@dataclass(frozen=True, slots=True)
class Event:
    """One understood story cluster, ready for validation (Phase 9) and
    composition (Phase 8). `event_key` is the cluster key: sha256 of sorted
    member urls -- the same story on a re-run maps to the same event, and
    the UNIQUE constraint makes a duplicate write a no-op."""

    event_key: str
    summary: str
    # The LLM's own informative one-liner (owner contract 2026-08-29: the
    # reader decides from the headline alone). In-memory only, like
    # category: not persisted, the events table predates both.
    headline: str = ""
    entities: tuple[str, ...] = ()
    # Digest-ranking category (owner decision 2026-08-29): military |
    # security | politics | economy | other. In-memory only -- NOT
    # persisted: the events table predates it and additive schema changes
    # are frozen at CREATE IF NOT EXISTS. Persist when Phase 11's scorer
    # arrives, if it still matters then.
    category: str = "other"
    claim_status: str = "unconfirmed"
    source_count: int = 0
    independent_count: int = 0
    confidence: float | None = None
    first_seen_at: datetime | None = None
    last_updated_at: datetime | None = None


def event_to_row(event: Event) -> tuple:
    return (
        event.event_key,
        event.summary,
        json.dumps(list(event.entities)),
        event.claim_status,
        event.source_count,
        event.independent_count,
        event.confidence,
        to_utc_iso(event.first_seen_at),
        to_utc_iso(event.last_updated_at),
    )


def row_to_event(row: Sequence) -> Event:
    entities_raw = row[2]
    try:
        entities = tuple(json.loads(entities_raw)) if entities_raw else ()
    except ValueError:
        entities = ()  # corrupt JSON must not kill a read of state
    confidence = None if row[6] is None else float(row[6])
    return Event(
        event_key=row[0],
        summary=row[1],
        entities=entities,
        claim_status=row[3],
        source_count=int(row[4]),
        independent_count=int(row[5]),
        confidence=confidence,
        first_seen_at=from_utc_iso(row[7]),
        last_updated_at=from_utc_iso(row[8]),
    )


def update_validation(conn: sqlite3.Connection, events: Iterable[Event]) -> None:
    """Phase 9: write claim_status/independent_count back for validated
    events. The understand stage inserts with 'unconfirmed'/0 -- the
    validate stage is the writer of truth."""
    rows = [
        (e.claim_status, e.independent_count, e.event_key) for e in events
    ]
    if rows:
        conn.executemany(
            "UPDATE events SET claim_status = ?, independent_count = ? "
            "WHERE event_key = ?",
            rows,
        )


def insert_events(conn: sqlite3.Connection, events: Iterable[Event]) -> int:
    """INSERT OR IGNORE: the same cluster key twice in one run is a no-op,
    never a crash (gate 7). Returns rows actually written. No transaction
    management -- the caller owns it."""
    rows = [event_to_row(event) for event in events]
    if not rows:
        return 0
    cursor = conn.executemany(_INSERT_SQL, rows)
    return int(cursor.rowcount)


def read_events(
    conn: sqlite3.Connection, *, limit: int | None = None
) -> list[Event]:
    sql = _SELECT_SQL + " ORDER BY last_updated_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        return [row_to_event(r) for r in conn.execute(sql, (limit,)).fetchall()]
    return [row_to_event(r) for r in conn.execute(sql).fetchall()]


def read_recent_events(
    conn: sqlite3.Connection, *, hours: int, now: datetime
) -> list[Event]:
    """Events whose last_updated_at falls within `hours` of the injected
    `now` (the clock-read rule: callers pass ctx.now, nothing here reads
    the wall clock). Used by the validate stage's anti-repetition
    matching."""
    from datetime import timedelta

    cutoff = (now - timedelta(hours=hours)).isoformat()
    sql = _SELECT_SQL + " WHERE last_updated_at >= ? ORDER BY last_updated_at DESC"
    return [row_to_event(r) for r in conn.execute(sql, (cutoff,)).fetchall()]
