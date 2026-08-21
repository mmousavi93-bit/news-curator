"""Gate 6: retention prunes at the boundary, per table, with an injected clock.

Every test here passes `now` in. Nothing sleeps and nothing freezes global time.
That is not a stylistic preference: pruning is the one operation whose bug looks
identical to correct behaviour from the outside -- rows are gone either way --
so if the boundary cannot be asserted cheaply it will simply be trusted.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.memory import db as memory_db
from agent.memory import models, retention
from agent.settings_schema import RetentionSettings

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

# The real values from config/settings.yaml, session-2 decision 2.
SETTINGS = RetentionSettings(
    url_hashes_days=7,
    events_days=30,
    embeddings_days=30,
    signal_events_days=180,
    speaker_statements_days=45,
    score_history_days=365,
    scheduled_events_days=365,
)

# table -> (INSERT sql, the timestamp column's position in the values tuple)
_INSERTS = {
    "seen_urls": "INSERT INTO seen_urls (url_hash, norm_url_hash, source_id, first_seen_at)"
                 " VALUES (?, 'n', 's', ?)",
    "items": "INSERT INTO items (source_id, url, title, body, lang, raw_hash, collected_at)"
             " VALUES ('s', ?, 't', 'b', 'en', 'h', ?)",
    "events": "INSERT INTO events (event_key, summary, first_seen_at, last_updated_at)"
              " VALUES (?, 's', '2026-01-01T00:00:00+00:00', ?)",
    "signal_events": "INSERT INTO signal_events (signal_id, observed_at) VALUES (?, ?)",
    "speaker_statements": "INSERT INTO speaker_statements (speaker, source_id, said_at,"
                          " text_hash) VALUES (?, 's', ?, 'h')",
    "risk_history": "INSERT INTO risk_history (regime, run_at) VALUES (?, ?)",
    "scheduled_events": "INSERT INTO scheduled_events (title, first_seen_at) VALUES (?, ?)",
    "market_metrics": "INSERT INTO market_metrics (series, observed_at, fetched_at)"
                      " VALUES (?, ?, '2026-08-19T00:00:00+00:00')",
}


@pytest.fixture
def conn(tmp_path: Path):
    connection = memory_db.initialize(tmp_path / "state.db")
    yield connection
    connection.close()


def seed(connection, table: str, key: str, age_days: float) -> None:
    stamp = models.to_utc_iso(NOW - timedelta(days=age_days))
    connection.execute(_INSERTS[table], (key, stamp))


def count(connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


@pytest.mark.parametrize(
    "table,window_days",
    [
        ("seen_urls", 7),
        ("items", 30),
        ("events", 30),
        ("signal_events", 180),
        ("speaker_statements", 45),
        ("risk_history", 365),
        ("scheduled_events", 365),
        ("market_metrics", 30),
    ],
)
def test_boundary_per_table(conn, table: str, window_days: int) -> None:
    """One day inside the window is kept, one day outside is gone. Asserted per
    table because the windows genuinely differ -- a pruner that applied the
    7-day URL window to signal_events would destroy 173 days of decay history
    and every score afterwards would be quietly too low."""
    seed(conn, table, "keep", window_days - 1)
    seed(conn, table, "drop", window_days + 1)
    assert count(conn, table) == 2

    deleted = retention.prune(conn, SETTINGS, NOW)

    assert count(conn, table) == 1
    assert deleted[table] == 1


def test_row_exactly_on_the_boundary_is_kept(conn) -> None:
    """Strictly-older is deleted. Deleting ON the boundary would make the
    outcome depend on sub-second timing of when the run happened to start."""
    seed(conn, "seen_urls", "exact", 7)
    retention.prune(conn, SETTINGS, NOW)
    assert count(conn, "seen_urls") == 1


def test_prune_is_a_no_op_on_a_fresh_database(conn) -> None:
    assert sum(retention.prune(conn, SETTINGS, NOW).values()) == 0


def test_source_health_is_never_pruned(conn) -> None:
    """~51 rows, one per source. Pruning it erases the only history that answers
    "how long has this feed been dead?"."""
    conn.execute(
        "INSERT INTO source_health (source_id, last_ok_at, updated_at) VALUES ('x', ?, ?)",
        (models.to_utc_iso(NOW - timedelta(days=900)),) * 2,
    )
    retention.prune(conn, SETTINGS, NOW)
    assert count(conn, "source_health") == 1


def test_embeddings_window_must_equal_events_window() -> None:
    """An embedding whose event has been pruned is silent corruption: Phase 6's
    cosine pass compares against a vector pointing at nothing and raises no
    error, the similarity numbers just stop meaning what they say."""
    mismatched = replace(SETTINGS, embeddings_days=14)
    with pytest.raises(memory_db.StateError, match="embeddings_days"):
        retention.validate(mismatched)


def test_zero_or_negative_window_is_rejected() -> None:
    zeroed = replace(SETTINGS, url_hashes_days=0)
    with pytest.raises(memory_db.StateError):
        retention.validate(zeroed)


def test_prune_validates_before_deleting_anything(conn) -> None:
    """A bad settings combination must not half-prune first."""
    seed(conn, "seen_urls", "old", 400)
    bad = replace(SETTINGS, embeddings_days=1)
    with pytest.raises(memory_db.StateError):
        retention.prune(conn, bad, NOW)
    assert count(conn, "seen_urls") == 1


def test_the_windows_match_the_shipped_settings_file() -> None:
    """The fixture above is not an independent opinion about retention -- it is
    a copy of settings.yaml, and this asserts the copy has not drifted."""
    from agent.config import load_all

    shipped = load_all().settings.retention
    assert shipped == SETTINGS


def test_pruning_an_event_cascades_to_its_children(conn) -> None:
    old = models.to_utc_iso(NOW - timedelta(days=90))
    conn.execute(
        "INSERT INTO events (id, event_key, summary, first_seen_at, last_updated_at)"
        " VALUES (1, 'k', 's', ?, ?)", (old, old),
    )
    conn.execute(
        "INSERT INTO event_timeline (event_id, occurred_at, note) VALUES (1, ?, 'n')", (old,)
    )
    conn.execute(
        "INSERT INTO embeddings (event_id, dim, vector, created_at)"
        " VALUES (1, 384, X'00', ?)", (old,),
    )
    retention.prune(conn, SETTINGS, NOW)
    assert count(conn, "events") == 0
    assert count(conn, "event_timeline") == 0
    assert count(conn, "embeddings") == 0
