"""Digest ranking -- significance gate + deterministic importance + significance sort.

Owner decision 2026-09-18 (Session 17): relevance is no longer "which country
does the story mention" (the keyword tiers in config/relevance.yaml). The
understand model's own judgment -- a structured `significance` field -- is the
relevance signal now:

  escalation = Iran directly in play (strikes on Iran, Iranian offensive
               action, nuclear/enrichment/IAEA/JCPOA, decapitation of a
               senior figure).
  balance    = the war's balance shifts WITHOUT Iran hit directly: a
               strategic asset destroyed/captured (fortification, tunnel,
               checkpoint, base, radar), force posture, realignment.
  economy    = oil, sanctions, the rial, fuel, markets.
  none       = routine combat with no strategic consequence ("war
               continues"), or anything that does not move the war picture.
               Israel/Gaza and Lebanon bombardment default here.

  gate: an event the model judged `significance: none` never reaches the
        digest. Missing/invalid significance falls back to `economy`
        (keep-and-rank-low) -- dropping is irreversible, under-ranking is
        recoverable.
  sort: significance weight + category + corroboration + tier + recency +
        volume, descending. Identical input -> identical order (deterministic
        Python, no LLM in the ranker).

score = significance_weight      (escalation / balance / economy / none)
      + category_weight
      + corroboration_weight * min(independent_groups, 3)
      + tier_bonus[best tier among members]
      + recency bonus (decays linearly to 0 at recency_window_hours)
      + size boost (0.1 per extra member, capped)

The defaults' arithmetic is documented in settings.yaml next to min_score.
The keyword relevance scorer (config/relevance.yaml) is no longer used here:
it survives ONLY for priority.py's pre-understand on_mission triage and
deescalation.py's is_escalation -- neither is digest ranking.
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
_SIGNIFICANCE = frozenset({"escalation", "balance", "economy", "none"})


def event_text(event: Event) -> str:
    """The text the (keyword) relevance gate matches over: headline, summary,
    entities. Kept for deescalation.py, which still uses keyword relevance."""
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


def significance_weight(event: Event, settings: Settings) -> float:
    """The event's war-picture impact weight (owner 2026-09-18, Session 17).
    Missing or unknown significance falls back to `economy` -- keep and rank
    low, never a silent drop: dropping is irreversible, under-ranking is
    recoverable."""
    sig = event.significance if event.significance in _SIGNIFICANCE else "economy"
    return float(settings.digest_rank.significance_weights.get(sig, 0.0))


def score_event(
    event: Event,
    cluster: Cluster | None,
    credibility: Mapping[str, object],
    settings: Settings,
    now: datetime,
) -> float:
    """Deterministic importance + significance score. Higher = more important.

    Significance is read from `event.significance` (the understand model's
    war-picture judgment), not passed in -- so the digest sort and the
    summaries.csv writer compute the SAME score from the SAME signal."""
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
    score += significance_weight(event, settings)
    return round(score, 3)


def event_order_key(
    event: Event,
    clusters_by_key: Mapping[str, Cluster],
    credibility: Mapping[str, object],
    settings: Settings,
    now: datetime,
) -> tuple:
    """The digest sort key: significance-weighted score desc, then recency
    desc, then key. Shared by rank_events and the summaries.csv writer, so
    `rank` in the CSV is the position the reader actually sees."""
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
) -> tuple[list[Event], list[Event], list[Event]]:
    """Significance gate (drop `none`), then significance-weighted importance
    sort, then min_score split.

    Returns (kept, below_min_score, significance_none). The two drop reasons
    are separated so the observability CSVs record the real fate -- a
    significance-gated event (war-picture noise) and a low-importance event
    are different diagnoses, and merging them hides which lever to tune."""
    passing, gated = [], []
    for e in events:
        sig = e.significance if e.significance in _SIGNIFICANCE else "economy"
        (gated if sig == "none" else passing).append(e)
    if gated:
        logger.info(
            "rank: %d event(s) significance=none -- not in the digest: %s",
            len(gated), ", ".join(e.event_key[:8] for e in gated),
        )

    scored = sorted(
        passing,
        key=lambda e: event_order_key(e, clusters_by_key, credibility, settings, now),
    )
    kept = [e for e in scored if score_event(
        e, clusters_by_key.get(e.event_key), credibility, settings, now,
    ) >= settings.digest_rank.min_score]
    dropped = [e for e in scored if e not in kept]
    if dropped:
        logger.info(
            "rank: %d event(s) below min_score %.1f -- not in the digest: %s",
            len(dropped), settings.digest_rank.min_score,
            ", ".join(e.event_key[:8] for e in dropped),
        )
    return kept, dropped, gated
