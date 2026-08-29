"""The topic gate (session-5 decision 1).

Sources marked `topic_gate: true` in sources.yaml are general-interest feeds
that would flood the funnel (aawsat 300, dw 137, reuters_gnews 100,
ap_gnews 100, state_dept_travel 87, france24 29 items/sweep -- 37% of the
corpus). This stage reduces them REVERSIBLY: an item survives only if a
keyword from config/topics.yaml matches its title or body. Non-gated
sources pass through untouched, always.

Fail-open on unknown language: an item whose language has no keyword list
passes. A gate that silently drops possibly-relevant items is worse than
no gate -- off-mission items are the understand stage's `irrelevant`
filter's job, and that one says why.
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from agent.collectors.base import Item
from agent.config import ConfigError


def validate_topics(raw: object) -> dict[str, tuple[str, ...]]:
    """Shape-check config/topics.yaml: {'topics': {lang: [kw, ...]}}.
    A typo'd shape fails the run loudly rather than silently gating
    nothing (or everything) -- same fail-fast rule as every config file."""
    if not isinstance(raw, dict) or not isinstance(raw.get("topics"), dict):
        raise ConfigError(
            "topics.yaml: expected a mapping with a 'topics' mapping, "
            f"got {type(raw).__name__}"
        )
    result: dict[str, tuple[str, ...]] = {}
    for lang, keywords in raw["topics"].items():
        if not isinstance(lang, str):
            raise ConfigError(f"topics.yaml: language key {lang!r} is not a string")
        if not isinstance(keywords, (list, tuple)):
            raise ConfigError(
                f"topics.yaml: topics.{lang} must be a list of strings, "
                f"got {type(keywords).__name__}"
            )
        for kw in keywords:
            if not isinstance(kw, str) or not kw.strip():
                raise ConfigError(f"topics.yaml: topics.{lang} contains a non-string keyword")
        result[lang] = tuple(kw.strip().lower() for kw in keywords)
    return result


class TopicGateStage:
    """Filters ctx.items in place. Counter keys: filter.kept / filter.dropped."""

    name = "filter"

    def __init__(
        self,
        topics: Mapping[str, Sequence[str]],
        gated_source_ids: set[str],
        logger: logging.Logger,
    ) -> None:
        self._topics = topics
        self._gated = gated_source_ids
        self._logger = logger

    def _matches(self, item: Item) -> bool:
        keywords = self._topics.get(item.lang)
        if not keywords:
            return True  # fail-open: no keyword list for this language
        haystack = f"{item.title}\n{item.body}".lower()
        return any(kw in haystack for kw in keywords)

    def run(self, ctx) -> None:
        items = list(getattr(ctx, "items", None) or [])
        if not items:
            ctx.counters.setdefault("filter.kept", 0)
            ctx.counters.setdefault("filter.dropped", 0)
            return
        kept = []
        dropped = 0
        for item in items:
            if item.source_id not in self._gated or self._matches(item):
                kept.append(item)
            else:
                dropped += 1
        ctx.items = kept
        ctx.counters["filter.kept"] = len(kept)
        ctx.counters["filter.dropped"] = dropped
        if dropped:
            self._logger.info(
                "filter: topic gate dropped %d of %d items (%d gated sources)",
                dropped, len(items), len(self._gated),
            )
