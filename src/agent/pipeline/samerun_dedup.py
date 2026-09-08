"""Same-run duplicate collapse: split out of repeats.py 2026-09-06 (round-2
review) when that file crossed the ~200-line cap (constraint 12).

Two events from the SAME run telling the same story must not both reach the
digest (2026-08-30 Hormuz-tanker double-send -- pipeline/repeats.py's
drop_repeats only compares against PREVIOUS runs' delivered summaries, so it
never catches a same-run collision). Pairwise cosine over the LLM-written
summaries, zero LLM calls; the survivor is chosen on (independent_count,
member count), NEVER member count alone (round-2 review, fix 2): three
reposting tier-3/lead channels must not outnumber and delete a single
corroborated tier-1/2 report of the same story.
"""

from __future__ import annotations

import logging
from typing import Mapping

from agent.memory.event_models import Event
from agent.pipeline.repeats import _cosine


def _rank_key(event: Event, clusters_by_key: Mapping[str, object]) -> tuple[int, int]:
    """(independent_count, member count) -- corroboration ranked ahead of
    raw size (round-2 review, fix 2): a pile of tier-3/lead reposts must
    never outrank a single-member tier-1/2 cluster just by outnumbering
    it."""
    cluster = clusters_by_key.get(event.event_key)
    size = len(cluster.members) if cluster is not None else 0
    return (event.independent_count, size)


def drop_same_run_dups(
    ctx, events: list[Event], logger: logging.Logger
) -> tuple[list[Event], list[Event], dict[str, str]]:
    """Survivor is the one with the higher (independent_count, member
    count), ties keep the first. Returns (kept, dropped, reasons) --
    reasons is machine-greppable (`same_run_dup sim=<f>`) for report_csv.py.
    """
    if getattr(ctx, "embedder", None) is None or len(events) < 2:
        return events, [], {}
    threshold = ctx.config.settings.pipeline.event_match_threshold
    vectors = ctx.embedder.embed([e.summary for e in events])
    clusters_by_key = {c.key: c for c in getattr(ctx, "clusters", None) or []}

    dropped_keys: set[str] = set()
    reasons: dict[str, str] = {}
    for i, event in enumerate(events):
        if event.event_key in dropped_keys:
            continue
        for j in range(i + 1, len(events)):
            other = events[j]
            if other.event_key in dropped_keys:
                continue
            similarity = _cosine(vectors[i], vectors[j])
            if similarity < threshold:
                continue
            loser = (
                event
                if _rank_key(event, clusters_by_key) < _rank_key(other, clusters_by_key)
                else other
            )
            dropped_keys.add(loser.event_key)
            reasons[loser.event_key] = f"same_run_dup sim={similarity:.2f}"
            logger.info(
                "validate: %s dropped as same-run duplicate of a more "
                "corroborated/larger cluster (sim %.2f)",
                loser.event_key[:8], similarity,
            )
    return (
        [e for e in events if e.event_key not in dropped_keys],
        [e for e in events if e.event_key in dropped_keys],
        reasons,
    )
