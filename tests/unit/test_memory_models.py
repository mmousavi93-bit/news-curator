"""Gate 7 (`date_only` round-trips) and requirement 3 (UTC, always).

`date_only` gets a whole test file section because it is the one field here
whose loss is UNRECOVERABLE. Every other column can be re-collected; once the
raw date string is gone, `00:00:00Z` is indistinguishable from a real midnight
publication, and the composer would print an invented "03:30 Tehran" as fact --
hard constraints 10 and 11, from a dropped INTEGER column.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.collectors.base import Item
from agent.memory import db as memory_db
from agent.memory import models

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _item(**overrides) -> Item:
    base = dict(
        source_id="state_dept_travel",
        url="https://travel.state.gov/advisory/iran",
        title="Iran Travel Advisory",
        body="Do not travel.",
        published_at=datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc),
        lang="en",
        raw_hash="abc",
        date_only=True,
    )
    base.update(overrides)
    return Item(**base)


@pytest.mark.parametrize("flag", [True, False])
def test_date_only_round_trips_in_both_states(tmp_path: Path, flag: bool) -> None:
    path = tmp_path / "state.db"
    conn = memory_db.initialize(path)
    models.insert_items(conn, [_item(date_only=flag)], NOW)
    conn.close()

    conn = memory_db.open_db(path)
    restored = models.read_items(conn)[0]
    stored = conn.execute("SELECT date_only FROM items").fetchone()[0]
    conn.close()

    assert restored.date_only is flag
    # Asserted at the column level too: SQLite has no boolean, so a `True`
    # written without mapping stores as 1 today and would still read back as
    # truthy if someone later stored the string "true" -- which would then also
    # be truthy while being a different value. Pin the storage type.
    assert stored == (1 if flag else 0)
    assert isinstance(stored, int)


def test_date_only_default_is_false_not_null(tmp_path: Path) -> None:
    """A NULL here would read back as neither True nor False and the composer's
    "was a time actually given?" question would have no answer."""
    conn = memory_db.initialize(tmp_path / "state.db")
    conn.execute(
        "INSERT INTO items (source_id, url, title, body, lang, raw_hash, collected_at) "
        "VALUES ('s', 'u', 't', 'b', 'en', 'h', ?)",
        (models.to_utc_iso(NOW),),
    )
    row = conn.execute("SELECT date_only FROM items").fetchone()
    conn.close()
    assert row[0] == 0


def test_missing_published_at_survives_as_none(tmp_path: Path) -> None:
    conn = memory_db.initialize(tmp_path / "state.db")
    models.insert_items(conn, [_item(published_at=None, date_only=False)], NOW)
    restored = models.read_items(conn)[0]
    conn.close()
    assert restored.published_at is None


def test_non_utc_input_is_converted_not_stored_as_local() -> None:
    """Tehran is UTC+3:30. 09:00 there is 05:30 UTC, and if the offset were
    stored verbatim every retention boundary and dedup window would silently
    inherit a display preference (requirement 3)."""
    tehran = timezone(timedelta(hours=3, minutes=30))
    stored = models.to_utc_iso(datetime(2026, 8, 19, 9, 0, tzinfo=tehran))
    assert stored == "2026-08-19T05:30:00+00:00"
    assert models.from_utc_iso(stored) == datetime(2026, 8, 19, 5, 30, tzinfo=timezone.utc)


def test_naive_datetime_is_rejected() -> None:
    """Rejected, not assumed to be UTC. Assuming turns an upstream collector
    change into a permanent, undetectable offset error in stored state."""
    with pytest.raises(ValueError):
        models.to_utc_iso(datetime(2026, 8, 19, 9, 0))


def test_stored_timestamp_without_offset_is_rejected_on_read() -> None:
    with pytest.raises(ValueError):
        models.from_utc_iso("2026-08-19T09:00:00")


def test_read_order_is_insertion_order(tmp_path: Path) -> None:
    """All items in one batch share a collected_at, so ordering on it would be
    non-deterministic and this assertion would flap rather than fail."""
    conn = memory_db.initialize(tmp_path / "state.db")
    titles = ["third", "first", "second"]
    models.insert_items(
        conn, [_item(title=t, url=f"https://x/{t}") for t in titles], NOW
    )
    assert [i.title for i in models.read_items(conn)] == titles
    assert models.count_items(conn) == 3
    conn.close()


def test_read_items_filters_by_source(tmp_path: Path) -> None:
    conn = memory_db.initialize(tmp_path / "state.db")
    models.insert_items(
        conn,
        [_item(source_id="a", url="https://x/1"), _item(source_id="b", url="https://x/2")],
        NOW,
    )
    assert len(models.read_items(conn, source_id="a")) == 1
    assert len(models.read_items(conn, limit=1)) == 1
    conn.close()
