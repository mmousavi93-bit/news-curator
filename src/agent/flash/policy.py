"""The burst state machine: collapse, follow-ups, caps, quiet windows,
ack-gating. Deterministic — identical state + input yields
identical sends (constraint 3). Rendering + window math live in
frames.py (split 2026-08-31, constraint 12).

Cleaning policy (owner-approved 2026-08-30, refined by live feedback
2026-08-31):

- per-signature bursts: one open burst per (bucket, location-ring)
  signature. The same event reported by N sources in minutes = one
  first alert + at most 2 source-count follow-ups. The count is
  reported, never upgraded — repetition is amplification, not
  corroboration (standing rumour policy).
- first alert fires at source-count 1 immediately, UNLESS the signature
  is in its quiet window — then it waits for >= quiet_requires_sources.
- follow-ups fire when the distinct-source count crosses config
  thresholds (3, 8).
- cap (max_alerts_per_hour) counts NEW first alerts actually SENT in
  the last hour; a cap hit DEFERS (burst stays open, next scan
  retries) — never drops.
- ack-gating: a burst is marked alert_sent only after a successful
  send. A failed send retries on the next tick while the burst is
  open. First alerts and follow-ups stop at followup_window_minutes
  after first sighting; the 3-hourly digest owns the story from there.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from agent.flash import frames, store
from agent.flash.config import FlashConfig
from agent.flash.matcher import Match


def evaluate(matches: list[Match], conn: sqlite3.Connection,
             config: FlashConfig, now: datetime, send, logger: logging.Logger) -> dict:
    log_rows: list[tuple] = []
    sent = 0

    # 1. Close stale bursts (per-class collapse window).
    for burst in store.open_bursts(conn):
        if frames.stale(burst, config, now):
            store.close_burst(conn, burst.id, now)
            logger.info("flash: burst %s closed (stale)", burst.signature)

    # 2. Merge matches into open bursts; create new ones (quiet-window checked).
    max_quiet = max(c.quiet_hours for c in config.classes.values())
    closed_recent = store.closed_signatures(
        conn, store._iso(now - timedelta(hours=max_quiet)))
    for match in matches:
        open_burst = next((b for b in store.open_bursts(conn)
                           if b.signature == match.signature), None)

        if open_burst is not None:
            if match.item.source_id not in open_burst.source_ids:
                store.add_source(conn, open_burst.id, match.item.source_id,
                                 match.term_bucket, now)
            continue

        requires = 0
        recent = closed_recent.get(match.signature, [])
        latest = max((b for b in recent),
                     key=lambda b: b.closed_at, default=None)
        if latest is not None and frames.in_quiet(latest, config, now):
            requires = config.classes[match.class_name].quiet_requires_sources
            log_rows.append(("quiet_held", match.class_name, match.signature,
                             match.item.source_id,
                             f"requires {requires} sources after recent closure"))
        store.insert_burst(conn, match, now, requires_sources=requires)

    # 3. Send decisions.
    alert_cutoff = store._iso(now - timedelta(hours=1))
    alerts_this_hour = store.alerts_sent_since(conn, alert_cutoff)
    for burst in store.open_bursts(conn):
        if now > frames.deadline(burst, config):
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
            result = send(frames.render_first(burst, config, now))
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
            result = send(frames.render_followup(burst, config))
            if not result.ok:
                break  # retry on the next tick
            store.mark_followup(conn, burst.id)
            fu_sent += 1
            sent += 1
            log_rows.append(("followup", burst.class_name, burst.signature,
                             "", f"{burst.source_count} source(s)"))
    if log_rows:
        store.log_flash(conn, log_rows, now)
    open_count = len(store.open_bursts(conn))
    return {"sent": sent, "alerts_this_hour": alerts_this_hour,
            "open_bursts": open_count}
