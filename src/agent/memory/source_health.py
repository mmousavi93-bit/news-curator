"""Row model for `source_health` (Phase 10): per-source liveness, written by
the collect stage every run. The table exists since Phase 4; nothing wrote
it until now.

`consecutive_empty_runs` is the signal the digest/runbook use for
"degraded" sources: it resets on a healthy run and climbs otherwise, so
`degraded_after_empty_runs` (settings) crossing means the feed has been
dead long enough to act on -- the same number the IranWire and UKMTO cuts
used, now persisted instead of remembered.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from agent.memory.models import to_utc_iso

_UPSERT_SQL = """
INSERT INTO source_health
    (source_id, last_ok_at, consecutive_empty_runs, last_error, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(source_id) DO UPDATE SET
    last_ok_at = excluded.last_ok_at,
    consecutive_empty_runs = excluded.consecutive_empty_runs,
    last_error = excluded.last_error,
    updated_at = excluded.updated_at
"""


@dataclass(frozen=True, slots=True)
class SourceHealthRow:
    source_id: str
    ok: bool
    error: str | None
    now: datetime


def upsert_source_health(
    conn: sqlite3.Connection, rows: Iterable[SourceHealthRow]
) -> dict[str, int]:
    """Write one row per source per run. Returns
    {source_id: consecutive_empty_runs} for every source written, so the
    caller can warn on threshold crossings without a second query."""
    current: dict[str, int] = {}
    for row in rows:
        existing = conn.execute(
            "SELECT consecutive_empty_runs FROM source_health WHERE source_id = ?",
            (row.source_id,),
        ).fetchone()
        previous = int(existing[0]) if existing else 0
        consecutive = 0 if row.ok else previous + 1
        conn.execute(
            _UPSERT_SQL,
            (row.source_id,
             to_utc_iso(row.now) if row.ok else None,
             consecutive,
             row.error,
             to_utc_iso(row.now)),
        )
        current[row.source_id] = consecutive
    return current


def read_degraded(
    conn: sqlite3.Connection, threshold: int
) -> Sequence[tuple[str, int]]:
    """Sources at or above the degraded threshold, worst first."""
    sql = (
        "SELECT source_id, consecutive_empty_runs FROM source_health "
        "WHERE consecutive_empty_runs >= ? ORDER BY consecutive_empty_runs DESC"
    )
    return [(r[0], int(r[1])) for r in conn.execute(sql, (threshold,)).fetchall()]
