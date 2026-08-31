"""History queries for the flash DB — seen-urls, momentum series, meta.
Split out of store.py 2026-08-31 (reviewer finding: store.py at 311
lines did two jobs — DDL/CRUD and history reads; constraint 12)."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def known_urls(conn: sqlite3.Connection, hashes: set[str]) -> set[str]:
    if not hashes:
        return set()
    placeholders = ",".join("?" for _ in hashes)
    rows = conn.execute(
        f"SELECT url_hash FROM seen_urls WHERE url_hash IN ({placeholders})",
        tuple(hashes),
    ).fetchall()
    return {r["url_hash"] for r in rows}


def mark_seen(conn: sqlite3.Connection, hashes: set[str], now: datetime) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO seen_urls (url_hash, first_seen_at) VALUES (?, ?)",
        [(h, now.astimezone().isoformat()) for h in hashes],
    )
    conn.commit()


def first_seen_since(conn: sqlite3.Connection, class_name: str,
                     bucket: str | None, since: str,
                     sent_only: bool = False) -> list[str]:
    """First-seen timestamps of a class's bursts (optionally one bucket)
    since `since`. `sent_only` restricts to bursts the owner was actually
    alerted about — momentum must never build a pattern from bursts that
    never reached the channel (reviewer finding: phantom de-escalation)."""
    sent_clause = " AND alert_sent = 1" if sent_only else ""
    if bucket is None:
        rows = conn.execute(
            f"SELECT first_seen_at FROM bursts WHERE class_name = ? AND "
            f"first_seen_at >= ?{sent_clause}",
            (class_name, since),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT first_seen_at FROM bursts WHERE class_name = ? AND "
            f"term_bucket = ? AND first_seen_at >= ?{sent_clause}",
            (class_name, bucket, since),
        ).fetchall()
    return [r["first_seen_at"] for r in rows]


def location_tokens_since(conn: sqlite3.Connection, class_name: str,
                          bucket: str, since: str) -> set[str]:
    """Distinct ALERTED location tokens a bucket fired on since `since` —
    novelty detection (a new target domain restores escalation weight)."""
    rows = conn.execute(
        "SELECT DISTINCT location_token FROM bursts WHERE class_name = ? AND "
        "term_bucket = ? AND first_seen_at >= ? AND alert_sent = 1",
        (class_name, bucket, since),
    ).fetchall()
    return {r["location_token"] for r in rows}


def recent_distinct_buckets(conn: sqlite3.Connection, class_name: str,
                            since: str, exclude_bucket: str) -> set[str]:
    """Distinct term buckets of a class with bursts first seen since
    `since`, excluding one bucket. Powers the convergence note
    (WAR_SIGNALS_PAPER: three categories moving within 72h is a war)."""
    rows = conn.execute(
        "SELECT DISTINCT term_bucket FROM bursts WHERE class_name = ? AND "
        "term_bucket != ? AND first_seen_at >= ?",
        (class_name, exclude_bucket, since),
    ).fetchall()
    return {r["term_bucket"] for r in rows}


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))
    conn.commit()
