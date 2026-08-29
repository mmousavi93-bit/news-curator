"""Unit tests for memory/event_models.py: the events round-trip, the
event_key UNIQUE constraint, and the UTC discipline shared with models.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.memory import db as memory_db
from agent.memory.event_models import Event, insert_events, read_events

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _open(tmp_path):
    return memory_db.open_db(tmp_path / "state.db", create_if_absent=True)


def _event(key="k" * 16, **overrides) -> Event:
    kwargs = dict(
        event_key=key,
        summary="summary",
        entities=("Iran", "Hezbollah"),
        source_count=3,
        first_seen_at=T0,
        last_updated_at=T0,
    )
    kwargs.update(overrides)
    return Event(**kwargs)


def test_round_trip_preserves_fields(tmp_path):
    conn = _open(tmp_path)
    try:
        insert_events(conn, [_event()])
        events = read_events(conn)
    finally:
        conn.close()
    assert len(events) == 1
    event = events[0]
    assert event.event_key == "k" * 16
    assert event.summary == "summary"
    assert event.entities == ("Iran", "Hezbollah")
    assert event.claim_status == "unconfirmed"
    assert event.source_count == 3
    assert event.independent_count == 0
    assert event.confidence is None
    assert event.first_seen_at == T0
    assert event.last_updated_at == T0


def test_duplicate_event_key_is_ignored_not_crashed(tmp_path):
    conn = _open(tmp_path)
    try:
        first = insert_events(conn, [_event()])
        second = insert_events(conn, [_event()])  # same key
        events = read_events(conn)
    finally:
        conn.close()
    assert first == 1
    assert second == 0  # INSERT OR IGNORE: no-op, not a crash (gate 7)
    assert len(events) == 1


def test_two_distinct_events_both_persist(tmp_path):
    conn = _open(tmp_path)
    try:
        insert_events(conn, [_event(key="a" * 16), _event(key="b" * 16)])
        events = read_events(conn)
    finally:
        conn.close()
    assert len(events) == 2


def test_naive_datetime_is_rejected_loudly(tmp_path):
    conn = _open(tmp_path)
    try:
        with pytest.raises(ValueError, match="naive"):
            insert_events(conn, [_event(first_seen_at=datetime(2026, 8, 20, 12, 0))])
    finally:
        conn.close()


def test_corrupt_entities_json_reads_as_empty_tuple(tmp_path):
    conn = _open(tmp_path)
    try:
        conn.execute(
            "INSERT INTO events (event_key, summary, entities, claim_status, "
            "source_count, independent_count, first_seen_at, last_updated_at) "
            "VALUES (?, ?, ?, 'unconfirmed', 1, 0, ?, ?)",
            ("z" * 16, "s", "{not json", T0.isoformat(), T0.isoformat()),
        )
        events = read_events(conn)
    finally:
        conn.close()
    assert events[0].entities == ()


def test_read_events_orders_by_last_updated_desc(tmp_path):
    conn = _open(tmp_path)
    try:
        insert_events(conn, [
            _event(key="a" * 16, last_updated_at=T0),
            _event(key="b" * 16, last_updated_at=datetime(2026, 8, 21, tzinfo=timezone.utc)),
        ])
        events = read_events(conn)
    finally:
        conn.close()
    assert events[0].event_key == "b" * 16
