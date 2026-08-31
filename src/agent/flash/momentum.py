"""The momentum layer (owner 2026-08-31): escalation detected FAST with
false positives accepted; de-escalation announced SLOWLY and only when
sure. This is WAR_SIGNALS_PAPER's "repetition discounts, novelty
restores" made deterministic.

Rules (all deterministic, config-owned in flash_alert.yaml):

- A bucket (e.g. escalation/strike) that ALERTED on >=
  momentum.streak_repeat_threshold_days distinct Tehran-days within
  momentum.streak_window_days is BACKGROUND: day 1-2 the same
  attack-and-response pattern is escalation; day 3+ at the same
  intensity it is the new normal. Background buckets re-alert only at
  >= momentum.repeat_requires_sources sources.
- NOVELTY RESTORES: a burst whose location token has NOT been alerted
  for that bucket in the streak window is a WIDENING TARGET DOMAIN --
  full escalation again, fires at 1 source even mid-streak and even
  inside the quiet window. A NEW bucket is novel by construction.
- DE-ESCALATION: only when the escalation class ALERTED on >= 3 distinct
  Tehran-days in the last 30 and has now been quiet for >=
  deescalation.quiet_days days -- then ONE 📉 notice per cooldown.
  Never-sent bursts do NOT count: a pattern the owner never saw cannot
  de-escalate (reviewer finding 2026-08-31). The asymmetry is the
  owner's: escalation guesses fast, de-escalation certifies slowly.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from agent.collectors.dates import to_tehran
from agent.flash import history, store
from agent.flash.config import FlashConfig

_DEESCALATION_KEY = "last_deescalation_notice"
_PRIOR_ACTIVITY_WINDOW_DAYS = 30


def _day(ts: str) -> str:
    return to_tehran(datetime.fromisoformat(ts)).strftime("%Y-%m-%d")


def _tehran_today(now: datetime) -> str:
    return to_tehran(now).strftime("%Y-%m-%d")


def bucket_streak_days(conn: sqlite3.Connection, class_name: str,
                       bucket: str, now: datetime, config: FlashConfig) -> int:
    since = store._iso(now - timedelta(days=config.momentum_streak_window_days))
    return len({_day(ts) for ts in history.first_seen_since(
        conn, class_name, bucket, since, sent_only=True)})


def novel_location(conn: sqlite3.Connection, class_name: str, bucket: str,
                   location_token: str, now: datetime, config: FlashConfig) -> bool:
    since = store._iso(now - timedelta(days=config.momentum_streak_window_days))
    return location_token not in history.location_tokens_since(
        conn, class_name, bucket, since)


def requires_override(conn: sqlite3.Connection, class_name: str, bucket: str,
                      location_token: str, now: datetime,
                      config: FlashConfig) -> int | None:
    """None = keep the quiet-window result. 0 = novel target domain, fire
    immediately. >=repeat_requires_sources = background bucket, need
    volume to re-alert."""
    if novel_location(conn, class_name, bucket, location_token, now, config):
        return 0
    if bucket_streak_days(conn, class_name, bucket, now, config) >= \
            config.momentum_streak_repeat_threshold_days:
        return config.momentum_repeat_requires_sources
    return None


def maybe_deescalation(conn: sqlite3.Connection, config: FlashConfig,
                       now: datetime, send, logger: logging.Logger) -> int:
    """Sends the one-time 📉 notice when a previously ALERTED escalation
    pattern has stayed quiet for deescalation.quiet_days. Returns 1 when
    sent, else 0."""
    last_notice = history.get_meta(conn, _DEESCALATION_KEY)
    if last_notice is not None:
        since_notice = now - datetime.fromisoformat(last_notice)
        if since_notice < timedelta(days=config.deescalation_cooldown_days):
            return 0
    since = store._iso(now - timedelta(days=_PRIOR_ACTIVITY_WINDOW_DAYS))
    activity = history.first_seen_since(conn, "escalation", None, since,
                                        sent_only=True)
    if not activity:
        return 0  # no alerted pattern to de-escalate from
    days = sorted({_day(ts) for ts in activity})
    if len(days) < 3:
        return 0  # fewer than 3 active days = no sustained pattern
    quiet_days = (datetime.fromisoformat(_tehran_today(now))
                  - datetime.fromisoformat(days[-1])).days
    if quiet_days < config.deescalation_quiet_days:
        return 0
    result = send(config.templates["deescalation"].format(n=quiet_days))
    if not result.ok:
        return 0
    history.set_meta(conn, _DEESCALATION_KEY, store._iso(now))
    logger.warning("flash: DE-ESCALATION notice sent (%d quiet days)", quiet_days)
    return 1
