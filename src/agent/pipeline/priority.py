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

if TYPE_CHECKING:  # import-only: cluster.py imports THIS module at runtime
    from agent.pipeline.cluster import Cluster


# Independence is capped before it enters the sort key: it must protect a
# corroborated story from the cap, not let one big pile-up dominate it. At
# 3+ independent groups two clusters tie and recency breaks them.
_INDEPENDENCE_CEILING = 3


def _priority_key(config: Config):
    """Sort key: tier weight desc, then INDEPENDENT SOURCE COUNT desc
    (capped), then recency desc, then size desc. Full ordering -- no coin
    flips (determinism requirement).

    Independence moved ahead of recency 2026-09-05. The old key was
    (tier, recency, size): size was the last tiebreak and, because
    timestamps are effectively always distinct, it never fired -- inside a
    tier the ranking was pure recency. That cut 36 of 76 clusters on the
    16:18 run and left 23 single-member clusters standing purely because
    they were fresher. Everything in a run is roughly "today" (collect drops
    date-only items past 72h), so recency is a weak discriminator here,
    while the number of INDEPENDENT outlets carrying the same story is the
    strongest importance signal available before an LLM has read anything.
    """
    multipliers = config.settings.scoring.tier_multipliers
    credibility = config.credibility

    def key(cluster: "Cluster") -> tuple[float, int, float, int]:
        return (
            -cluster.max_tier_weight(credibility, multipliers),
            -min(cluster.independent_count(credibility), _INDEPENDENCE_CEILING),
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
