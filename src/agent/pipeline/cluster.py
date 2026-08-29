"""Greedy cosine clustering with deterministic ordering, a priority rank,
and the enforced cluster cap (session-5 decision 1).

Determinism is a hard requirement: identical input must produce identical
clusters, or Phase 10's tuning and every trend comparison become noise.
Input items are sorted by a fixed key before clustering; ties break on
url, so there are no coin flips.

The cap: `max_clusters_per_run` was prose until this file. Clusters beyond
the cap are DROPPED, not deferred -- a budget that only fires on the
busiest news day must not rely on tomorrow being quieter. Priority is
tier-then-recency-then-size (tier weights from settings.scoring, already
validated config). Dropped keys are logged once; they get no LLM call.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Sequence

from agent.collectors.base import Item
from agent.config import Config

# Items with no published_at sort as ancient, so undated items cluster last
# rather than crowding out dated coverage.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _sort_key(item: Item) -> tuple[datetime, str, str]:
    return (item.published_at or _EPOCH, item.source_id, item.url)


def _tier_weight(credibility, source_id: str, multipliers: Mapping[int, float]) -> float:
    entry = credibility.get(source_id)
    if entry is None or entry.tier == "lead":
        return 0.0  # leads cannot drive priority (LEAD_HANDLING.md)
    return float(multipliers.get(entry.tier, 0.0))


@dataclass
class Cluster:
    """One story: member items and a running centroid. `key` is the sha256
    of the sorted member urls -- deterministic and unique, used as the
    events.event_key (same cluster on a re-run maps to the same event)."""

    key: str
    members: list[Item] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)
    _count: int = 0

    def add(self, item: Item, vector: Sequence[float]) -> None:
        if not self.members:
            self.centroid = list(vector)
        else:
            n = self._count
            self.centroid = [
                (c * n + v) / (n + 1) for c, v in zip(self.centroid, vector)
            ]
        self.members.append(item)
        self._count += 1
        self.key = self._compute_key()

    def _compute_key(self) -> str:
        urls = sorted(m.url for m in self.members)
        return hashlib.sha256("\n".join(urls).encode("utf-8")).hexdigest()[:16]

    def latest(self) -> datetime:
        return max((m.published_at or _EPOCH for m in self.members), default=_EPOCH)

    def max_tier_weight(self, credibility, multipliers) -> float:
        return max(
            (_tier_weight(credibility, m.source_id, multipliers) for m in self.members),
            default=0.0,
        )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    # Vectors are unit-normalised upstream (FakeEmbedder and MiniLM with
    # normalize_embeddings=True) -- cosine is the dot product.
    return sum(x * y for x, y in zip(a, b))


def cluster_items(
    items: Sequence[Item],
    vectors: Sequence[Sequence[float]],
    threshold: float,
) -> list[Cluster]:
    """Greedy incremental clustering; `items` and `vectors` aligned by index."""
    clusters: list[Cluster] = []
    order = sorted(range(len(items)), key=lambda i: _sort_key(items[i]))
    for index in order:
        vector = vectors[index]
        best: Cluster | None = None
        best_sim = threshold
        for cluster in clusters:
            sim = _cosine(vector, cluster.centroid)
            if sim >= best_sim:
                best = cluster
                best_sim = sim
        if best is not None:
            best.add(items[index], vector)
        else:
            clusters.append(Cluster(key="", members=[], centroid=list(vector)))
            clusters[-1].add(items[index], vector)
    return clusters


def _priority_key(config: Config):
    """Sort key for a cluster: tier weight desc, then recency desc, then
    size desc. Full ordering -- no coin flips (determinism requirement)."""
    multipliers = config.settings.scoring.tier_multipliers
    credibility = config.credibility

    def key(cluster: Cluster) -> tuple[float, float, int]:
        return (
            -cluster.max_tier_weight(credibility, multipliers),
            -cluster.latest().timestamp(),
            -len(cluster.members),
        )

    return key


def rank_and_truncate(
    clusters: list[Cluster],
    max_clusters: int,
    config: Config,
    logger: logging.Logger,
) -> list[Cluster]:
    """Priority-ordered spending (session-5 decision 1): tier, then recency,
    then size. Returns at most `max_clusters` clusters, highest priority
    first -- the order the understand stage should spend calls in. Overflow
    is DROPPED, never deferred: a budget that only fires on the busiest
    news day must not rely on tomorrow being quieter."""
    ranked = sorted(clusters, key=_priority_key(config))
    if len(ranked) <= max_clusters:
        return ranked
    kept = ranked[:max_clusters]
    dropped = ranked[max_clusters:]
    logger.error(
        "cluster cap: %d clusters, keeping %d, dropping %d keys: %s",
        len(clusters), len(kept), len(dropped),
        ", ".join(c.key for c in dropped),
    )
    return kept


class ClusterStage:
    """Embeds -> clusters -> cap. Reads ctx.items and ctx.embeddings
    (aligned by index); writes ctx.clusters in priority order."""

    name = "cluster"

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger

    def run(self, ctx) -> None:
        items = list(getattr(ctx, "items", None) or [])
        vectors = list(getattr(ctx, "embeddings", None) or [])
        if not items:
            ctx.clusters = []
            ctx.counters["cluster"] = 0
            return
        if len(vectors) != len(items):
            # A length mismatch means a stage contract broke; clustering on
            # misaligned data would silently join unrelated stories.
            raise ValueError(f"cluster: {len(items)} items but {len(vectors)} embeddings")
        clusters = cluster_items(
            items, vectors, self._config.settings.pipeline.cluster_similarity_threshold
        )
        clusters = rank_and_truncate(
            clusters, self._config.settings.pipeline.max_clusters_per_run,
            self._config, self._logger,
        )
        ctx.clusters = clusters
        ctx.counters["cluster"] = len(clusters)
        self._logger.info(
            "cluster: %d items -> %d clusters (threshold %.2f)",
            len(items), len(clusters),
            self._config.settings.pipeline.cluster_similarity_threshold,
        )


class EmbedStage:
    """Embeds ctx.items through ctx.embedder into ctx.embeddings."""

    name = "embed"

    def run(self, ctx) -> None:
        items = list(getattr(ctx, "items", None) or [])
        if not items:
            ctx.embeddings = []
            return
        texts = [f"{item.title}\n{item.body}" for item in items]
        ctx.embeddings = ctx.embedder.embed(texts)
