"""Unit tests for memory/source_health.py: the per-source liveness counter
(Phase 10)."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.memory import db as memory_db
from agent.memory.source_health import (
    SourceHealthRow,
    read_degraded,
    upsert_source_health,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _open(tmp_path):
    return memory_db.open_db(tmp_path / "state.db", create_if_absent=True)


def test_consecutive_empty_runs_climb_and_reset(tmp_path):
    conn = _open(tmp_path)
    try:
        upsert_source_health(conn, [SourceHealthRow("s", ok=False, error=None, now=NOW)])
        upsert_source_health(conn, [SourceHealthRow("s", ok=False, error="HTTP 403", now=NOW)])
        current = upsert_source_health(conn, [SourceHealthRow("s", ok=True, error=None, now=NOW)])
        assert current["s"] == 0  # reset on a healthy run
        current = upsert_source_health(conn, [SourceHealthRow("s", ok=False, error=None, now=NOW)])
        assert current["s"] == 1
    finally:
        conn.close()


def test_read_degraded_orders_worst_first(tmp_path):
    conn = _open(tmp_path)
    try:
        for _ in range(4):
            upsert_source_health(conn, [SourceHealthRow("a", ok=False, error=None, now=NOW)])
        for _ in range(3):
            upsert_source_health(conn, [SourceHealthRow("b", ok=False, error=None, now=NOW)])
        upsert_source_health(conn, [SourceHealthRow("c", ok=True, error=None, now=NOW)])
        degraded = read_degraded(conn, threshold=3)
    finally:
        conn.close()
    assert degraded == [("a", 4), ("b", 3)]


def test_last_error_round_trips(tmp_path):
    conn = _open(tmp_path)
    try:
        upsert_source_health(conn, [SourceHealthRow("s", ok=False, error="timeout", now=NOW)])
        row = conn.execute(
            "SELECT last_error, consecutive_empty_runs FROM source_health WHERE source_id='s'"
        ).fetchone()
    finally:
        conn.close()
    assert row["last_error"] == "timeout"
    assert row["consecutive_empty_runs"] == 1
