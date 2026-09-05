"""Provider health across runs — the memory behind the health-aware
cascade (owner-approved move 2, 2026-08-31).

The 2026-08-31 05:10 UTC run shows why: Gemini was 50% throttled
(3 ok / 3x 429) and every cluster started with a doomed Gemini attempt
before rotating to Groq. Persisting per-provider calls/failed in the
state DB lets the NEXT run start on the provider that actually worked.

Deterministic rules (identical state + identical stats -> identical
order): a provider with >= MIN_SAMPLES calls and fail_rate >= 0.5 in
the 7-day window is DEMOTED to the end of the cascade; providers with
no key/config are skipped by build_adapters regardless. Demotion only
ever costs the configured order on days a provider is measurably sick.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Mapping, Sequence

_META_KEY = "provider_health_v1"
_DAILY_KEY = "provider_daily_v1"
_WINDOW_DAYS = 7
_MIN_SAMPLES = 4
_FAIL_RATE = 0.5
_MAX_CALLS = 200  # window reset point: keeps the JSON bounded


def _quota_day(now: datetime) -> str:
    """Day key aligned to Google's quota reset (midnight America/Los_Angeles).

    Gemini free RPD resets at Pacific midnight (~07:00 UTC in Sept), but the
    pipeline's canonical 09:00 Tehran digest runs at 05:30 UTC -- 1.5h BEFORE
    that reset. A UTC day key would therefore hand the digest a quota Google
    is still counting against the PRIOR Pacific day, and the 20 calls would
    429 on exactly the run that matters most. ZoneInfo needs tzdata; fall
    back to a fixed UTC-7 (PDT) offset if it is absent -- the worst error is
    a 1h reset shift, never a cap violation.
    """
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:  # noqa: BLE001 -- tzdata absent: degrade, don't crash
        return (now - timedelta(hours=7)).date().isoformat()


def load_health(conn: sqlite3.Connection) -> dict[str, dict]:
    """{provider: {"calls": int, "failed": int}, "_last_run": iso}. Empty
    when the meta key is absent or corrupt — an unreadable health record
    degrades to the configured order, never crashes the run."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (_META_KEY,)).fetchone()
    if row is None:
        return {}
    try:
        data = json.loads(row["value"])
        if isinstance(data, dict) and "_last_run" in data:
            return data
    except (ValueError, TypeError):
        pass
    return {}


def save_health(conn: sqlite3.Connection, stats: Mapping[str, Mapping[str, int]],
                now: datetime) -> None:
    stored = load_health(conn)
    last = stored.get("_last_run")
    fresh = stored
    if last is not None:
        try:
            age = now - datetime.fromisoformat(last)
            if age > timedelta(days=_WINDOW_DAYS):
                fresh = {}
        except ValueError:
            fresh = {}
    merged: dict[str, dict] = {"_last_run": now.isoformat()}
    for name, entry in stats.items():
        calls = int(entry.get("calls", 0)) + int(fresh.get(name, {}).get("calls", 0))
        failed = int(entry.get("failed", 0)) + int(fresh.get(name, {}).get("failed", 0))
        if calls > _MAX_CALLS:
            calls, failed = 0, 0  # bounded window: restart the sample
        merged[name] = {"calls": calls, "failed": failed}
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_META_KEY, json.dumps(merged, sort_keys=True)),
    )
    conn.commit()


def load_daily(conn: sqlite3.Connection, now: datetime) -> dict[str, int]:
    """{provider: calls_so_far_today} on the UTC date of `now`. Empty when
    the key is absent/corrupt, or when a provider's record is from a prior
    day (the day rolled over -- yesterday's spend does not count against
    today's free-tier quota). Degrades to "no history", which only means
    the cap starts from zero -- never crashes the run."""
    day = _quota_day(now)
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (_DAILY_KEY,)).fetchone()
    if row is None:
        return {}
    try:
        data = json.loads(row["value"])
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for name, entry in data.items():
        if isinstance(entry, dict) and entry.get("date") == day:
            out[name] = int(entry.get("calls", 0))
    return out


def save_daily(conn: sqlite3.Connection, stats: Mapping[str, Mapping[str, int]],
               now: datetime) -> None:
    """Accumulate today's per-provider ATTEMPTS (stats["calls"]) onto the
    persisted daily record. A new quota day replaces the record wholesale
    (only providers in `stats` are written -- the rest of today's tally is
    carried forward from `load_daily`). The router reads this via
    `load_daily` at build time so a provider's per-run cap is today's
    remaining allowance, not the full day's (the router is stateless
    between runs by construction). Empty stats (mock/dry-run, or no
    providers built) does NOT write: a debugging run must not wipe the
    persisted quota record."""
    if not stats:
        return
    day = _quota_day(now)
    prior = load_daily(conn, now)
    merged: dict[str, dict] = {}
    for name, entry in stats.items():
        calls = int(entry.get("calls", 0)) + prior.get(name, 0)
        merged[name] = {"date": day, "calls": calls}
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_DAILY_KEY, json.dumps(merged, sort_keys=True)),
    )
    conn.commit()


def cascade_order(configured: Sequence[str],
                  health: Mapping[str, Mapping]) -> list[str]:
    """Configured order with measurably-sick providers demoted to the end.
    Unknown providers (in health but not configured) are ignored."""
    sick = {
        name for name, entry in health.items()
        if name != "_last_run"
        and isinstance(entry, Mapping)
        and entry.get("calls", 0) >= _MIN_SAMPLES
        and entry.get("failed", 0) / max(entry.get("calls", 1), 1) >= _FAIL_RATE
    }
    return ([p for p in configured if p not in sick]
            + [p for p in configured if p in sick])
