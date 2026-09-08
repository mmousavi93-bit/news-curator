"""Cluster priority and the enforced cluster cap (session-5 decision 1).

Split out of cluster.py 2026-09-05 when it crossed the ~200-line cap
(constraint 12). cluster.py owns the clustering ALGORITHM -- what counts as
one story; this module owns the POLICY -- which stories are worth an LLM
call when there are more of them than the budget allows.

Forensic for the ordering change is in POSTMORTEMS.md (2026-09-05).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.config import Config
from agent.pipeline.relevance import score_relevance

if TYPE_CHECKING:  # import-only: cluster.py imports THIS module at runtime
    from agent.pipeline.cluster import Cluster


# Independence is capped before it enters the sort key: it must protect a
# corroborated story from the cap, not let one big pile-up dominate it. At
# 3+ independent groups two clusters tie and recency breaks them.
_INDEPENDENCE_CEILING = 3


def _cluster_text(cluster: "Cluster") -> str:
    return "\n".join(f"{member.title}\n{member.body}" for member in cluster.members)


def _priority_key(config: Config):
    """Sort key: ON-MISSION (binary) desc, then tier weight desc, then
    CORROBORATING SOURCE COUNT desc (capped), then recency desc, then size
    desc. Full ordering -- no coin flips (determinism requirement).

    on_mission demoted ahead of tier 2026-09-06 (fix 2): the 2026-09-05
    16:18 run made tier an absolute wall, cutting 24 of 40 kept clusters
    that later came back `irrelevant`/`clickbait` while corroborated
    Iranian-naval-escalation clusters were cut with NO LLM call at all.
    on_mission is a DEMOTION, never a drop: an off-mission cluster still
    gets a cap slot on a quiet day, it just sinks below every on-mission
    cluster regardless of tier. Deliberately BINARY -- using the relevance
    TIER VALUE here was tried and regressed (iran_direct's keyword list
    fires on gossip too; relevance is a gate/sort signal for the DIGEST,
    Config.relevance's config/relevance.yaml, not a cap-ordering scale).

    Independence moved ahead of recency 2026-09-05, and its source swapped
    2026-09-06 (fix 3) from independent_count (all tiers, group: null
    fallback -- 17 owner TG channels could out-rank a genuine corroborated
    story by repost volume) to corroborating_count (tier-1/2 groups only,
    rulebook Step 1). The old key was (tier, recency, size): size was the
    last tiebreak and, because timestamps are effectively always distinct,
    it never fired -- inside a tier the ranking was pure recency. That cut
    36 of 76 clusters on the 16:18 run and left 23 single-member clusters
    standing purely because they were fresher.

    on_mission gated on tier_weight > 0, 2026-09-06 (round-3 review, fix 1c):
    a `lead` cluster (weight 0.0, LEAD_HANDLING.md -- leads cannot drive
    priority) that happens to match a relevance keyword must not be promoted
    ahead of an off-mission tier-2 cluster just because on_mission is the
    top sort term. Regardless of what score_relevance returns, a cluster
    whose max_tier_weight is 0 (all-lead) always sorts as off-mission.
    """
    multipliers = config.settings.scoring.tier_multipliers
    credibility = config.credibility
    relevance = config.relevance

    def key(cluster: "Cluster") -> tuple[int, float, int, float, int]:
        tier_weight = cluster.max_tier_weight(credibility, multipliers)
        on_mission = 1 if (
            tier_weight > 0
            and score_relevance(relevance, _cluster_text(cluster)) > 0
        ) else 0
        return (
            -on_mission,
            -tier_weight,
            -min(cluster.corroborating_count(credibility), _INDEPENDENCE_CEILING),
            -cluster.latest().timestamp(),
            -len(cluster.members),
        )

    return key


def split_at_cap(
    clusters: list["Cluster"],
    max_clusters: int,
    config: Config,
    logger: logging.Logger,
) -> tuple[list["Cluster"], list["Cluster"]]:
    """Priority-ordered spending. Returns (kept, dropped), highest priority
    first, `kept` capped at `max_clusters`. Overflow is DROPPED, never
    deferred: a budget that only fires on the busiest news day must not rely
    on tomorrow being quieter.

    `dropped` is returned rather than discarded so report_csv can write a
    `cap_dropped` row per cluster. Before 2026-09-05 the overflow left only
    a line of hex keys in the log -- 36 clusters a run with no sources and
    no titles, so there was no way to tell whether the cap was cutting noise
    or cutting the story of the day.
    """
    ranked = sorted(clusters, key=_priority_key(config))
    if len(ranked) <= max_clusters:
        return ranked, []
    kept, dropped = ranked[:max_clusters], ranked[max_clusters:]
    logger.error(
        "cluster cap: %d clusters, keeping %d, dropping %d "
        "(see cap_dropped rows in chosen.csv); keys: %s",
        len(clusters), len(kept), len(dropped),
        ", ".join(c.key for c in dropped),
    )
    return kept, dropped


def rank_and_truncate(
    clusters: list["Cluster"],
    max_clusters: int,
    config: Config,
    logger: logging.Logger,
) -> list["Cluster"]:
    """`split_at_cap` keeping only the survivors -- the simple call shape
    for callers that do not need the overflow."""
    return split_at_cap(clusters, max_clusters, config, logger)[0]
