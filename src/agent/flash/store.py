"""The flash monitor's own SQLite state — its OWN file on its OWN
`flash-state` branch, never the pipeline's state.db (solver analysis
2026-08-30: the pipeline's state-branch push dance deletes everything on
that branch except its one encrypted DB, and two workflows force-pushing
one branch race on lost updates).

This file: DDL + open + burst CRUD + retention. History queries live in
history.py (split 2026-08-31, constraint 12).

Constraint 14 (never silently reset) shapes everything here, with one
documented deviation: the FIRST boot may create an empty DB because the
freshness gate makes empty memory safe — nothing older than 2 hours can
fire, so an empty store cannot resurrect old events. A decrypt FAILURE
on an existing DB is different: the workflow halts and sends the
system-down message; it never boots empty over a file it could not read.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

FLASH_SCHEMA_VERSION = 1
_VERSION_KEY = "flash_schema_version"
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_BURST_RETENTION_DAYS = 30  # the momentum layer's lookback horizon
_URL_RETENTION_DAYS = 7
_LOG_RETENTION_DAYS = 30


@dataclass(frozen=True, slots=True)
class BurstRow:
    id: int
    class_name: str
    signature: str
    term_bucket: str
    location_token: str
    headline: str
    first_source: str
    first_seen_at: str
    last_seen_at: str
    source_ids: tuple[str, ...]
    requires_sources: int
    alert_sent: bool
    followups_sent: int
    closed_at: str | None

    @property
    def source_count(self) -> int:
        return len(self.source_ids)


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat()


def open_flash_db(path: Path, *, create_if_absent: bool = False) -> sqlite3.Connection:
    # Existence check BEFORE connect: sqlite3.connect() creates an empty
    # file, which would make the refusal branch unreachable.
    if not path.exists() and not create_if_absent:
        raise sqlite3.OperationalError(
            f"flash DB missing at {path} and create_if_absent=False — "
            "refusing to boot empty over a missing file (constraint 14)"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    row = conn.execute("SELECT value FROM meta WHERE key = ?",
                       (_VERSION_KEY,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                     (_VERSION_KEY, str(FLASH_SCHEMA_VERSION)))
        conn.commit()
    return conn


def open_bursts(conn: sqlite3.Connection) -> list[BurstRow]:
    rows = conn.execute(
        "SELECT * FROM bursts WHERE closed_at IS NULL ORDER BY id"
    ).fetchall()
    return [_burst(r) for r in rows]


def closed_signatures(conn: sqlite3.Connection, since: str) -> dict[str, list[BurstRow]]:
    """signature -> recently closed bursts (quiet-window lookup)."""
    rows = conn.execute(
        "SELECT * FROM bursts WHERE closed_at IS NOT NULL AND closed_at >= ?",
        (since,),
    ).fetchall()
    out: dict[str, list[BurstRow]] = {}
    for r in rows:
        out.setdefault(r["signature"], []).append(_burst(r))
    return out


def _burst(row: sqlite3.Row) -> BurstRow:
    return BurstRow(
        id=row["id"], class_name=row["class_name"], signature=row["signature"],
        term_bucket=row["term_bucket"], location_token=row["location_token"],
        headline=row["headline"], first_source=row["first_source"],
        first_seen_at=row["first_seen_at"], last_seen_at=row["last_seen_at"],
        source_ids=tuple(json.loads(row["source_ids"])),
        requires_sources=row["requires_sources"],
        alert_sent=bool(row["alert_sent"]),
        followups_sent=row["followups_sent"],
        closed_at=row["closed_at"],
    )


def insert_burst(conn: sqlite3.Connection, match, now: datetime,
                 requires_sources: int) -> int:
    cursor = conn.execute(
        """INSERT INTO bursts (class_name, signature, term_bucket, location_ring,
           location_token, headline, first_source, first_seen_at, last_seen_at,
           source_ids, requires_sources)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            match.class_name, match.signature, match.term_bucket,
            match.location_ring, match.location_token,
            match.item.title, match.item.source_id,
            _iso(now), _iso(now), json.dumps([match.item.source_id]),
            requires_sources,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def add_sources(conn: sqlite3.Connection, burst_id: int,
                source_ids: set[str], now: datetime) -> None:
    row = conn.execute("SELECT source_ids FROM bursts WHERE id = ?",
                       (burst_id,)).fetchone()
    merged = sorted(set(json.loads(row["source_ids"])) | source_ids)
    conn.execute(
        "UPDATE bursts SET source_ids = ?, last_seen_at = ? WHERE id = ?",
        (json.dumps(merged), _iso(now), burst_id),
    )
    conn.commit()


def mark_alert_sent(conn: sqlite3.Connection, burst_id: int, now: datetime) -> None:
    conn.execute(
        "UPDATE bursts SET alert_sent = 1, alert_sent_at = ? WHERE id = ?",
        (_iso(now), burst_id),
    )
    conn.commit()


def mark_followup(conn: sqlite3.Connection, burst_id: int) -> None:
    conn.execute(
        "UPDATE bursts SET followups_sent = followups_sent + 1 WHERE id = ?",
        (burst_id,),
    )
    conn.commit()


def close_burst(conn: sqlite3.Connection, burst_id: int, now: datetime) -> None:
    conn.execute("UPDATE bursts SET closed_at = ? WHERE id = ?",
                 (_iso(now), burst_id))
    conn.commit()


def alerts_sent_since(conn: sqlite3.Connection, cutoff: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM bursts WHERE alert_sent = 1 AND alert_sent_at >= ?",
        (cutoff,),
    ).fetchone()
    return int(row["n"])


def log_flash(conn: sqlite3.Connection, rows: list[tuple], now: datetime) -> None:
    conn.executemany(
        """INSERT INTO flash_log (run_at, kind, class_name, signature,
           source_id, detail) VALUES (?, ?, ?, ?, ?, ?)""",
        [(_iso(now), *row) for row in rows],
    )
    conn.commit()


def prune(conn: sqlite3.Connection, now: datetime) -> tuple[int, int]:
    """(bursts_pruned, urls_pruned). Bursts keep 30 days — the momentum
    layer's lookback horizon; pruning them at 7 days made the
    de-escalation notice unreachable after a week of quiet (reviewer
    finding 2026-08-31). URLs keep 7, the log 30."""
    cutoff_burst = _iso(now - timedelta(days=_BURST_RETENTION_DAYS))
    cutoff_log = _iso(now - timedelta(days=_LOG_RETENTION_DAYS))
    cursor = conn.execute(
        "DELETE FROM bursts WHERE closed_at IS NOT NULL AND closed_at < ?",
        (cutoff_burst,))
    n_bursts = cursor.rowcount
    cursor = conn.execute(
        "DELETE FROM seen_urls WHERE first_seen_at < ?",
        (_iso(now - timedelta(days=_URL_RETENTION_DAYS)),))
    n_urls = cursor.rowcount
    conn.execute("DELETE FROM flash_log WHERE run_at < ?", (cutoff_log,))
    conn.commit()
    return n_bursts, n_urls
