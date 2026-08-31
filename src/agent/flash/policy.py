"""The burst state machine: collapse, follow-ups, caps, quiet windows,
ack-gating. Deterministic — identical state + input yields identical
sends (constraint 3).

Cleaning policy (owner-approved 2026-08-30):
- one explosion reported by N sources in minutes = ONE first alert + at
  most 2 follow-up updates. The count is reported, never upgraded —
  repetition is amplification, not corroboration (standing rumour
  policy).
- first alert fires at source-count 1 immediately, UNLESS the signature
  is in its quiet window (a closed burst, same signature, closed within
  class.quiet_hours) — then it waits for >= quiet_requires_sources.
- follow-ups fire when the distinct-source count crosses config
  thresholds (3, 8).
- cap (max_alerts_per_hour) counts NEW first alerts actually SENT in the
  last hour; a cap hit DEFERS (burst stays open, next scan retries) —
  never drops.
- ack-gating: a burst is marked alert_sent only after a successful send.
  A failed send retries on the next tick while the burst is open.
- both first alerts and follow-ups stop at followup_window_minutes after
  first sighting; the 3-hourly digest owns the story from there.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from agent.collectors.dates import to_tehran
from agent.delivery.formatter import escape_html
from agent.flash import history, momentum, store
from agent.flash.config import FlashConfig
from agent.flash.matcher import Match
from agent.util.jalali import format_jalali

_HEADLINE_CAP = 120


def _headline(burst: store.BurstRow) -> str:
    text = escape_html(burst.headline.strip())
    return text if len(text) <= _HEADLINE_CAP else text[:_HEADLINE_CAP] + "…"


def _render_first(burst: store.BurstRow, config: FlashConfig, now: datetime,
                  convergence: str = "") -> str:
    extra = burst.source_count - 1
    return config.templates["first"].format(
        label=config.classes[burst.class_name].label,
        headline=_headline(burst),
        location_token=escape_html(burst.location_token),
        first_source=burst.first_source,
        more_sources=f" (+{extra} منبع دیگر)" if extra else "",
        convergence=convergence,
        jalali=format_jalali(now),
        tehran_time=to_tehran(now).strftime("%H:%M"),
    )


def _render_followup(burst: store.BurstRow, config: FlashConfig) -> str:
    return config.templates["followup"].format(
        n=burst.source_count,
        label=config.classes[burst.class_name].label,
        headline=_headline(burst),
    )


def _deadline(burst: store.BurstRow, config: FlashConfig) -> datetime:
    first = datetime.fromisoformat(burst.first_seen_at)
    return first + timedelta(minutes=config.followup_window_minutes)


def _stale(burst: store.BurstRow, config: FlashConfig, now: datetime) -> bool:
    # An UNFIRED burst inside its follow-up window is NOT stale: the cap
    # deferral keeps it open for a later tick, and stale-closing it would
    # silently drop the event (reviewer finding 2026-08-31 — the
    # "defer, never drop" contract is now enforced, not claimed).
    if not burst.alert_sent and now <= _deadline(burst, config):
        return False
    last = datetime.fromisoformat(burst.last_seen_at)
    return now - last > timedelta(minutes=config.collapse_window_minutes)


def _in_quiet(burst: store.BurstRow, config: FlashConfig, now: datetime) -> bool:
    quiet_hours = config.classes[burst.class_name].quiet_hours
    closed_at = datetime.fromisoformat(burst.closed_at)
    return now - closed_at <= timedelta(hours=quiet_hours)


def evaluate(matches: list[Match], conn: sqlite3.Connection,
             config: FlashConfig, now: datetime, send, logger: logging.Logger) -> dict:
    log_rows: list[tuple] = []
    sent = 0

    # 1. Close stale bursts.
    for burst in store.open_bursts(conn):
        if _stale(burst, config, now):
            store.close_burst(conn, burst.id, now)
            logger.info("flash: burst %s closed (stale)", burst.signature)

    # 2. Merge matches into open bursts; create new ones (quiet-checked).
    max_quiet = max(c.quiet_hours for c in config.classes.values())
    closed_recent = store.closed_signatures(
        conn, store._iso(now - timedelta(hours=max_quiet)))
    for match in matches:
        open_by_sig = {b.signature: b for b in store.open_bursts(conn)}
        burst = open_by_sig.get(match.signature)
        if burst is not None:
            if match.item.source_id not in burst.source_ids:
                store.add_sources(conn, burst.id, {match.item.source_id}, now)
            continue
        requires = 0
        recent = closed_recent.get(match.signature, [])
        latest = max((b for b in recent),
                     key=lambda b: b.closed_at, default=None)
        if latest is not None and _in_quiet(latest, config, now):
            requires = config.classes[match.class_name].quiet_requires_sources
            log_rows.append(("quiet_held", match.class_name, match.signature,
                             match.item.source_id,
                             f"requires {requires} sources after recent closure"))
        # Momentum (owner 2026-08-31): a background bucket needs volume
        # to re-alert; a NOVEL target domain restores instant escalation
        # even inside the quiet window.
        override = momentum.requires_override(
            conn, match.class_name, match.term_bucket,
            match.location_token, now, config,
        )
        if override is not None:
            if override == 0:
                log_rows.append(("novel_target", match.class_name,
                                 match.signature, match.item.source_id,
                                 f"new domain {match.location_token}"))
            else:
                log_rows.append(("background", match.class_name,
                                 match.signature, match.item.source_id,
                                 f"repeat pattern needs {override} sources"))
            requires = override
        store.insert_burst(conn, match, now, requires_sources=requires)

    # 3. Send decisions.
    alert_cutoff = store._iso(now - timedelta(hours=1))
    alerts_this_hour = store.alerts_sent_since(conn, alert_cutoff)
    for burst in store.open_bursts(conn):
        if now > _deadline(burst, config):
            continue  # digest territory
        if not burst.alert_sent:
            if burst.source_count < burst.requires_sources:
                log_rows.append(("quiet_held", burst.class_name, burst.signature,
                                 "", f"{burst.source_count}/{burst.requires_sources} sources"))
                continue
            if alerts_this_hour >= config.max_alerts_per_hour:
                log_rows.append(("deferred", burst.class_name, burst.signature,
                                 "", "hourly cap"))
                logger.warning("flash: alert deferred (hourly cap %d)",
                               config.max_alerts_per_hour)
                continue
            # Convergence (WAR_SIGNALS_PAPER 2025-26): one category
            # screaming is a rumor cycle; three categories moving within
            # 72h is a war. Deterministic note, never a claim upgrade.
            convergence = ""
            if burst.class_name == "escalation":
                others = history.recent_distinct_buckets(
                    conn, "escalation",
                    store._iso(now - timedelta(hours=72)),
                    exclude_bucket=burst.term_bucket,
                )
                if len(others) >= 2:
                    convergence = (
                        f"⚠️ همگرایی سیگنال‌ها: {len(others) + 1} دسته "
                        "در ۷۲ ساعت گذشته\n"
                    )
            result = send(_render_first(burst, config, now, convergence))
            if result.ok:
                store.mark_alert_sent(conn, burst.id, now)
                alerts_this_hour += 1
                sent += 1
                log_rows.append(("fired", burst.class_name, burst.signature,
                                 burst.first_source, f"{burst.source_count} source(s)"))
                logger.warning("flash: ALERT %s %s", burst.class_name, burst.signature)
            continue
        # Follow-ups: only after a sent first alert, within the window.
        # A while-loop, not an if: a single scan can cross BOTH thresholds
        # (8 sources appear in one tick — both updates are information).
        fu_sent = burst.followups_sent
        while (fu_sent < len(config.followups)
               and burst.source_count >= config.followups[fu_sent]):
            result = send(_render_followup(burst, config))
            if not result.ok:
                break  # retry on the next tick
            store.mark_followup(conn, burst.id)
            fu_sent += 1
            sent += 1
            log_rows.append(("followup", burst.class_name, burst.signature,
                             "", f"{burst.source_count} source(s)"))
    if log_rows:
        store.log_flash(conn, log_rows, now)
    deescalated = momentum.maybe_deescalation(conn, config, now, send, logger)
    open_count = len(store.open_bursts(conn))
    return {"sent": sent + deescalated, "alerts_this_hour": alerts_this_hour,
            "open_bursts": open_count}
