"""Digest ranking -- deterministic importance scoring for MESSAGE ORDER.

Explicitly NOT the risk engine (Phase 11): that scores escalation
probability from the 36-signal catalog; this scores "what should the owner
read first" from what the run already knows -- category, corroboration,
source tier, recency, volume. Same constraint 3 discipline: deterministic
Python, no LLM, identical input -> identical order.

score = category_weight
      + corroboration_weight * min(independent_groups, 3)
      + tier_bonus[best tier among members]
      + recency bonus (decays linearly to 0 at recency_window_hours)
      + size boost (0.1 per extra member, capped)

The defaults' arithmetic is documented in settings.yaml next to min_score.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Mapping, Sequence

from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.settings import Settings

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_CATEGORIES = frozenset({"military", "security", "politics", "economy", "other"})


def best_tier(cluster: Cluster, credibility: Mapping[str, object]) -> int:
    """Best (lowest-numbered) tier among NON-lead members; 3 if none."""
    best = 3
    for member in cluster.members:
        entry = credibility.get(member.source_id)
        tier = getattr(entry, "tier", None) if entry is not None else None
        if isinstance(tier, int) and tier in (1, 2, 3):
            best = min(best, tier)
    return best


def latest_stamp(cluster: Cluster) -> datetime | None:
    stamps = [m.published_at for m in cluster.members if m.published_at is not None]
    return max(stamps) if stamps else None


def score_event(
    event: Event,
    cluster: Cluster | None,
    credibility: Mapping[str, object],
    settings: Settings,
    now: datetime,
) -> float:
    """Deterministic importance score. Higher = more important."""
    cfg = settings.digest_rank
    category = event.category if event.category in _CATEGORIES else "other"
    score = float(cfg.category_weights.get(category, 0))
    score += cfg.corroboration_weight * min(event.independent_count, 3)
    score += float(cfg.tier_bonus.get(best_tier(cluster, credibility), 0)) \
        if cluster is not None else 0.0
    if cluster is not None:
        stamp = latest_stamp(cluster)
        if stamp is not None:
            hours = max(0.0, (now - stamp).total_seconds() / 3600.0)
            decay = max(0.0, 1.0 - hours / cfg.recency_window_hours)
            score += cfg.recency_max_bonus * decay
        score += min(
            cfg.size_boost_per_member * max(0, len(cluster.members) - 1),
            cfg.size_boost_cap,
        )
    return round(score, 3)


def rank_events(
    events: Sequence[Event],
    clusters_by_key: Mapping[str, Cluster],
    credibility: Mapping[str, object],
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
) -> tuple[list[Event], list[Event]]:
    """Sorts events by score desc (recency, then key, as tie-breaks) and
    splits at min_score. Returns (kept, dropped). Dropped events never
    reach the message -- logged once with their scores."""
    def _recency(event: Event) -> float:
        cluster = clusters_by_key.get(event.event_key)
        stamp = latest_stamp(cluster) if cluster else None
        return stamp.timestamp() if stamp else 0.0

    scored = sorted(
        events,
        key=lambda e: (
            -score_event(e, clusters_by_key.get(e.event_key), credibility, settings, now),
            -_recency(e),
            e.event_key,
        ),
    )
    kept = [e for e in scored if score_event(
        e, clusters_by_key.get(e.event_key), credibility, settings, now
    ) >= settings.digest_rank.min_score]
    dropped = [e for e in scored if e not in kept]
    if dropped:
        logger.info(
            "rank: %d event(s) below min_score %.1f -- not in the digest: %s",
            len(dropped), settings.digest_rank.min_score,
            ", ".join(e.event_key[:8] for e in dropped),
        )
    return kept, dropped
