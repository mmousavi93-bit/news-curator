"""The 24h digest queue (Session 25).

The digest is no longer "everything above min_score that fits". It is the top
`max_items` from a 24h POOL: this run's fresh events PLUS prior runs' events
that were ranked but not delivered. A story ranked #6 this morning stays a
candidate and can rise to #2 this afternoon once the morning's top-5 are
delivered and gone -- it is deferred, never discarded. Rows expire after
`queue_window_hours` or on delivery.

Why a separate table: the `events` table stores only the persisted subset
(summary/entities/claim/counts); the digest needs the in-memory fields too
(headline/category/significance/why_matters/follow_up/prior_context) to
re-render a deferred event with full fidelity. `pending.event_json` is the
whole Event; `score` is the rank-time score (a later run has no cluster to
recompute it from). No LLM, no network -- deterministic.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from agent.memory.event_json import event_from_json, event_to_json
from agent.memory.event_models import Event

_EXPIRY_PREFIX = "digest_queue_expired:"


def load_pending(ctx, now) -> list[tuple[Event, float]]:
    """Eligible undelivered snapshots, strictly younger than the lifetime."""
    db = getattr(ctx, "db", None)
    if db is None:
        return []
    cutoff = (now - timedelta(hours=ctx.config.settings.digest_rank.queue_window_hours)).isoformat()
    rows = db.execute(
        "SELECT event_json, score FROM pending WHERE ranked_at > ? "
        "AND event_key NOT IN (SELECT event_key FROM delivered)", (cutoff,),
    ).fetchall()
    return [(event_from_json(row[0]), float(row[1])) for row in rows]


def store_pending(ctx, entries: list[tuple[Event, float]], now) -> None:
    """Persist admissions; refresh payload/score without renewing ranked_at.

    Production SQLite autocommit makes each admission durable before send.
    Expired keys cannot be readmitted while their memory marker exists.
    """
    db = getattr(ctx, "db", None)
    if db is None or not entries:
        return
    db.executemany(
        "INSERT INTO pending (event_key, event_json, score, ranked_at) "
        "SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM meta WHERE key = ?) "
        "ON CONFLICT(event_key) DO UPDATE SET event_json=excluded.event_json, score=excluded.score",
        [(e.event_key, event_to_json(e), score, now.isoformat(), _EXPIRY_PREFIX + e.event_key)
         for e, score in entries],
    )


def prune_pending(ctx, now) -> None:
    """Remember terminal expiry before deleting the full snapshot.

    Compact key/time markers use existing meta, not a schema change. They
    expire after retention.events_days from observed expiry, consistent with
    the existing finite event/delivery memory. Never gate on publish time.
    With autocommit, markers are durable BEFORE snapshot deletion.
    """
    ctx.queue_expired = []
    db = getattr(ctx, "db", None)
    if db is None:
        return
    memory_cutoff = (now - timedelta(days=ctx.config.settings.retention.events_days)).isoformat()
    db.execute("DELETE FROM meta WHERE key GLOB ? AND value <= ?",
               (_EXPIRY_PREFIX + "*", memory_cutoff))
    cutoff = (now - timedelta(hours=ctx.config.settings.digest_rank.queue_window_hours)).isoformat()
    rows = db.execute(
        "SELECT event_key, event_json FROM pending WHERE ranked_at <= ? "
        "AND event_key NOT IN (SELECT event_key FROM delivered)", (cutoff,),
    ).fetchall()
    ctx.queue_expired = [event_from_json(row[1]) for row in rows]
    db.executemany("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                   [(_EXPIRY_PREFIX + row[0], now.isoformat()) for row in rows])
    db.execute("DELETE FROM pending WHERE ranked_at <= ?", (cutoff,))
    db.execute("DELETE FROM pending WHERE event_key IN (SELECT event_key FROM delivered)")


def select_digest(ctx, fresh: list[Event], settings, logger: logging.Logger) -> list[Event]:
    """Select at most max_items from admissions, never from publish age.

    Same-key updates preserve first admission. Terminal-key memory prevents
    replay after pruning, even without a snapshot publication timestamp.
    Fresh-vs-fresh same-key behavior and stored-score policy are unchanged.
    """
    from agent.pipeline.rank import score_event

    clusters = {c.key: c for c in getattr(ctx, "clusters", None) or []}
    prune_pending(ctx, ctx.now)
    db = getattr(ctx, "db", None)
    expired_keys = {r[0][len(_EXPIRY_PREFIX):] for r in db.execute(
        "SELECT key FROM meta WHERE key GLOB ?", (_EXPIRY_PREFIX + "*",)
    )} if db is not None else set()
    reported_expiry = {e.event_key for e in ctx.queue_expired}
    ctx.queue_expired.extend(e for e in fresh if e.event_key in expired_keys - reported_expiry)
    acknowledged = {r[0] for r in db.execute("SELECT event_key FROM delivered")} if db else set()
    fresh_scored = [
        (e, score_event(e, clusters.get(e.event_key), ctx.config.credibility, settings, ctx.now))
        for e in fresh if e.event_key not in expired_keys | acknowledged
    ]
    store_pending(ctx, fresh_scored, ctx.now)
    pending_by_key = {e.event_key: (e, s) for e, s in load_pending(ctx, ctx.now)}
    ctx.queue_pending_keys = set(pending_by_key) - {e.event_key for e, _ in fresh_scored}
    pool = list(fresh_scored)
    for e, _ in fresh_scored:
        pending_by_key.pop(e.event_key, None)
    pool.extend(pending_by_key.values())
    pool.sort(key=lambda es: (-es[1], es[0].event_key))
    top = [e for e, _ in pool[:settings.digest_rank.max_items]]
    selected_keys = {e.event_key for e in top}
    ctx.queue_deferred = [e for e, _ in pool if e.event_key not in selected_keys]
    ctx.queue_scores = {e.event_key: score for e, score in pool}
    ctx.queue_selected = top
    logger.info("queue: %d fresh admitted, %d selected, %d deferred, %d expired",
                len(fresh_scored), len(top), len(ctx.queue_deferred), len(ctx.queue_expired))
    return top
