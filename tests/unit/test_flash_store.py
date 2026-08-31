"""Unit tests for flash/store.py — the monitor's own DB on its own
flash-state branch (never the pipeline's state; solver 2026-08-30)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.flash import history, store

NOW = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)


def test_open_creates_fresh_on_first_boot(tmp_path):
    path = tmp_path / "flash.db"
    conn = store.open_flash_db(path, create_if_absent=True)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'flash_schema_version'").fetchone()
        assert row["value"] == "1"
    finally:
        conn.close()
    conn2 = store.open_flash_db(path)  # exists now: opens without the flag
    conn2.close()


def test_open_refuses_missing_file_without_flag(tmp_path):
    with pytest.raises(Exception):
        store.open_flash_db(tmp_path / "nope.db")


def test_known_and_mark_seen_roundtrip(tmp_path):
    conn = store.open_flash_db(tmp_path / "flash.db", create_if_absent=True)
    try:
        hashes = {history.url_hash(u) for u in ("https://x/1", "https://x/2")}
        assert history.known_urls(conn, hashes) == set()
        history.mark_seen(conn, hashes, NOW)
        assert history.known_urls(conn, hashes | {history.url_hash("https://x/3")}) == hashes
    finally:
        conn.close()


def test_url_hash_stable_and_distinct():
    assert history.url_hash("https://x/1") == history.url_hash("https://x/1")
    assert history.url_hash("https://x/1") != history.url_hash("https://x/2")


def test_burst_lifecycle_and_alert_count_window(tmp_path):
    conn = store.open_flash_db(tmp_path / "flash.db", create_if_absent=True)
    try:
        from agent.flash.matcher import Match

        match = Match(class_name="tehran", term_bucket="explosion",
                      location_ring="city", location_token="تهران",
                      item=type("I", (), {"title": "انفجار", "body": "",
                                          "source_id": "tg_a"})(),
                      signature="tehran|explosion|city")
        burst_id = store.insert_burst(conn, match, NOW, requires_sources=0)
        store.add_source(conn, burst_id, "tg_b", "explosion",
                         NOW + timedelta(minutes=5))
        store.mark_alert_sent(conn, burst_id, NOW)
        old = NOW - timedelta(hours=2)
        assert store.alerts_sent_since(conn, store._iso(old)) == 1
        assert store.alerts_sent_since(conn, store._iso(NOW + timedelta(minutes=1))) == 0
        store.close_burst(conn, burst_id, NOW + timedelta(minutes=10))
        assert store.open_bursts(conn) == []
    finally:
        conn.close()


def test_prune_removes_old_closed_bursts_and_urls(tmp_path):
    conn = store.open_flash_db(tmp_path / "flash.db", create_if_absent=True)
    try:
        from agent.flash.matcher import Match

        match = Match(class_name="tehran", term_bucket="explosion",
                      location_ring="city", location_token="تهران",
                      item=type("I", (), {"title": "انفجار", "body": "",
                                          "source_id": "tg_a"})(),
                      signature="tehran|explosion|city")
        burst_id = store.insert_burst(conn, match, NOW, requires_sources=0)
        store.close_burst(conn, burst_id, NOW)
        # Bursts keep 30 days (the momentum lookback horizon); urls 7.
        conn.execute("UPDATE bursts SET closed_at = ? WHERE id = ?",
                     (store._iso(NOW - timedelta(days=31)), burst_id))
        conn.commit()
        history.mark_seen(conn, {history.url_hash("https://x/old")},
                          NOW - timedelta(days=8))
        n_bursts, n_urls = store.prune(conn, NOW)
        assert (n_bursts, n_urls) == (1, 1)
        # An 8-day-old closed burst SURVIVES prune — the de-escalation
        # notice needs it (reviewer finding 2026-08-31).
        burst_id2 = store.insert_burst(conn, match, NOW, requires_sources=0)
        store.close_burst(conn, burst_id2, NOW)
        conn.execute("UPDATE bursts SET closed_at = ? WHERE id = ?",
                     (store._iso(NOW - timedelta(days=8)), burst_id2))
        conn.commit()
        assert store.prune(conn, NOW)[0] == 0
    finally:
        conn.close()


def test_insert_burst_uses_body_when_title_empty(tmp_path):
    # Owner live feedback 2026-08-31: Telegram posts carry their content
    # in the body and nothing in the title — the first live run shipped
    # blank headlines.
    conn = store.open_flash_db(tmp_path / "flash.db", create_if_absent=True)
    try:
        from agent.flash.matcher import Match

        match = Match(class_name="tehran", term_bucket="explosion",
                      location_ring="city", location_token="تهران",
                      item=type("I", (), {
                          "title": "",
                          "body": "صدای انفجار در تهران شنیده شد — جزئیات بعدا",
                          "source_id": "tg_a"})(),
                      signature="tehran|explosion|city")
        store.insert_burst(conn, match, NOW, requires_sources=0)
        assert store.open_bursts(conn)[0].headline.startswith("صدای انفجار")
    finally:
        conn.close()


def test_log_flash_persists_rows(tmp_path):
    conn = store.open_flash_db(tmp_path / "flash.db", create_if_absent=True)
    try:
        store.log_flash(conn, [("fired", "tehran", "s", "tg_a", "1 source(s)")], NOW)
        row = conn.execute("SELECT COUNT(*) AS n FROM flash_log").fetchone()
        assert row["n"] == 1
    finally:
        conn.close()
