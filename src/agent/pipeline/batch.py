"""Pure batching mechanics for the understand stage (session 9s): chunk
clusters, build one batch payload, map a batched response back to clusters
by ECHOED KEY, never by position.

Why this file exists: one LLM call per cluster pays the ~2,300-token prompt
template 40 times per run. Batching pays it ~8 times (batch of 5) and cuts
~138K tokens/run to ~46K -- the fix for Groq's 8,000 TPM wall (the binder
measured 2026-09-08: 35 of 60 groq calls 429'd while the RpmPacer was
pacing requests, not tokens). See agents/briefs/SESSION_9S_BRIEF.md.

CONSTRAINT 11 IS THE RISK (the brief's "most likely constraint violation"):
the convenient mapping zips request order against response order. The
moment a model omits or reorders one element, cluster B receives cluster
A's summary and the digest publishes a confident, well-formed, completely
fabricated event. Every response element must echo the short per-batch id
(`c1`..`cN`) its request carried; ids the request never sent are dropped
and logged; a cluster the model omitted maps to None and is fated
`unavailable`. Position is never trusted.

The BATCH prompt lives in config/prompts/understand_batch.txt; the single
prompt stays config/prompts/understand.txt untouched -- two files on
purpose: settings.llm.batch_size=1 must reproduce the old
one-call-per-cluster behaviour EXACTLY (the rollback path), and that
means the old single-object contract survives byte-identical.
"""

from __future__ import annotations

import logging
from typing import Sequence

from agent.collectors.dates import to_tehran
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster

# Per-batch ids, e.g. "c1".."c5". Short on purpose: 64-char cluster keys
# would cost ~16 tokens each twice (prompt + echo) for zero extra safety --
# the id only ever maps back to THIS batch's list, fixed before the call.
_KEY_PREFIX = "c"


def _key_for(index: int) -> str:
    return f"{_KEY_PREFIX}{index + 1}"


def chunk(clusters: Sequence[Cluster], size: int) -> list[list[Cluster]]:
    """Sequential slices of at most `size` clusters. A size of 1 is NOT
    routed through here -- understand.py keeps the pre-batching path for it
    (see module docstring)."""
    return [list(clusters[i:i + size]) for i in range(0, len(clusters), size)]


def _format_item_line(item, body_chars: int) -> str:
    """One article block inside the prompt. date_only items carry the date
    and the honest note that no time was given -- never a midnight
    placeholder rendered as a Tehran clock time (constraints 10 and 11).
    Moved here from understand.py 2026-09-09 so both prompt builders share
    one copy; understand.py re-imports it."""
    published = item.published_at
    if published is None:
        when = "date unknown"
    elif item.date_only:
        when = f"{to_tehran(published):%Y-%m-%d} (date only, time not stated)"
    else:
        when = f"{to_tehran(published):%Y-%m-%d %H:%M}"
    body = item.body if len(item.body) <= body_chars else item.body[:body_chars] + "..."
    return f"- [{item.source_id} | {when}] {item.title}\n  {body}"


def render_prompt(template: str, cluster: Cluster, body_chars: int) -> str:
    """The SINGLE-cluster prompt (batch_size=1 path and the language-drift
    retry). Re-exported from understand.py so existing callers and tests
    keep importing it there."""
    lines = [_format_item_line(item, body_chars) for item in cluster.members]
    return template.replace("{items}", "\n".join(lines))


def build_payload(batch: Sequence[Cluster], template: str, body_chars: int) -> str:
    """One prompt carrying `batch` clusters, each under a `## cluster cN`
    heading whose id the response must echo (map_results enforces it)."""
    blocks: list[str] = []
    for index, cluster in enumerate(batch):
        lines = [_format_item_line(item, body_chars) for item in cluster.members]
        blocks.append(f"## cluster {_key_for(index)}\n" + "\n".join(lines))
    return template.replace("{items}", "\n\n".join(blocks))


def build_event(cluster: Cluster, parsed: dict, now) -> Event:
    """Cluster + parsed payload -> Event. Moved here from
    UnderstandStage._build_event 2026-09-09 so the single path, the
    batched path and the language retry share one copy (understand.py
    keeps the staticmethod as an alias)."""
    entities_raw = parsed.get("entities") or []
    entities = tuple(str(e) for e in entities_raw if isinstance(e, str))
    published = [m.published_at for m in cluster.members if m.published_at is not None]
    summary = str(parsed.get("summary") or parsed.get("headline") or "")
    headline = str(parsed.get("headline") or "").strip()
    # Digest-ranking category, validated to the known set; anything the
    # model invents falls back to "other" (weight 0 in the ranker).
    category = str(parsed.get("category") or "other")
    if category not in ("military", "security", "politics", "economy", "other"):
        category = "other"
    # When no member carries a date, the run's now is the observation
    # time -- a fact, not an invention (events.first_seen_at is NOT NULL;
    # writing NULL here would make INSERT OR IGNORE drop the row).
    observed = min(published) if published else now
    return Event(
        event_key=cluster.key,
        summary=summary,
        headline=headline,
        entities=entities,
        category=category,
        source_count=len(cluster.members),
        first_seen_at=observed,
        last_updated_at=max(published) if published else observed,
    )


def map_results(
    batch: Sequence[Cluster], parsed: object, logger: logging.Logger
) -> dict[str, dict | None]:
    """Map a parsed batch response (a JSON list) back to clusters BY KEY.

    Returns one entry PER CLUSTER IN THE BATCH, keyed by cluster.key:
    the element whose echoed id matched, or None when the model omitted
    that cluster. Never keyed by position -- see the module docstring.
    Elements are `dict`s; anything else is dropped as malformed (the
    caller fates that cluster `unparseable`). An echoed id the request did
    not send is dropped and logged, never trusted; a duplicated id keeps
    the first occurrence.
    """
    key_to_cluster = {_key_for(i): cluster for i, cluster in enumerate(batch)}
    result: dict[str, dict | None] = {cluster.key: None for cluster in batch}
    for index, element in enumerate(parsed if isinstance(parsed, list) else []):
        if not isinstance(element, dict):
            logger.error(
                "understand: batch element %d is not an object -- discarded", index
            )
            continue
        echoed = element.get("key")
        cluster = key_to_cluster.get(str(echoed)) if isinstance(echoed, str) else None
        if cluster is None:
            # A key we never sent: the model invented it (or echoed wrongly).
            # Trusting it could attach one cluster's content to another's
            # key -- the fabrication path. Drop it, log it, never map it.
            logger.error(
                "understand: batch element %d echoed unknown key %r -- discarded",
                index, echoed,
            )
            continue
        if result[cluster.key] is not None:
            logger.error(
                "understand: batch element %d echoes key %r twice -- first kept",
                index, echoed,
            )
            continue
        result[cluster.key] = element
    return result
