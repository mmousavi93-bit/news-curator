"""Unit tests for flash/policy.py — the burst state machine: collapse,
follow-ups, caps, quiet windows, ack-gating (owner-approved cleaning
policy 2026-08-30)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from agent.collectors.base import Item
from agent.delivery.telegram import SendResult
from agent.flash import store
from agent.flash.loader import validate_flash
from agent.flash.matcher import match_items
from agent.flash.policy import evaluate

_REPO_ROOT = Path(__file__).parent.parent.parent
_CONFIG = validate_flash(
    yaml.safe_load((_REPO_ROOT / "config" / "flash_alert.yaml")
                   .read_text(encoding="utf-8"))
)
NOW = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)


class _Sender:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    def __call__(self, text: str) -> SendResult:
        if self.fail:
            return SendResult(ok=False, status_code=500, description="x", attempts=1)
        self.sent.append(text)
        return SendResult(ok=True, status_code=200, description=None, attempts=1)


def _item(url: str, title: str, source_id: str, now=NOW, lang: str = "fa") -> Item:
    return Item(source_id=source_id, url=url, title=title, body="",
                published_at=now, lang=lang, raw_hash="f" * 8)


def _match(url: str, title: str, source_id: str, now=NOW, lang: str = "fa"):
    matches, _ = match_items([_item(url, title, source_id, now, lang)], _CONFIG, now)
    return matches[0]


def _db(tmp_path):
    return store.open_flash_db(tmp_path / "flash.db", create_if_absent=True)


def test_first_alert_fires_at_one_source(tmp_path):
    conn = _db(tmp_path)
    sender = _Sender()
    match = _match("https://x/1", "انفجار در تهران گزارش شد", "tg_a")
    stats = evaluate([match], conn, _CONFIG, NOW, sender, _Log())
    assert stats["sent"] == 1
    assert len(sender.sent) == 1
    assert "🚨" in sender.sent[0] and "تأیید نشده" in sender.sent[0]
    assert store.open_bursts(conn)[0].alert_sent is True


def test_second_source_merges_into_open_burst_no_second_alert(tmp_path):
    conn = _db(tmp_path)
    match = _match("https://x/1", "انفجار در تهران گزارش شد", "tg_a")
    evaluate([match], conn, _CONFIG, NOW, _Sender(), _Log())
    later = NOW + timedelta(minutes=5)
    match2 = _match("https://x/2", "انفجار در تهران؛ جزئیات تکمیلی", "tg_b", later)
    sender = _Sender()
    evaluate([match2], conn, _CONFIG, later, sender, _Log())
    assert sender.sent == []  # merged, not re-alerted
    burst = store.open_bursts(conn)[0]
    assert burst.source_count == 2


def test_followup_fires_at_three_sources(tmp_path):
    conn = _db(tmp_path)
    match = _match("https://x/1", "انفجار در تهران گزارش شد", "tg_a")
    evaluate([match], conn, _CONFIG, NOW, _Sender(), _Log())
    t = NOW + timedelta(minutes=5)
    matches = [
        _match("https://x/2", "انفجار در تهران؛ جزئیات", "tg_b", t),
        _match("https://x/3", "صدای انفجار در تهران", "tg_c", t),
    ]
    sender = _Sender()
    evaluate(matches, conn, _CONFIG, t, sender, _Log())
    assert len(sender.sent) == 1
    assert "به‌روزرسانی" in sender.sent[0] and "3 منبع" in sender.sent[0]


def test_followups_capped_at_two(tmp_path):
    conn = _db(tmp_path)
    match = _match("https://x/1", "انفجار در تهران گزارش شد", "tg_a")
    evaluate([match], conn, _CONFIG, NOW, _Sender(), _Log())
    t = NOW + timedelta(minutes=5)
    matches = [_match(f"https://x/{i}", "انفجار در تهران", f"tg_{i}", t)
               for i in range(2, 9)]  # 7 more sources
    sender = _Sender()
    evaluate(matches, conn, _CONFIG, t, sender, _Log())
    assert len(sender.sent) == 2  # FU at 3 and 8, nothing beyond


def test_ack_gating_retries_failed_send(tmp_path):
    conn = _db(tmp_path)
    match = _match("https://x/1", "انفجار در تهران گزارش شد", "tg_a")
    evaluate([match], conn, _CONFIG, NOW, _Sender(fail=True), _Log())
    assert store.open_bursts(conn)[0].alert_sent is False
    later = NOW + timedelta(minutes=10)
    sender = _Sender()
    evaluate([], conn, _CONFIG, later, sender, _Log())
    assert len(sender.sent) == 1  # retried on the next tick


def test_hourly_cap_defers_new_alert(tmp_path):
    conn = _db(tmp_path)
    # Seed three sent first-alerts in the last hour — three DISTINCT
    # signatures (same signature would merge, not alert).
    seeds = [
        _match("https://x/1", "انفجار در تهران", "tg_1"),
        _match("https://x/2", "حمله هوایی در تهران", "tg_2"),
        _match("https://x/3", "انفجار در کرج", "tg_3"),
    ]
    evaluate(seeds, conn, _CONFIG, NOW, _Sender(), _Log())
    assert store.alerts_sent_since(conn, store._iso(NOW - timedelta(hours=1))) == 3
    # A fourth NEW event (escalation class) hits the cap -> deferred.
    match = _match("https://x/9", "حمله آمریکا به لارک", "tg_9")
    sender = _Sender()
    evaluate([match], conn, _CONFIG, NOW + timedelta(minutes=1), sender, _Log())
    assert sender.sent == []  # deferred, burst stays open
    assert any(b.signature == "escalation"
               for b in store.open_bursts(conn))


def test_quiet_window_holds_refire_below_threshold(tmp_path):
    conn = _db(tmp_path)
    match = _match("https://x/1", "انفجار در تهران گزارش شد", "tg_a")
    evaluate([match], conn, _CONFIG, NOW, _Sender(), _Log())
    # Close the burst.
    for burst in store.open_bursts(conn):
        store.close_burst(conn, burst.id, NOW)
    later = NOW + timedelta(minutes=40)
    match2 = _match("https://x/2", "انفجار در تهران دوباره", "tg_b", later)
    sender = _Sender()
    evaluate([match2], conn, _CONFIG, later, sender, _Log())
    assert sender.sent == []  # tehran quiet_hours = 24 -> held at 1 source
    # Three sources break the quiet hold.
    t2 = later + timedelta(minutes=5)
    matches = [_match(f"https://x/{i}", "انفجار در تهران", f"tg_{i}", t2)
               for i in range(3, 6)]
    evaluate(matches, conn, _CONFIG, t2, sender, _Log())
    assert len(sender.sent) == 1


def test_escalation_quiet_window_is_shorter(tmp_path):
    conn = _db(tmp_path)
    match = _match("https://x/1", "حمله آمریکا به لارک", "tg_a")
    evaluate([match], conn, _CONFIG, NOW, _Sender(), _Log())
    for burst in store.open_bursts(conn):
        store.close_burst(conn, burst.id, NOW)
    # 13 hours later: within tehran's 24h quiet, OUTSIDE escalation's 12h.
    later = NOW + timedelta(hours=13)
    match2 = _match("https://x/2", "حمله آمریکا به ایران", "tg_b", later)
    sender = _Sender()
    evaluate([match2], conn, _CONFIG, later, sender, _Log())
    assert len(sender.sent) == 1  # fires immediately at one source


def test_window_expiry_stops_all_sends(tmp_path):
    conn = _db(tmp_path)
    # A burst whose first sighting is 2h old: past the 90-min follow-up
    # window — even the first alert is digest territory now.
    match = _match("https://x/1", "انفجار در تهران گزارش شد", "tg_a")
    store.insert_burst(conn, match, NOW - timedelta(hours=2), requires_sources=0)
    sender = _Sender()
    evaluate([], conn, _CONFIG, NOW, sender, _Log())
    assert sender.sent == []
    # The stale-close step closes the burst (30-min collapse window), but
    # it never SENT anything — closed unsent is the correct fate.
    row = conn.execute("SELECT alert_sent FROM bursts").fetchone()
    assert row["alert_sent"] == 0
    assert store.open_bursts(conn) == []


def test_cap_deferral_survives_and_fires_when_cap_frees(tmp_path):
    # Reviewer scenario 2026-08-31: on the busy hour the 4th real event
    # must NOT be silently dropped — "defer, never drop" is enforced.
    conn = _db(tmp_path)
    seeds = [
        _match("https://x/1", "انفجار در تهران", "tg_1"),
        _match("https://x/2", "حمله هوایی در تهران", "tg_2"),
        _match("https://x/3", "انفجار در کرج", "tg_3"),
    ]
    evaluate(seeds, conn, _CONFIG, NOW, _Sender(), _Log())
    match = _match("https://x/9", "حمله آمریکا به لارک", "tg_9")
    sender = _Sender()
    evaluate([match], conn, _CONFIG, NOW + timedelta(minutes=1), sender, _Log())
    assert sender.sent == []  # deferred at the cap
    # +30 min: within the 90-min deadline -> NOT stale-closed.
    evaluate([], conn, _CONFIG, NOW + timedelta(minutes=30), sender, _Log())
    assert any(b.signature == "escalation"
               for b in store.open_bursts(conn))
    # +62 min: the seeds aged out of the hourly window -> cap free, the
    # pending first alert finally fires.
    evaluate([], conn, _CONFIG, NOW + timedelta(minutes=62), sender, _Log())
    assert len(sender.sent) == 1
    assert "افزایش تنش" in sender.sent[0]


def test_never_sent_bursts_do_not_count_for_deescalation(tmp_path):
    # Reviewer finding 2026-08-31: quiet-held/stale-closed rumour hits
    # that never alerted must not produce a false "calm" notice.
    conn = _db(tmp_path)
    for d in (6, 7, 8):
        match = _match(f"https://x/h{d}", "حمله آمریکا به لارک", f"tg_h{d}")
        _backdate_burst(conn, match, d, sent=False)
    sender = _Sender()
    evaluate([], conn, _CONFIG, NOW, sender, _Log())
    assert sender.sent == []


def test_headline_html_is_escaped_before_send(tmp_path):
    # Telegram sendMessage parses HTML; a title carrying & or < would
    # 400 permanently and lose the alert (reviewer finding).
    conn = _db(tmp_path)
    match = _match("https://x/1", "انفجار در تهران & کرج <جزئیات>", "tg_a")
    sender = _Sender()
    evaluate([match], conn, _CONFIG, NOW, sender, _Log())
    assert len(sender.sent) == 1
    assert "&amp;" in sender.sent[0]
    assert "<جزئیات>" not in sender.sent[0]


def test_escalation_wave_merges_into_one_alert(tmp_path):
    # Owner live feedback 2026-08-31: three escalation posts in the same
    # wave (different buckets, different locations) = ONE alert, sources
    # accumulate — not three first alerts.
    conn = _db(tmp_path)
    matches = [
        _match("https://x/1", "حمله به کشتی تجاری در تنگه هرمز", "tg_1"),
        _match("https://x/2", "حمله آمریکا به لارک", "tg_2"),
    ]
    sender = _Sender()
    evaluate(matches, conn, _CONFIG, NOW, sender, _Log())
    assert len(sender.sent) == 1
    assert store.open_bursts(conn)[0].source_count == 2


def test_novel_bucket_after_gap_realerts(tmp_path):
    # "Once per momentum change is enough": a novel bucket INSIDE the
    # gap merges silently; past the gap it is a new momentum change and
    # re-alerts.
    conn = _db(tmp_path)
    evaluate([_match("https://x/1", "حمله به کشتی تجاری در تنگه هرمز", "tg_1")],
             conn, _CONFIG, NOW, _Sender(), _Log())
    t1 = NOW + timedelta(minutes=60)
    sender = _Sender()
    evaluate([_match("https://x/2", "حمله دریایی در بندرعباس", "tg_2", t1)],
             conn, _CONFIG, t1, sender, _Log())
    assert sender.sent == []  # inside the 120-min gap: merged, no re-alert
    t2 = t1 + timedelta(minutes=130)
    evaluate([_match("https://x/3", "ناوهواپیمابر وارد خلیج فارس شد", "tg_3", t2)],
             conn, _CONFIG, t2, sender, _Log())
    assert len(sender.sent) == 1  # past the gap: momentum change -> alert
    assert "افزایش تنش" in sender.sent[0]


def test_convergence_note_when_three_buckets_fire_within_72h(tmp_path):
    # WAR_SIGNALS_PAPER: one category screaming is a rumor cycle; three
    # ALERTED categories moving within 72h is a war. The note is
    # deterministic, never a claim upgrade, and counts only buckets the
    # owner actually saw (live feedback 2026-08-31).
    conn = _db(tmp_path)
    seeds = [
        _match("https://x/1", "حمله به کشتی تجاری در تنگه هرمز", "tg_1"),
        _match("https://x/2", "حمله آمریکا به لارک", "tg_2"),
    ]
    evaluate(seeds, conn, _CONFIG, NOW, _Sender(), _Log())
    for burst in store.open_bursts(conn):
        store.close_burst(conn, burst.id, NOW)
    t = NOW + timedelta(minutes=130)  # past the novelty gap
    match = _match("https://x/9", "ناوهواپیمابر لینکلن وارد خلیج فارس شد",
                   "tg_9", t)
    sender = _Sender()
    evaluate([match], conn, _CONFIG, t, sender, _Log())
    assert len(sender.sent) == 1
    assert "همگرایی سیگنال" in sender.sent[0]
    assert "3 دسته" in sender.sent[0]


def test_arabic_headline_gets_lang_prefix(tmp_path):
    # Owner live feedback 2026-08-31: raw Arabic headlines read as a
    # broken Persian contract. The quote stays raw; the prefix makes it
    # explicit.
    conn = _db(tmp_path)
    match = _match("https://x/1",
                   "بحرية الحرس الثوري الإيراني: تحذير لشركات الملاحة",
                   "al_manar", lang="ar")
    sender = _Sender()
    evaluate([match], conn, _CONFIG, NOW, sender, _Log())
    assert len(sender.sent) == 1
    assert "«عربی»" in sender.sent[0]


def _backdate_burst(conn, match, days_ago, now=NOW, sent: bool = True):
    """Insert a burst then backdate its first/last-seen and close it —
    the momentum layer reads ALERTED history rows, so seeds are marked
    sent by default (a pattern the owner never saw cannot count)."""
    burst_id = store.insert_burst(conn, match, now, requires_sources=0)
    stamp = store._iso(now - timedelta(days=days_ago))
    conn.execute(
        "UPDATE bursts SET first_seen_at = ?, last_seen_at = ?, closed_at = ?, "
        "alert_sent = ?, alert_sent_at = ? WHERE id = ?",
        (stamp, stamp, stamp, int(sent), stamp if sent else None, burst_id))
    conn.commit()


def test_background_bucket_needs_volume_on_day_three(tmp_path):
    # Day 1-2 of the same attack-and-response pattern is escalation; by
    # day 3 at the same intensity it is the new normal — re-alert only
    # with volume (owner 2026-08-31 momentum rule).
    conn = _db(tmp_path)
    for d in (1, 2, 3):
        match = _match(f"https://x/h{d}", "حمله آمریکا به لارک", f"tg_h{d}")
        _backdate_burst(conn, match, d)
    match = _match("https://x/n1", "حمله آمریکا به لارک", "tg_n1")
    sender = _Sender()
    evaluate([match], conn, _CONFIG, NOW, sender, _Log())
    assert sender.sent == []  # background: 1 source is not enough
    t = NOW + timedelta(minutes=5)
    matches = [_match(f"https://x/n{i}", "حمله آمریکا به لارک", f"tg_n{i}", t)
               for i in range(2, 4)]
    evaluate(matches, conn, _CONFIG, t, sender, _Log())
    assert len(sender.sent) == 1  # 3 sources break the background hold


def test_novel_target_restores_instant_escalation(tmp_path):
    conn = _db(tmp_path)
    for d in (1, 2, 3):
        match = _match(f"https://x/h{d}", "حمله آمریکا به لارک", f"tg_h{d}")
        _backdate_burst(conn, match, d)
    # Same bucket, NEW target domain (تهران) — full escalation again,
    # fires at ONE source even mid-streak.
    match = _match("https://x/n1", "حمله آمریکا به تهران", "tg_n1")
    sender = _Sender()
    evaluate([match], conn, _CONFIG, NOW, sender, _Log())
    assert len(sender.sent) == 1


def test_deescalation_notice_after_three_quiet_days(tmp_path):
    conn = _db(tmp_path)
    for d in (6, 7, 8):
        match = _match(f"https://x/h{d}", "حمله آمریکا به لارک", f"tg_h{d}")
        _backdate_burst(conn, match, d)
    sender = _Sender()
    evaluate([], conn, _CONFIG, NOW, sender, _Log())
    assert len(sender.sent) == 1
    assert "کاهش تنش" in sender.sent[0]
    # Cooldown: no second notice on the next tick.
    sender2 = _Sender()
    evaluate([], conn, _CONFIG, NOW + timedelta(minutes=15), sender2, _Log())
    assert sender2.sent == []


def test_no_deescalation_without_prior_pattern(tmp_path):
    conn = _db(tmp_path)
    sender = _Sender()
    evaluate([], conn, _CONFIG, NOW, sender, _Log())
    assert sender.sent == []


def test_no_deescalation_when_recently_active(tmp_path):
    conn = _db(tmp_path)
    match = _match("https://x/1", "حمله آمریکا به لارک", "tg_1")
    _backdate_burst(conn, match, 1)
    sender = _Sender()
    evaluate([], conn, _CONFIG, NOW, sender, _Log())
    assert sender.sent == []


def test_requires_override_none_on_short_streak_seen_token(tmp_path):
    from agent.flash import momentum as momentum_mod

    conn = _db(tmp_path)
    match = _match("https://x/h1", "حمله آمریکا به لارک", "tg_h1")
    _backdate_burst(conn, match, 1)
    assert momentum_mod.requires_override(
        conn, "escalation", "strike", "لارک", NOW, _CONFIG) is None


def test_stale_burst_closes(tmp_path):
    conn = _db(tmp_path)
    match = _match("https://x/1", "انفجار در تهران گزارش شد", "tg_a")
    evaluate([match], conn, _CONFIG, NOW, _Sender(), _Log())
    later = NOW + timedelta(minutes=40)  # > collapse window 30
    evaluate([], conn, _CONFIG, later, _Sender(), _Log())
    assert store.open_bursts(conn) == []
