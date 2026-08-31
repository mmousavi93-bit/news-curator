"""Message rendering + window math for the flash monitor. Split out of
policy.py 2026-08-31 (constraint 12: the state machine gained the
novelty-gap merge and policy.py crossed the ~200-line cap).

Rendering rules (owner output contract):
- every interpolated source string is HTML-escaped (Telegram parse_mode
  is HTML; one unescaped & is a permanent 400 on an unattended run).
- non-Persian headlines carry a lang prefix — «(عربی)» / «(انگلیسی)» —
  the Persian contract governs the SYSTEM's voice, quoted source text
  stays raw (owner feedback 2026-08-31: raw Arabic headlines read as a
  broken contract; the prefix makes the quote explicit).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agent.collectors.dates import to_tehran
from agent.delivery.formatter import escape_html
from agent.flash import store
from agent.flash.config import FlashConfig
from agent.util.jalali import format_jalali

_HEADLINE_CAP = 120
_ARABIC_ONLY = frozenset("ةىيكإ")


def _lang_prefix(text: str) -> str:
    if any(ch in _ARABIC_ONLY for ch in text) or any(
            0x0590 <= ord(c) <= 0x05FF for c in text):
        return "«عربی» "
    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if ord(c) < 128) / len(letters) > 0.6:
        return "«انگلیسی» "
    return ""


def headline(burst: store.BurstRow) -> str:
    raw = burst.headline.strip()
    if len(raw) > _HEADLINE_CAP:
        raw = raw[:_HEADLINE_CAP] + "…"
    return _lang_prefix(raw) + escape_html(raw)


def render_first(burst: store.BurstRow, config: FlashConfig, now: datetime,
                 convergence: str = "") -> str:
    extra = burst.source_count - 1
    return config.templates["first"].format(
        label=config.classes[burst.class_name].label,
        headline=headline(burst),
        location_token=escape_html(burst.location_token),
        first_source=burst.first_source,
        more_sources=f" (+{extra} منبع دیگر)" if extra else "",
        convergence=convergence,
        jalali=format_jalali(now),
        tehran_time=to_tehran(now).strftime("%H:%M"),
    )


def render_followup(burst: store.BurstRow, config: FlashConfig) -> str:
    return config.templates["followup"].format(
        n=burst.source_count,
        label=config.classes[burst.class_name].label,
        headline=headline(burst),
    )


def deadline(burst: store.BurstRow, config: FlashConfig) -> datetime:
    first = datetime.fromisoformat(burst.first_seen_at)
    return first + timedelta(minutes=config.followup_window_minutes)


def collapse_window(burst: store.BurstRow, config: FlashConfig) -> int:
    """Per-class collapse window: escalation's class-level burst stays
    open 180 min so one wave = one alert (owner live feedback
    2026-08-31); tehran uses the global 30-min default."""
    alert_class = config.classes[burst.class_name]
    return (alert_class.collapse_window_minutes
            or config.collapse_window_minutes)


def stale(burst: store.BurstRow, config: FlashConfig, now: datetime) -> bool:
    # An UNFIRED burst inside its follow-up window is NOT stale: the cap
    # deferral keeps it open for a later tick, and stale-closing it would
    # silently drop the event (reviewer finding 2026-08-31 — the
    # "defer, never drop" contract is now enforced, not claimed).
    if not burst.alert_sent and now <= deadline(burst, config):
        return False
    last = datetime.fromisoformat(burst.last_seen_at)
    return now - last > timedelta(minutes=collapse_window(burst, config))


def in_quiet(burst: store.BurstRow, config: FlashConfig, now: datetime) -> bool:
    quiet_hours = config.classes[burst.class_name].quiet_hours
    closed_at = datetime.fromisoformat(burst.closed_at)
    return now - closed_at <= timedelta(hours=quiet_hours)
