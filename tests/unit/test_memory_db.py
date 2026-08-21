"""Gate items 1, 4, 5: restart survival, corruption halts, bad version halts.

Items 4 and 5 are the ones that pass by doing nothing, so each asserts TWO
things: that the process refused, AND that it left no database behind. A test
that only checks `pytest.raises` would pass against an implementation that
raised after quietly creating a fresh empty state -- which is precisely the
constraint-14 failure the tests exist to catch.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.collectors.base import Item
from agent.memory import db as memory_db
from agent.memory import models

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def make_item(n: int, *, date_only: bool = False) -> Item:
    return Item(
        source_id="reuters_gnews",
        url=f"https://example.com/story-{n}",
        title=f"Story {n}",
        body=f"Body {n}",
        published_at=datetime(2026, 8, 18, 9, 30, tzinfo=timezone.utc),
        lang="en",
        raw_hash=f"hash{n}",
        date_only=date_only,
    )


def test_initialize_creates_every_table(tmp_path: Path) -> None:
    conn = memory_db.initialize(tmp_path / "state.db")
    names = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    # The deferred scoring tables must exist NOW. Skipping them means Phase 7
    # migrates an age-encrypted state branch on a runner, unattended.
    assert {
        "meta", "items", "seen_urls", "events", "event_timeline", "embeddings",
        "signal_events", "speaker_statements", "risk_history", "scheduled_events",
        "market_metrics", "source_health",
    } <= names


def test_initialize_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    memory_db.initialize(path).close()
    with pytest.raises(memory_db.StateError):
        memory_db.initialize(path)


def test_state_survives_a_restart(tmp_path: Path) -> None:
    """Gate 1. Write, close, reopen, read back identical values."""
    path = tmp_path / "state.db"
    conn = memory_db.initialize(path)
    items = [make_item(n) for n in range(5)]
    models.insert_items(conn, items, NOW)
    conn.close()

    conn = memory_db.open_db(path)
    restored = models.read_items(conn)
    conn.close()

    assert restored == items
    assert all(i.published_at.tzinfo is not None for i in restored)
    assert all(i.published_at.utcoffset().total_seconds() == 0 for i in restored)


def test_open_refuses_to_create_implicitly(tmp_path: Path) -> None:
    missing = tmp_path / "state.db"
    with pytest.raises(memory_db.StateError):
        memory_db.open_db(missing)
    assert not missing.exists(), "an absent db may mean a failed restore -- do not create one"


def test_corruption_halts_and_creates_nothing(tmp_path: Path) -> None:
    """Gate 4. Scribble over a real database and assert BOTH properties."""
    path = tmp_path / "state.db"
    memory_db.initialize(path).close()
    with open(path, "r+b") as handle:
        handle.seek(0)
        handle.write(b"this is not a sqlite header, it is garbage" * 40)

    before = {p.name for p in tmp_path.iterdir()}
    with pytest.raises(memory_db.StateError):
        memory_db.open_db(path, create_if_absent=True)
    after = {p.name for p in tmp_path.iterdir()}

    # create_if_absent=True above is deliberate: even when the caller has asked
    # for permission to create, a CORRUPT file must not be replaced by a fresh
    # one. Permission to create is not permission to discard.
    assert after == before, f"halt left new files behind: {after - before}"


def test_truncated_to_zero_bytes_halts(tmp_path: Path) -> None:
    """A zero-byte file is a VALID empty SQLite database -- integrity_check
    passes on it. Only the missing schema_version row stands between "state was
    truncated to nothing" and "fresh state, all good"."""
    path = tmp_path / "state.db"
    memory_db.initialize(path).close()
    path.write_bytes(b"")
    with pytest.raises(memory_db.StateError, match="schema_version"):
        memory_db.open_db(path)


def test_unknown_schema_version_halts(tmp_path: Path) -> None:
    """Gate 5. Same assertion as gate 4, different cause."""
    path = tmp_path / "state.db"
    conn = memory_db.initialize(path)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(99),))
    conn.close()

    before = {p.name for p in tmp_path.iterdir()}
    with pytest.raises(memory_db.StateError, match="99"):
        memory_db.open_db(path, create_if_absent=True)
    assert {p.name for p in tmp_path.iterdir()} == before


def test_non_integer_schema_version_halts(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    conn = memory_db.initialize(path)
    conn.execute("UPDATE meta SET value = 'v1' WHERE key = 'schema_version'")
    conn.close()
    with pytest.raises(memory_db.StateError):
        memory_db.open_db(path)


def test_open_leaves_no_journal_or_wal_sidecars(tmp_path: Path) -> None:
    """WAL mode would leave -wal/-shm files that Phase 7 has to flush before
    age-encrypting, and that this suite's "no new file" assertions would trip
    over. Assert the default journal mode is still in force."""
    path = tmp_path / "state.db"
    memory_db.initialize(path).close()
    conn = memory_db.open_db(path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() != "wal"
    assert {p.name for p in tmp_path.iterdir()} == {"state.db"}


def test_halt_flags_cannot_be_switched_off() -> None:
    class Ops:
        halt_on_state_decrypt_failure = True
        halt_on_db_integrity_failure = True

    memory_db.assert_halt_flags(Ops())
    Ops.halt_on_db_integrity_failure = False
    with pytest.raises(memory_db.StateError):
        memory_db.assert_halt_flags(Ops())


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """embeddings/event_timeline cascade from events. Without the pragma on,
    pruning events would orphan rows instead of removing them."""
    path = tmp_path / "state.db"
    memory_db.initialize(path).close()
    conn = memory_db.open_db(path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_timeline (event_id, occurred_at, note) VALUES (999, ?, 'x')",
            (models.to_utc_iso(NOW),),
        )
    conn.close()
