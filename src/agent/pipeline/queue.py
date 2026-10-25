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

from agent.memory.event_models import Event, event_from_json, event_to_json


def load_pending(ctx, now) -> list[tuple[Event, float]]:
    """Pending events within `queue_window_hours`, not yet delivered, with
    their stored scores. Returns [] when ctx.db is absent (dry-run/mock)."""
    db = getattr(ctx, "db", None)
    if db is None:
        return []
    window = ctx.config.settings.digest_rank.queue_window_hours
    cutoff = (now - timedelta(hours=window)).isoformat()
    rows = db.execute(
        "SELECT event_key, event_json, score FROM pending "
        "WHERE ranked_at >= ? AND event_key NOT IN "
        "(SELECT event_key FROM delivered)",
        (cutoff,),
    ).fetchall()
    return [(event_from_json(row[1]), float(row[2])) for row in rows]


def store_pending(ctx, entries: list[tuple[Event, float]], now) -> None:
    """Upsert `entries` (event, score) into the queue, ranked at `now`."""
    db = getattr(ctx, "db", None)
    if db is None or not entries:
        return
    db.executemany(
        "INSERT OR REPLACE INTO pending (event_key, event_json, score, ranked_at) "
        "VALUES (?, ?, ?, ?)",
        [(e.event_key, event_to_json(e), score, now.isoformat())
         for e, score in entries],
    )


def prune_pending(ctx, now) -> None:
    """Delete expired and already-delivered pending rows (bounded growth)."""
    db = getattr(ctx, "db", None)
    if db is None:
        return
    window = ctx.config.settings.digest_rank.queue_window_hours
    cutoff = (now - timedelta(hours=window)).isoformat()
    db.execute("DELETE FROM pending WHERE ranked_at < ?", (cutoff,))
    db.execute(
        "DELETE FROM pending WHERE event_key IN (SELECT event_key FROM delivered)"
    )


def select_digest(ctx, fresh: list[Event], settings, logger: logging.Logger) -> list[Event]:
    """Top `max_items` from the 24h pool (fresh + pending), score-sorted.

    A fresh event supersedes a stale pending copy with the same event_key.
    Fresh events below the cut are persisted to the queue (deferred, never
    dropped); the queue is pruned of expired/delivered rows. Returns the
    selected Events in score order -- compose renders them directly."""
    from agent.pipeline.rank import score_event

    clusters = {c.key: c for c in getattr(ctx, "clusters", None) or []}
    credibility = ctx.config.credibility
    now = ctx.now
    fresh_scored = [
        (e, score_event(e, clusters.get(e.event_key), credibility, settings, now))
        for e in fresh
    ]
    by_key: dict[str, tuple[Event, float]] = {}
    for e, s in load_pending(ctx, now):
        by_key[e.event_key] = (e, s)
    for e, s in fresh_scored:
        by_key[e.event_key] = (e, s)  # fresh supersedes a stale pending copy
    pool = sorted(by_key.values(), key=lambda es: (-es[1], es[0].event_key))
    top = [e for e, _ in pool[: settings.digest_rank.max_items]]
    top_keys = {e.event_key for e in top}
    deferred = [(e, s) for e, s in fresh_scored if e.event_key not in top_keys]
    if deferred:
        store_pending(ctx, deferred, now)
    prune_pending(ctx, now)
    if len(fresh_scored) > len(top):
        logger.info(
            "queue: %d fresh ranked -> %d delivered, %d deferred to 24h queue",
            len(fresh_scored), len(top), len(deferred),
        )
    return top
