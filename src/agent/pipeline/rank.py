"""Digest ranking -- relevance gate + deterministic importance sorting.

Owner decision 2026-08-30 (refined mid-session, validated by two
independent pro-agent evaluations of 9 delivered events): relevance is a
FILTER, importance is the SORT, count is dynamic.

  filter: an event whose highest relevance tier (config/relevance.yaml)
          is below min_relevance never reaches the digest.
  sort:   importance score, descending -- category, corroboration, tier,
          recency, volume. Identical input -> identical order
          (constraint 3 discipline: deterministic Python, no LLM).

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
from agent.pipeline.relevance import RelevanceConfig, passes_gate
from agent.settings import Settings

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_CATEGORIES = frozenset({"military", "security", "politics", "economy", "other"})


def event_text(event: Event) -> str:
    """The text the relevance gate matches over: headline, summary, entities."""
    return " ".join(filter(None, [
        event.headline or "",
        event.summary or "",
        " ".join(getattr(event, "entities", None) or []),
    ]))


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
    """Deterministic IMPORTANCE score. Higher = more important.

    Relevance is not part of this score -- it is the upstream FILTER
    (rank_events drops below-min_relevance events before sorting)."""
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


def event_order_key(
    event: Event,
    clusters_by_key: Mapping[str, Cluster],
    credibility: Mapping[str, object],
    settings: Settings,
    now: datetime,
) -> tuple:
    """The digest sort key: importance score desc, then recency desc, then
    key. Shared by rank_events and the summaries.csv writer, so `rank` in
    the CSV is the position the reader actually sees in the message."""
    cluster = clusters_by_key.get(event.event_key)
    stamp = latest_stamp(cluster) if cluster else None
    return (
        -score_event(event, cluster, credibility, settings, now),
        -(stamp.timestamp() if stamp else 0.0),
        event.event_key,
    )


def rank_events(
    events: Sequence[Event],
    clusters_by_key: Mapping[str, Cluster],
    credibility: Mapping[str, object],
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
    relevance: RelevanceConfig | None = None,
) -> tuple[list[Event], list[Event], list[Event]]:
    """Relevance gate (filter), then importance sort, then min_score split.

    Returns (kept, below_min_score, below_relevance). The two drop reasons
    are separated so the observability CSVs record the real fate -- a
    relevance-gated event and a low-importance event are different
    diagnoses, and merging them hides which lever to tune."""
    if relevance is not None:
        passing, gated = [], []
        for e in events:
            (passing if passes_gate(relevance, event_text(e)) else gated).append(e)
        if gated:
            logger.info(
                "rank: %d event(s) below relevance gate (min_relevance %.1f) -- "
                "not in the digest: %s",
                len(gated), relevance.min_relevance,
                ", ".join(e.event_key[:8] for e in gated),
            )
    else:
        passing, gated = list(events), []

    scored = sorted(
        passing,
        key=lambda e: event_order_key(e, clusters_by_key, credibility, settings, now),
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
    return kept, dropped, gated
