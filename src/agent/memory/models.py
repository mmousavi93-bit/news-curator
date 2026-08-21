"""Row <-> dataclass mapping for `items`. Nothing else in this package writes
SQL against that table.

Two rules this file exists to enforce, both of which are silent data corruption
if broken rather than loud failures:

**UTC, always.** SQLite has no datetime type, so a timestamp is text and the
text is meaningless without an offset. Every value stored here is ISO-8601 with
an explicit `+00:00`, converted with `astimezone` on the way in. A naive
datetime is REJECTED rather than assumed to be UTC -- assuming is how a
collector bug becomes a permanent, unfixable one-hour error in the dedup window.
Local-time conversion lives in `collectors/dates.py` and is display-only; no
local zone may be named anywhere under `memory/` -- requirement 3, asserted by a
grep test, which is why this paragraph does not name one either.

**`date_only` round-trips.** SQLite has no boolean either, so it is INTEGER 0/1
and is mapped back explicitly. If this column is dropped or silently defaulted,
the information is unrecoverable: once the raw date string is gone, `00:00:00Z`
is indistinguishable from a real midnight publication and the composer will
eventually print an invented local clock time as fact (constraints 10 and 11).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Sequence

from agent.collectors.base import Item

_COLUMNS = (
    "source_id",
    "url",
    "title",
    "body",
    "published_at",
    "date_only",
    "lang",
    "raw_hash",
    "collected_at",
)

_INSERT_SQL = (
    f"INSERT INTO items ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)

_SELECT_SQL = f"SELECT {', '.join(_COLUMNS)} FROM items"


def to_utc_iso(dt: datetime | None) -> str | None:
    """Aware datetime -> ISO-8601 UTC text. Naive input raises.

    The raise is the feature. Every collector in this codebase already returns
    tz-aware UTC (`collectors/dates.py` normalises before returning), so a naive
    value arriving here means something upstream changed, and the cheapest place
    to discover that is the moment before it is written to permanent state.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError(
            "refusing to store a naive datetime: an offset-less timestamp in state "
            "cannot be interpreted later. Attach timezone.utc upstream."
        )
    return dt.astimezone(timezone.utc).isoformat()


def from_utc_iso(text: str | None) -> datetime | None:
    """ISO-8601 text -> aware UTC datetime. The inverse of `to_utc_iso`."""
    if text is None:
        return None
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"stored timestamp has no offset: {text!r}")
    return parsed.astimezone(timezone.utc)


def item_to_row(item: Item, collected_at: datetime) -> tuple:
    return (
        item.source_id,
        item.url,
        item.title,
        item.body,
        to_utc_iso(item.published_at),
        1 if item.date_only else 0,
        item.lang,
        item.raw_hash,
        to_utc_iso(collected_at),
    )


def row_to_item(row: Sequence) -> Item:
    """`date_only` is compared against 1 rather than passed through `bool()`:
    the CHECK constraint in schema.sql already restricts it to 0/1, and an
    explicit comparison documents that the storage type is an integer, not a
    boolean SQLite is pretending to have."""
    return Item(
        source_id=row[0],
        url=row[1],
        title=row[2],
        body=row[3],
        published_at=from_utc_iso(row[4]),
        lang=row[6],
        raw_hash=row[7],
        date_only=row[5] == 1,
    )


def insert_items(conn: sqlite3.Connection, items: Iterable[Item], collected_at: datetime) -> int:
    """Append `items`. Returns the count written. No transaction management --
    the caller owns the transaction so the items write and the seen_urls write
    commit together or not at all (see dedup.store_new)."""
    rows = [item_to_row(item, collected_at) for item in items]
    if not rows:
        return 0
    conn.executemany(_INSERT_SQL, rows)
    return len(rows)


def read_items(
    conn: sqlite3.Connection, *, source_id: str | None = None, limit: int | None = None
) -> list[Item]:
    """Read back in insertion order. Ordered by `id`, not by `collected_at`:
    a batch shares one collected_at value, so ordering on it is
    non-deterministic and a restart-survival test would flap."""
    sql = _SELECT_SQL
    params: list = []
    if source_id is not None:
        sql += " WHERE source_id = ?"
        params.append(source_id)
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [row_to_item(row) for row in conn.execute(sql, params).fetchall()]


def count_items(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
