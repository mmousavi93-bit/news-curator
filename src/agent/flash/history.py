"""History queries for the flash DB — seen-urls, meta.
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


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))
    conn.commit()
