"""Unit tests for llm/health.py — the health-aware cascade (owner-approved
move 2, 2026-08-31). Deterministic rules: a provider with >= 4 calls and
>= 50% failures in the 7-day window starts last on the next run."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.flash import store as flash_store
from agent.llm import health

NOW = datetime(2026, 8, 31, 5, 30, tzinfo=timezone.utc)
ORDER = ["gemini", "groq", "bai", "openrouter"]


def _db(tmp_path):
    return flash_store.open_flash_db(tmp_path / "h.db", create_if_absent=True)


def test_cascade_order_keeps_healthy_configuration():
    h = {"gemini": {"calls": 10, "failed": 1}, "groq": {"calls": 8, "failed": 0}}
    assert health.cascade_order(ORDER, h) == list(ORDER)


def test_cascade_order_demotes_sick_provider_to_end():
    h = {"gemini": {"calls": 6, "failed": 4}, "groq": {"calls": 6, "failed": 0}}
    assert health.cascade_order(ORDER, h) == ["groq", "bai", "openrouter", "gemini"]


def test_cascade_order_requires_minimum_samples():
    h = {"gemini": {"calls": 3, "failed": 3}}  # 100% bad but tiny sample
    assert health.cascade_order(ORDER, h) == list(ORDER)


def test_cascade_order_ignores_unknown_providers_in_health():
    h = {"ghost": {"calls": 50, "failed": 50}}
    assert health.cascade_order(ORDER, h) == list(ORDER)


def test_cascade_order_empty_health_is_configured_order():
    assert health.cascade_order(ORDER, {}) == list(ORDER)


def test_save_load_roundtrip_and_merge(tmp_path):
    conn = _db(tmp_path)
    try:
        health.save_health(conn, {"gemini": {"calls": 5, "failed": 2}}, NOW)
        stored = health.load_health(conn)
        assert stored["gemini"] == {"calls": 5, "failed": 2}
        health.save_health(conn, {"gemini": {"calls": 2, "failed": 1}},
                           NOW + timedelta(hours=3))
        stored = health.load_health(conn)
        assert stored["gemini"] == {"calls": 7, "failed": 3}
    finally:
        conn.close()


def test_save_resets_window_after_seven_days(tmp_path):
    conn = _db(tmp_path)
    try:
        health.save_health(conn, {"gemini": {"calls": 10, "failed": 9}},
                           NOW - timedelta(days=8))
        health.save_health(conn, {"gemini": {"calls": 2, "failed": 0}}, NOW)
        stored = health.load_health(conn)
        assert stored["gemini"] == {"calls": 2, "failed": 0}
    finally:
        conn.close()


def test_load_corrupt_health_returns_empty(tmp_path):
    conn = _db(tmp_path)
    try:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                     ("provider_health_v1", "not json at all"))
        conn.commit()
        assert health.load_health(conn) == {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Daily quota persistence (llm/limits.py seeds ProviderBudget from this)
# ---------------------------------------------------------------------------


def test_daily_roundtrip_and_merge_same_day(tmp_path):
    conn = _db(tmp_path)
    try:
        health.save_daily(conn, {"gemini": {"calls": 5, "failed": 1}}, NOW)
        assert health.load_daily(conn, NOW) == {"gemini": 5}
        health.save_daily(conn, {"gemini": {"calls": 3, "failed": 0}},
                          NOW + timedelta(hours=1))
        assert health.load_daily(conn, NOW + timedelta(hours=1)) == {"gemini": 8}
    finally:
        conn.close()


def test_daily_rollover_resets_to_zero(tmp_path):
    conn = _db(tmp_path)
    try:
        health.save_daily(conn, {"gemini": {"calls": 20, "failed": 0}}, NOW)
        next_day = NOW + timedelta(days=1)
        assert health.load_daily(conn, next_day) == {}
        health.save_daily(conn, {"gemini": {"calls": 2, "failed": 0}}, next_day)
        assert health.load_daily(conn, next_day) == {"gemini": 2}
    finally:
        conn.close()


def test_daily_absent_provider_counts_zero(tmp_path):
    conn = _db(tmp_path)
    try:
        health.save_daily(conn, {"gemini": {"calls": 5, "failed": 0}}, NOW)
        assert health.load_daily(conn, NOW).get("groq", 0) == 0
    finally:
        conn.close()


def test_load_corrupt_daily_returns_empty(tmp_path):
    conn = _db(tmp_path)
    try:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                     ("provider_daily_v1", "not json"))
        conn.commit()
        assert health.load_daily(conn, NOW) == {}
    finally:
        conn.close()


def test_daily_key_aligns_to_pacific_midnight(tmp_path):
    # 05:30 UTC on Sep 5 is 22:30 PDT on Sep 4 -- the canonical digest sits
    # BEFORE Google's Pacific-midnight reset, so its calls must land on the
    # prior quota day, not a fresh one. 07:01 UTC is 00:01 PDT Sep 5.
    conn = _db(tmp_path)
    try:
        before_reset = datetime(2026, 9, 5, 5, 30, tzinfo=timezone.utc)
        health.save_daily(conn, {"gemini": {"calls": 1, "failed": 0}}, before_reset)
        assert health.load_daily(conn, before_reset) == {"gemini": 1}
        after_reset = datetime(2026, 9, 5, 7, 1, tzinfo=timezone.utc)
        assert health.load_daily(conn, after_reset) == {}
    finally:
        conn.close()


def test_daily_empty_stats_does_not_wipe(tmp_path):
    conn = _db(tmp_path)
    try:
        health.save_daily(conn, {"gemini": {"calls": 5, "failed": 0}}, NOW)
        health.save_daily(conn, {}, NOW + timedelta(hours=1))
        assert health.load_daily(conn, NOW + timedelta(hours=1)) == {"gemini": 5}
    finally:
        conn.close()
