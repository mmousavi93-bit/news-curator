"""The burst state machine: collapse, follow-ups, caps, quiet windows,
momentum, ack-gating. Deterministic — identical state + input yields
identical sends (constraint 3). Rendering + window math live in
frames.py (split 2026-08-31, constraint 12).

Cleaning policy (owner-approved 2026-08-30, refined by live feedback
2026-08-31):

- CLASS-LEVEL bursts for escalation: ONE open escalation state, not one
  burst per story. The same event reported by N sources in minutes =
  one first alert + at most 2 source-count follow-ups. The count is
  reported, never upgraded — repetition is amplification, not
  corroboration (standing rumour policy).
- first alert fires at source-count 1 immediately, UNLESS the signature
  is in its quiet window — then it waits for >= quiet_requires_sources.
- follow-ups fire when the distinct-source count crosses config
  thresholds (3, 8).
- NOVELTY RE-ALERT (the momentum-change alert): a match whose
  (bucket, location-ring) has not alerted in the streak window
  re-alerts as a new first alert — but only after the open burst has
  been quiet for novelty_min_gap_minutes. "Once for each momentum
  change is enough" (owner, on the 6-alerts-in-2-hours live run).
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

from agent.flash import frames, history, momentum, store
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

    # 2. Merge matches into open bursts; create new ones (quiet- and
    #    momentum-checked; novelty-gap re-alerts).
    max_quiet = max(c.quiet_hours for c in config.classes.values())
    closed_recent = store.closed_signatures(
        conn, store._iso(now - timedelta(hours=max_quiet)))
    for match in matches:
        open_burst = next((b for b in store.open_bursts(conn)
                           if b.signature == match.signature), None)
        override = momentum.requires_override(
            conn, match.class_name, match.term_bucket,
            match.location_token, now, config,
        )
        novel = override == 0

        if open_burst is not None:
            if (novel and open_burst.alert_sent
                    and now - datetime.fromisoformat(open_burst.last_seen_at)
                    >= timedelta(minutes=config.novelty_min_gap_minutes)):
                # A momentum change on a quieted wave: new first alert.
                store.insert_burst(conn, match, now, requires_sources=0)
                log_rows.append(("novel_target", match.class_name,
                                 match.signature, match.item.source_id,
                                 f"new bucket/ring {match.term_bucket}/{match.location_ring}"))
                continue
            if match.item.source_id not in open_burst.source_ids:
                store.add_source(conn, open_burst.id, match.item.source_id,
                                 match.term_bucket, now)
            if novel and not open_burst.alert_sent and open_burst.requires_sources > 0:
                # The held burst's wave gained a novel domain: the hold
                # must not suppress it (novelty restores instantly).
                store.set_requires(conn, open_burst.id, 0)
                log_rows.append(("novel_target", match.class_name,
                                 match.signature, match.item.source_id,
                                 "held burst restored by novel domain"))
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
        # Momentum (owner 2026-08-31): a background bucket needs volume
        # to re-alert; a NOVEL target domain restores instant escalation
        # even inside the quiet window.
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
            # Convergence (WAR_SIGNALS_PAPER 2025-26): one category
            # screaming is a rumor cycle; three ALERTED categories
            # moving within 72h is a war. Deterministic note, never a
            # claim upgrade.
            convergence = ""
            if burst.class_name == "escalation":
                others = history.recent_distinct_buckets(
                    conn, "escalation",
                    store._iso(now - timedelta(hours=72)),
                    exclude_bucket=burst.term_bucket, sent_only=True,
                )
                if len(others) >= 2:
                    convergence = (
                        f"⚠️ همگرایی سیگنال‌ها: {len(others) + 1} دسته "
                        "در ۷۲ ساعت گذشته\n"
                    )
            result = send(frames.render_first(burst, config, now, convergence))
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
    deescalated = momentum.maybe_deescalation(conn, config, now, send, logger)
    open_count = len(store.open_bursts(conn))
    return {"sent": sent + deescalated, "alerts_this_hour": alerts_this_hour,
            "open_bursts": open_count}
