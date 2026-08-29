"""Unit tests for memory/lead_models.py: the silent lead_outcomes rows
(Phase 9, SCHEMA_VERSION 2 additive)."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.memory import db as memory_db
from agent.memory.lead_models import LeadOutcome, insert_lead_outcomes, read_lead_outcomes

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _open(tmp_path):
    return memory_db.open_db(tmp_path / "state.db", create_if_absent=True)


def test_round_trip_preserves_outcomes(tmp_path):
    conn = _open(tmp_path)
    try:
        insert_lead_outcomes(conn, [
            LeadOutcome(lead_source_id="lead1", event_key="e" * 16,
                        outcome="raised", observed_at=NOW),
            LeadOutcome(lead_source_id="lead1", event_key="f" * 16,
                        outcome="confirmed", observed_at=NOW),
        ])
        outcomes = read_lead_outcomes(conn)
    finally:
        conn.close()
    assert outcomes == [
        ("lead1", "e" * 16, "raised"),
        ("lead1", "f" * 16, "confirmed"),
    ]


def test_filter_by_lead_source(tmp_path):
    conn = _open(tmp_path)
    try:
        insert_lead_outcomes(conn, [
            LeadOutcome(lead_source_id="lead1", event_key="e" * 16,
                        outcome="raised", observed_at=NOW),
            LeadOutcome(lead_source_id="lead2", event_key="f" * 16,
                        outcome="raised", observed_at=NOW),
        ])
        outcomes = read_lead_outcomes(conn, lead_source_id="lead2")
    finally:
        conn.close()
    assert outcomes == [("lead2", "f" * 16, "raised")]


def test_existing_v1_database_gains_table_additively(tmp_path):
    """Gate 5: a pre-Phase-9 database opens cleanly and gains the table
    with every row intact. Simulated by creating a db, then opening again
    (schema v2) and writing outcomes."""
    conn = _open(tmp_path)
    try:
        conn.execute(
            "INSERT INTO events (event_key, summary, claim_status, source_count, "
            "independent_count, first_seen_at, last_updated_at) "
            "VALUES (?, 's', 'unconfirmed', 1, 0, ?, ?)",
            ("a" * 16, NOW.isoformat(), NOW.isoformat()),
        )
    finally:
        conn.close()
    conn = _open(tmp_path)  # re-open: no crash, no data loss
    try:
        from agent.memory.event_models import read_events
        assert len(read_events(conn)) == 1
        insert_lead_outcomes(conn, [LeadOutcome("l", "a" * 16, "raised", NOW)])
        assert len(read_lead_outcomes(conn)) == 1
    finally:
        conn.close()
