"""De-escalation notice for the 3-hourly digest (session 9z).

The flash used to announce de-escalation 📉 -- when the escalation class had
ALERTED on >=3 distinct Tehran-days in the last 30 and then stayed quiet for
>= quiet_days. When the flash was redesigned to be tehran-only (9y), that
notice was dropped. This module restores it on the digest side, using the
digest's own definition of escalation: a military/security event that cleared
the strategic relevance tier (a regional anchor or Iran is named -- the same
"detection is not relevance" gate rank applies).

Deterministic and zero-LLM. State is one meta row (JSON) in the digest's
state.db; missing or corrupt state means "no history yet", never a crash --
the notice is cosmetic, not load-bearing.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from agent.collectors.tz import to_tehran
from agent.config import ConfigError
from agent.pipeline.rank import event_text
from agent.pipeline.relevance import score_relevance
from agent.util.jalali import to_persian_digits

ESCALATION_CATEGORIES = frozenset({"military", "security"})

_META_KEY = "deescalation_v1"

_INT_FIELDS = ("streak_days", "window_days", "quiet_days", "cooldown_days")
_KNOWN_KEYS = frozenset(("enabled",) + _INT_FIELDS)


@dataclass(frozen=True, slots=True)
class DeescalationConfig:
    enabled: bool
    streak_days: int
    window_days: int
    quiet_days: int
    cooldown_days: int


def validate_deescalation(raw: object) -> DeescalationConfig:
    """Strict shape: {enabled: bool, streak_days/window_days/quiet_days/
    cooldown_days: positive int}. Raises ConfigError listing every problem
    once -- never echoes a value (some day a value may be sensitive)."""
    if not isinstance(raw, dict):
        raise ConfigError(
            f"deescalation.yaml: expected a mapping, got {type(raw).__name__}"
        )
    errors: list[str] = []
    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        errors.append(f"deescalation.yaml: unknown key(s) {unknown}")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        errors.append(
            f"deescalation.yaml: 'enabled' must be a boolean, "
            f"got {type(enabled).__name__}"
        )
    values: dict[str, int] = {}
    for field in _INT_FIELDS:
        v = raw.get(field)
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            errors.append(
                f"deescalation.yaml: '{field}' must be a positive integer, "
                f"got {type(v).__name__}"
            )
            v = 1
        values[field] = v
    if errors:
        raise ConfigError("; ".join(errors))
    return DeescalationConfig(enabled=bool(enabled), **values)


def is_escalation(event, relevance) -> bool:
    """An event is escalation iff its category is military/security AND it
    cleared the strategic relevance tier (>= strategic weight). No config
    loaded (direct Config in tests) falls back to category-only."""
    if event.category not in ESCALATION_CATEGORIES:
        return False
    if relevance is None:
        return True
    threshold = relevance.weights.get("strategic", relevance.min_relevance)
    return score_relevance(relevance, event_text(event)) >= threshold


def _load_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read the de-escalation meta row. Missing/corrupt -> empty state."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (_META_KEY,)
        ).fetchone()
        if row is None:
            return {}
        state = json.loads(row[0])
        return state if isinstance(state, dict) else {}
    except (json.JSONDecodeError, sqlite3.Error):
        return {}


def _save_state(conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (_META_KEY, json.dumps(state, ensure_ascii=False)),
    )


def _parse_day(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def maybe_deescalation_notice(
    conn: sqlite3.Connection | None,
    now: datetime,
    config: DeescalationConfig | None,
    labels: Mapping[str, str],
) -> str | None:
    """Return the 📉 notice text, or None. Fires once per cooldown when
    escalation was delivered on >= streak_days distinct days in the last
    window_days AND has been quiet for >= quiet_days days."""
    if conn is None or config is None or not config.enabled:
        return None
    state = _load_state(conn)
    today = to_tehran(now).date()
    days: list[date] = []
    for raw in state.get("days", []):
        if isinstance(raw, str):
            d = _parse_day(raw)
            if d is not None and d < today and (today - d).days <= config.window_days:
                days.append(d)
    days.sort()
    if len(days) < config.streak_days:
        return None
    quiet_days = (today - days[-1]).days
    if quiet_days < config.quiet_days:
        return None
    last_notice = state.get("last_notice_at")
    if isinstance(last_notice, str):
        try:
            since_notice = now - datetime.fromisoformat(last_notice)
            if since_notice < timedelta(days=config.cooldown_days):
                return None
        except (ValueError, TypeError):
            pass  # corrupt timestamp -> treat as never notified
    return labels["deescalation"].format(days=to_persian_digits(str(quiet_days)))


def record_escalation_day(
    conn: sqlite3.Connection | None, now: datetime, config: DeescalationConfig | None
) -> None:
    """Mark today as an escalation-delivered day (called by deliver after a
    real, all-successful send). Idempotent per day; prunes to window_days."""
    if conn is None or config is None or not config.enabled:
        return
    state = _load_state(conn)
    today = to_tehran(now).date()
    days: list[date] = []
    for raw in state.get("days", []):
        if isinstance(raw, str):
            d = _parse_day(raw)
            if d is not None:
                days.append(d)
    if today not in days:
        days.append(today)
    cutoff = today - timedelta(days=config.window_days)
    days = [d for d in days if d >= cutoff]
    state["days"] = sorted(d.isoformat() for d in days)
    _save_state(conn, state)


def mark_notice_sent(conn: sqlite3.Connection | None, now: datetime) -> None:
    """Stamp the cooldown clock after the digest carrying the notice sent."""
    if conn is None:
        return
    state = _load_state(conn)
    state["last_notice_at"] = now.isoformat()
    _save_state(conn, state)
