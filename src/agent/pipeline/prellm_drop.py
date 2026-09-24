"""Pre-LLM drop: drop obviously-off-mission clusters BEFORE the LLM and the cap.

Companion to prerelevance.py, which REORDERS only. This module DROPS, and
does so under two guards so it can never silently re-introduce coverage loss:

1. GATED OFF by default. `pipeline.prellm_drop_enabled` is false until the
   CI calibration reports a real score distribution; while disabled this
   stage only LOGS the distribution and records per-cluster scores into
   chosen.csv's `prellm_score` column -- it drops NOTHING.
2. KEYWORD guard. A cluster the deterministic keyword relevance already
   marks on-mission (priority.is_on_mission) is NEVER dropped, whatever its
   embedding score -- embedding similarity is a weaker judge than the LLM's
   verdict, and the lexical signal is the last word.

Zero LLM, zero network, zero torch. Scoring reuses prerelevance.py's pure
functions; the anchors are embedded ONCE per process by the caller through
ctx.embedder and passed in as vectors (no model call happens in this module).

Why drop BEFORE the cap: ~46% of the understand LLM budget is spent
classifying clusters later discarded as `irrelevant` (last-day runs: 66/142
and 70/150). Dropping them first means the cap budget goes to on-mission
stories, not to clusters the LLM would throw away anyway.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Iterable, Sequence

from agent.pipeline.prerelevance import (
    DEFAULT_ANCHOR_TEXTS,
    cosine,
    relevance_score,
)


def anchor_vectors(embedder: Any) -> list[list[float]]:
    """Embed DEFAULT_ANCHOR_TEXTS once via the process embedder. Returns []
    (gate disabled, nothing scored) when there is no usable embedder."""
    if embedder is None or not hasattr(embedder, "embed"):
        return []
    try:
        return [v for v in embedder.embed(list(DEFAULT_ANCHOR_TEXTS)) if v]
    except Exception:  # defensive: scoring must never be able to kill a run
        return []


def drop_off_mission(
    clusters: Sequence[Any],
    anchors: Iterable[Sequence[float]],
    *,
    threshold: float | None,
    on_mission: Callable[[Any], bool] | None = None,
    similarity: Callable[[Sequence[float], Sequence[float]], float] = cosine,
) -> tuple[list[Any], list[Any]]:
    """(kept, dropped). A cluster is dropped only when ALL hold:

      * `threshold` is not None (otherwise this is the identity -- disabled);
      * its max cosine to anchors (`relevance_score`) is not None and
        strictly below `threshold`;
      * `on_mission(cluster)` is falsy (or no predicate was given).

    Unscorable clusters (no centroid, or no usable anchors) are always kept.
    Input order is preserved within both halves.
    """
    if threshold is None:
        return list(clusters), []
    kept: list[Any] = []
    dropped: list[Any] = []
    for cluster in clusters:
        score = relevance_score(cluster, anchors, similarity)
        if score is None:
            kept.append(cluster)
            continue
        if on_mission is not None and on_mission(cluster):
            kept.append(cluster)
            continue
        (dropped if score < threshold else kept).append(cluster)
    return kept, dropped


def score_histogram(scores: Iterable[float | None]) -> dict[str, float] | None:
    """p10/p25/p50/p75/p90/min/max over the scored (non-None) subset. None
    when nothing was scorable -- the caller prints a distinct line then."""
    values = sorted(s for s in scores if s is not None)
    if not values:
        return None

    def _pct(p: float) -> float:
        x = (len(values) - 1) * p
        lo, hi = math.floor(x), math.ceil(x)
        if lo == hi:
            return values[lo]
        return values[lo] + (values[hi] - values[lo]) * (x - lo)

    return {
        "n": float(len(values)),
        "min": values[0],
        "p10": _pct(0.10),
        "p25": _pct(0.25),
        "p50": _pct(0.50),
        "p75": _pct(0.75),
        "p90": _pct(0.90),
        "max": values[-1],
    }


def prellm_pass(
    clusters: Sequence[Any],
    config: Any,
    embedder: Any,
    logger: logging.Logger,
) -> tuple[list[Any], list[Any], dict[str, float | None]]:
    """Run the pre-LLM gate over the freshly-clustered set.

    Returns (kept, dropped, scores_by_key):
      * `kept` is every cluster that survived (== input when disabled);
      * `dropped` is [] whenever disabled, embedder-less, or anchor-less;
      * `scores_by_key` maps cluster.key -> float|None, for chosen.csv.

    The histogram is logged unconditionally -- it IS the calibration probe:
    the next real CI run prints the score distribution that decides the
    threshold, and chosen.csv's `prellm_score` column carries the per-cluster
    values the analyzer reads.
    """
    settings = config.settings.pipeline
    anchors = anchor_vectors(embedder)
    scores = [(c, relevance_score(c, anchors, cosine)) for c in clusters]
    scores_by_key = {c.key: s for c, s in scores}

    hist = score_histogram(s for _, s in scores)
    if hist:
        logger.info(
            "prellm: %d/%d clusters scored; dist min=%.3f p10=%.3f p25=%.3f "
            "p50=%.3f p75=%.3f p90=%.3f max=%.3f",
            int(hist["n"]), len(clusters),
            hist["min"], hist["p10"], hist["p25"],
            hist["p50"], hist["p75"], hist["p90"], hist["max"],
        )
    elif anchors:
        logger.info("prellm: no clusters scorable (empty centroids)")

    threshold = settings.prellm_drop_threshold if settings.prellm_drop_enabled else None
    if threshold is None:
        return list(clusters), [], scores_by_key

    # Local import: priority.py does a TYPE_CHECKING import of cluster.py
    # (which imports THIS module) -- a top-level import here would close the
    # loop. The guard is identical to the cap sort's on_mission by sharing
    # priority.is_on_mission as the single source of truth.
    from agent.pipeline.priority import is_on_mission

    kept, dropped = drop_off_mission(
        clusters, anchors, threshold=threshold,
        on_mission=lambda c: is_on_mission(c, config), similarity=cosine,
    )
    if dropped:
        logger.info(
            "prellm: DROPPED %d clusters below threshold %.3f "
            "(on-mission guard kept the rest); keys: %s",
            len(dropped), threshold, ", ".join(c.key for c in dropped),
        )
    return kept, dropped, scores_by_key
