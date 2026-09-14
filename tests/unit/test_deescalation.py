"""De-escalation notice (session 9z): config shape, escalation detection, and
the stateful notice/record/mark logic. Deterministic, zero LLM, in-memory
sqlite only -- no network, no keys.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agent.config import ConfigError
from agent.memory.event_models import Event
from agent.pipeline.deescalation import (
    DeescalationConfig,
    is_escalation,
    mark_notice_sent,
    maybe_deescalation_notice,
    record_escalation_day,
    validate_deescalation,
)
from agent.pipeline.relevance import RelevanceConfig
from agent.util.jalali import to_persian_digits

# 09:00 UTC == 12:30 Tehran, same calendar day (2026-09-14).
NOW = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
LABELS = {"deescalation": "DEESC {days}"}


def _rel() -> RelevanceConfig:
    return RelevanceConfig(
        weights={"strategic": 4.0, "iran_direct": 8.0},
        keywords={"strategic": ("جنگ",), "iran_direct": ("ایران",)},
        min_relevance=3.0,
    )


def _cfg(**kw) -> DeescalationConfig:
    defaults = dict(
        enabled=True, streak_days=3, window_days=30, quiet_days=3, cooldown_days=7
    )
    defaults.update(kw)
    return DeescalationConfig(**defaults)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return conn


def _set_state(conn: sqlite3.Connection, state: dict) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        ("deescalation_v1", json.dumps(state)),
    )


def _get_state(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'deescalation_v1'"
    ).fetchone()
    return json.loads(row[0]) if row else {}


# --- validate_deescalation ---


def test_validate_accepts_full_shape():
    cfg = validate_deescalation(
        {
            "enabled": True,
            "streak_days": 3,
            "window_days": 30,
            "quiet_days": 3,
            "cooldown_days": 7,
        }
    )
    assert cfg == _cfg()


def test_validate_reports_every_problem_once():
    with pytest.raises(ConfigError) as exc_info:
        validate_deescalation({"enabled": "yes", "streak_days": 0, "bogus": 1})
    message = str(exc_info.value)
    assert "'enabled' must be a boolean" in message
    assert "'streak_days' must be a positive integer" in message
    assert "unknown key(s)" in message and "bogus" in message


def test_validate_rejects_non_mapping():
    with pytest.raises(ConfigError, match="expected a mapping"):
        validate_deescalation([])


# --- is_escalation ---


def test_is_escalation_category_gate():
    rel = _rel()
    assert not is_escalation(
        Event(event_key="k", summary="s", category="politics", headline="جنگ"), rel
    )
    assert not is_escalation(
        Event(event_key="k", summary="s", category="economy", headline="جنگ"), rel
    )


def test_is_escalation_requires_strategic_tier():
    rel = _rel()
    below = Event(event_key="k", summary="s", category="military", headline="اخبار روزمره")
    assert not is_escalation(below, rel)
    strategic = Event(event_key="k", summary="s", category="military", headline="جنگ در منطقه")
    assert is_escalation(strategic, rel)


def test_is_escalation_no_config_falls_back_to_category():
    assert is_escalation(Event(event_key="k", summary="s", category="security"), None)
    assert not is_escalation(Event(event_key="k", summary="s", category="other"), None)


# --- maybe_deescalation_notice ---


def test_notice_disabled_missing_conn_or_config_returns_none():
    conn = _conn()
    assert maybe_deescalation_notice(None, NOW, _cfg(), LABELS) is None
    assert maybe_deescalation_notice(conn, NOW, None, LABELS) is None
    assert maybe_deescalation_notice(conn, NOW, _cfg(enabled=False), LABELS) is None


def test_notice_no_history_returns_none():
    assert maybe_deescalation_notice(_conn(), NOW, _cfg(), LABELS) is None


def test_notice_fires_after_streak_and_quiet():
    conn = _conn()
    _set_state(conn, {"days": ["2026-09-08", "2026-09-09", "2026-09-10"]})
    notice = maybe_deescalation_notice(conn, NOW, _cfg(), LABELS)
    # quiet_days == (14 - 10) == 4
    assert notice == LABELS["deescalation"].format(days=to_persian_digits("4"))


def test_notice_needs_enough_streak():
    conn = _conn()
    _set_state(conn, {"days": ["2026-09-08", "2026-09-09"]})
    assert maybe_deescalation_notice(conn, NOW, _cfg(), LABELS) is None


def test_notice_needs_enough_quiet():
    conn = _conn()
    _set_state(conn, {"days": ["2026-09-11", "2026-09-12", "2026-09-13"]})
    assert maybe_deescalation_notice(conn, NOW, _cfg(), LABELS) is None


def test_notice_respects_cooldown():
    conn = _conn()
    _set_state(
        conn,
        {
            "days": ["2026-09-08", "2026-09-09", "2026-09-10"],
            "last_notice_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    assert maybe_deescalation_notice(conn, NOW, _cfg(), LABELS) is None


def test_notice_corrupt_cooldown_treated_as_never():
    conn = _conn()
    _set_state(
        conn,
        {
            "days": ["2026-09-08", "2026-09-09", "2026-09-10"],
            "last_notice_at": "not-a-timestamp",
        },
    )
    assert maybe_deescalation_notice(conn, NOW, _cfg(), LABELS) == LABELS[
        "deescalation"
    ].format(days=to_persian_digits("4"))


# --- record_escalation_day ---


def test_record_marks_today_and_is_idempotent():
    conn = _conn()
    record_escalation_day(conn, NOW, _cfg())
    record_escalation_day(conn, NOW, _cfg())
    assert _get_state(conn)["days"] == ["2026-09-14"]


def test_record_prunes_old_days():
    conn = _conn()
    _set_state(conn, {"days": ["2026-08-01", "2026-09-13"]})
    record_escalation_day(conn, NOW, _cfg())
    # 2026-08-01 is beyond window_days=30 and must be dropped.
    assert _get_state(conn)["days"] == ["2026-09-13", "2026-09-14"]


def test_record_noop_when_disabled_or_no_conn():
    conn = _conn()
    record_escalation_day(None, NOW, _cfg())
    record_escalation_day(conn, NOW, None)
    record_escalation_day(conn, NOW, _cfg(enabled=False))
    assert conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0


# --- mark_notice_sent ---


def test_mark_notice_sent_stamps_clock():
    conn = _conn()
    mark_notice_sent(conn, NOW)
    assert _get_state(conn)["last_notice_at"] == NOW.isoformat()


def test_mark_notice_sent_noop_without_conn():
    mark_notice_sent(None, NOW)  # must not raise
