"""Provider health across runs — the memory behind the health-aware cascade
(owner-approved move 2, 2026-08-31): per-provider calls/failed persist in the
state DB so the NEXT run starts on the provider that actually worked.

Rule (identical state + stats -> identical order): >= MIN_SAMPLES calls with
fail_rate >= 0.5 in the 7-day window is DEMOTED to the end; no key/config is
skipped by build_adapters anyway.

Self-healing (2026-09-23, candidate #5): demotion is NOT permanent. A saved run
that leaves a provider sick WITHOUT attempting it (demoted, or quota-skipped by
limits.py) advances a cooldown counter; at DEMOTION_RETRY_AFTER_RUNS it is
re-admitted to its CONFIGURED position and re-demoted on the next failed run,
so gemini's 20 RPD tier cannot stay 0/0.

Demotion only ever costs the configured order on days a provider is measurably
sick: a re-admission is ONE retry at the configured slot (once every
DEMOTION_RETRY_AFTER_RUNS saved runs) and a failing retry re-demotes, so a
transient outage costs at most one doomed attempt per cooldown -- never a
permanent deprioritisation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from agent.util.logging import get_logger

_META_KEY = "provider_health_v1"
_DAILY_KEY = "provider_daily_v1"
_WINDOW_DAYS = 7
_MIN_SAMPLES = 4
_FAIL_RATE = 0.5
_MAX_CALLS = 200  # window reset point: keeps the JSON bounded

# Demotion cooldown, counted in SAVED RUNS: save_health stamps every run, so no
# clock is read here. A retried provider regains only its CONFIGURED slot.
_DEMOTION_RETRY_AFTER_RUNS = 3
SKIP_REASON_DEMOTED = "demoted"


def _quota_day(now: datetime) -> str:
    """Day key on Google's quota reset (midnight America/Los_Angeles): the 05:30
    UTC digest runs 1.5h BEFORE it, so a UTC key would spend a quota Google still
    counts against the PRIOR Pacific day. tzdata may be absent -> fixed UTC-7
    (PDT). The worst error that fallback can cause is a 1h reset shift at the
    PST boundary (UTC-8) -- the key only ever lands on an ADJACENT day, never a
    quota-cap violation."""
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:  # noqa: BLE001 -- tzdata absent: degrade, don't crash
        return (now - timedelta(hours=7)).date().isoformat()


def load_health(conn: sqlite3.Connection) -> dict[str, dict]:
    """{provider: {"calls": int, "failed": int, "model": str|None,
    "demoted_runs": int}, "_last_run": iso}. Empty when the key is absent or
    corrupt (never a crash): degrades to config order."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (_META_KEY,)).fetchone()
    try:
        data = json.loads(row["value"]) if row is not None else {}
    except (ValueError, TypeError):
        data = None
    return data if isinstance(data, dict) and "_last_run" in data else {}


def save_health(conn: sqlite3.Connection, stats: Mapping[str, Mapping[str, int]],
                now: datetime, models: Mapping[str, str] | None = None) -> None:
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
        prev = fresh.get(name, {}) if isinstance(fresh.get(name), Mapping) else {}
        # A model id change means the sample was recorded against a DIFFERENT
        # model: start fresh rather than let a dead alias poison the new one.
        stale_model = (models is not None and prev.get("model") != models.get(name))
        base_calls = 0 if stale_model else int(prev.get("calls", 0))
        base_failed = 0 if stale_model else int(prev.get("failed", 0))
        base_runs = 0 if stale_model else int(prev.get("demoted_runs", 0))
        calls = int(entry.get("calls", 0)) + base_calls
        failed = int(entry.get("failed", 0)) + base_failed
        attempted = int(entry.get("calls", 0)) > 0
        # Judge sickness on the FULL sample BEFORE the window cap trims it:
        # the cap bounds the JSON, it must not erase a demotion by zeroing
        # the sample (a 199/199 provider is sick whether or not we keep count).
        pre = {"calls": calls, "failed": failed}
        if models is not None:
            pre["model"] = models.get(name)
        sick_now = _sick(pre, name, models)
        if calls > _MAX_CALLS:
            calls, failed = 0, 0  # bounded window: restart the sample
        merged[name] = {"calls": calls, "failed": failed}
        if models is not None:
            merged[name]["model"] = models.get(name)
        if sick_now:
            # Cooldown bookkeeping: an ATTEMPTED run that still ended sick
            # restarts the window at 1; a sick-but-unattempted run advances it.
            merged[name]["demoted_runs"] = 1 if attempted else base_runs + 1
        elif base_runs > 0 and not attempted:
            # Demoted but skipped again (or its sample was just reset to 0):
            # the cooldown keeps counting, never dropped by a window reset.
            merged[name]["demoted_runs"] = base_runs + 1
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_META_KEY, json.dumps(merged, sort_keys=True)),
    )
    conn.commit()


def load_daily(conn: sqlite3.Connection, now: datetime) -> dict[str, int]:
    """{provider: calls_so_far_today} for `now`'s quota day. Empty when the key
    is absent/corrupt or the record is from a prior day (spend does not carry)."""
    day = _quota_day(now)
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (_DAILY_KEY,)).fetchone()
    try:
        data = json.loads(row["value"]) if row is not None else {}
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict):
        return {}
    return {n: int(e.get("calls", 0)) for n, e in data.items()
            if isinstance(e, Mapping) and e.get("date") == day}


def save_daily(conn: sqlite3.Connection, stats: Mapping[str, Mapping[str, int]],
               now: datetime) -> None:
    """Accumulate today's per-provider ATTEMPTS (stats["calls"]) onto the
    persisted daily record, so the router seeds each cap from today's remaining
    allowance. Empty stats (mock/dry-run) does NOT write -- no record wiped."""
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


def _sick(entry: Mapping, name: str, models: Mapping[str, str] | None) -> bool:
    """The one demotion rule (also drives save_health's cooldown bookkeeping)."""
    return (entry.get("calls", 0) >= _MIN_SAMPLES
            and entry.get("failed", 0) / max(entry.get("calls", 1), 1) >= _FAIL_RATE
            and (models is None or entry.get("model") == models.get(name)))


def _demotion_state(configured: Sequence[str], health: Mapping[str, Mapping],
                    models: Mapping[str, str] | None) -> tuple[list[str], list[str]]:
    """(still-demoted, cooldown-elapsed) configured providers, in config order."""
    held: list[str] = []
    retried: list[str] = []
    for name in configured:
        entry = health.get(name)
        if not isinstance(entry, Mapping):
            continue
        runs = int(entry.get("demoted_runs", 0))
        # Demoted when the sample says sick, OR a surviving cooldown counter
        # sits on a sample the window cap just zeroed (calls < MIN_SAMPLES) --
        # the reset must not become a free re-admission.
        demoted = (_sick(entry, name, models)
                   or (runs >= 1 and entry.get("calls", 0) < _MIN_SAMPLES))
        if demoted:
            (held if runs < _DEMOTION_RETRY_AFTER_RUNS else retried).append(name)
    return held, retried


def cascade_order(configured: Sequence[str],
                  health: Mapping[str, Mapping],
                  models: Mapping[str, str] | None = None,
                  logger: logging.Logger | None = None) -> list[str]:
    """Configured order with measurably-sick providers demoted to the end.
    Unknown providers are ignored; with `models`, a provider is demoted only if
    its stored sample was recorded against the CURRENTLY configured model (an
    old model id or a legacy record does not indict the freshly-pinned one).

    Self-healing: a provider whose cooldown (DEMOTION_RETRY_AFTER_RUNS saved sick
    runs) has elapsed is re-admitted to its configured position. Each decision is
    logged with its skip reason (`logger` defaults to the module logger)."""
    held, retried = _demotion_state(configured, health, models)
    log = logger or get_logger("agent.llm.health")
    for name in held:
        entry = health[name]
        log.error("llm provider %s: skip_reason=%s -- demoted to the end of the "
                  "cascade (%s/%s calls failed, %s of %d cooldown run(s) done)",
                  name, SKIP_REASON_DEMOTED, entry.get("failed", 0),
                  entry.get("calls", 0), entry.get("demoted_runs", 0),
                  _DEMOTION_RETRY_AFTER_RUNS)
    for name in retried:
        log.info("llm provider %s: skip_reason=%s -- cooldown (%d run(s)) elapsed, "
                 "re-admitted to its configured position; a failing retry is "
                 "re-demoted", name, SKIP_REASON_DEMOTED, _DEMOTION_RETRY_AFTER_RUNS)
    return ([p for p in configured if p not in held]
            + [p for p in configured if p in held])
