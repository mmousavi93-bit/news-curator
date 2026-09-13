"""The batched understand LOOP (session 9s): one router call per batch of
clusters, per-element failure isolation.

Split from batch.py (pure mechanics) to keep both under the ~200-line cap
(constraint 12). Partial-failure contract (brief requirement 1, THE risk
of this change): one malformed object in a 5-cluster response must cost
ONE summary, not five -- a cluster whose element is missing, malformed,
or filtered is fated individually, its batch-mates ship. The whole-batch
cases are three: the LLM call itself failed (no response exists), the
response is not parseable as a JSON array (which includes a single-object
answer against the array contract -- no safe per-cluster mapping exists),
and the response failed the batch-level raw-length cap (contract.py's
MAX_RESPONSE_CHARS scaled by batch size -- the ramble guard the single
path has had since 2026-08-30).
"""

from __future__ import annotations

import logging
from typing import Sequence

from agent.llm.errors import REFUSED_CAP, LlmResult
from agent.memory.event_models import Event
from agent.pipeline.batch import (
    build_event,
    build_payload,
    chunk,
    map_results,
    render_prompt,
)
from agent.pipeline.cluster import Cluster
from agent.pipeline.contract import (
    MAX_RESPONSE_CHARS,
    extract_json_array,
    within_bounds,
)
from agent.pipeline.langretry import drifts_from_persian, recovery_payload


def process_element(
    ctx,
    cluster: Cluster,
    parsed: dict,
    logger: logging.Logger,
    single_template: str,
    body_chars: int,
) -> tuple[Event | None, str | None]:
    """One parsed batch element -> (event, None) or (None, fate). The same
    output contract, content filter and language-drift retry the single
    path applies -- see understand.py for the field-level rationale. The
    language retry sends the SINGLE-cluster prompt (understand.txt's
    contract), so a drifting batch-mate is recovered with exactly today's
    retry, one extra call, batch-mates untouched."""
    ok_bounds, bounds_reason = within_bounds(parsed, 0)
    if not ok_bounds:
        logger.error(
            "understand: cluster %s out of contract -- skipped (%s)",
            cluster.key, bounds_reason,
        )
        return None, "oversized"
    if parsed.get("clickbait") or parsed.get("irrelevant"):
        logger.info(
            "understand: cluster %s dropped by content filter "
            "(clickbait=%s irrelevant=%s)",
            cluster.key, bool(parsed.get("clickbait")),
            bool(parsed.get("irrelevant")),
        )
        return None, "clickbait" if parsed.get("clickbait") else "irrelevant"
    event = build_event(cluster, parsed, ctx.now)
    if drifts_from_persian(event.headline, event.summary):
        replacement, retry_status = recovery_payload(
            ctx.router, render_prompt(single_template, cluster, body_chars)
        )
        if replacement is not None:
            event = build_event(cluster, replacement, ctx.now)
            logger.info(
                "understand: cluster %s retried -- Persian recovered",
                cluster.key,
            )
        else:
            logger.warning(
                "understand: cluster %s language drift -- retry failed "
                "(%s); original kept, compose gate drops if needed",
                cluster.key, retry_status,
            )
    return event, None


def run_batches(
    ctx,
    clusters: Sequence[Cluster],
    *,
    batch_size: int,
    batch_template: str,
    single_template: str,
    body_chars: int,
    logger: logging.Logger,
) -> tuple[
    list[Event], list[tuple[str, str]], bool, int, dict[str, int], dict[str, str], dict[str, str]
]:
    """The batched loop. Returns (events, fates, saw_success,
    unavailable_total, skipped_statuses, cluster_provider, event_provider)
    -- exactly the state understand.run() merges into ctx, identical shape
    to what the single-cluster loop produced before batching existed."""
    events: list[Event] = []
    cluster_fates: list[tuple[str, str]] = []
    saw_success = False
    unavailable_total = 0
    skipped_statuses: dict[str, int] = {}
    cluster_provider: dict[str, str] = {}
    event_provider: dict[str, str] = {}
    batches = chunk(list(clusters), batch_size)
    for batch_index, batch in enumerate(batches):
        prompt = build_payload(batch, batch_template, body_chars)
        result: LlmResult = ctx.router.complete(prompt, stage="understand")
        for cluster in batch:
            cluster_provider[cluster.key] = result.provider or ""
        if not result.ok:
            if result.status == REFUSED_CAP:
                # The budget is exhausted: every further call would be
                # refused too. Stop the loop, log once, run degrades
                # (PHASE_6_BRIEF gate 6).
                logger.error(
                    "understand: LLM call cap reached after %d cluster(s) -- "
                    "remaining clusters skipped, run continues degraded",
                    len(events),
                )
                cluster_fates.extend((c.key, "cap_refused") for c in batch)
                for later_batch in batches[batch_index + 1:]:
                    cluster_fates.extend((c.key, "cap_refused") for c in later_batch)
                break
            unavailable_total += len(batch)
            skipped_statuses[result.status] = (
                skipped_statuses.get(result.status, 0) + len(batch)
            )
            if unavailable_total == len(batch):
                # The first failing batch names the evidence; the rest are
                # the same sentence with a different hash (2026-08-30: 27
                # identical lines in one run's log).
                logger.error(
                    "understand: batch of %d (first cluster %s) skipped "
                    "(status=%s)",
                    len(batch), batch[0].key[:8], result.status,
                )
            cluster_fates.extend((c.key, result.status) for c in batch)
            continue
        saw_success = True  # the AI answered; parse quality is separate

        try:
            parsed = extract_json_array(result.text)
        except ValueError:
            # Includes the single-object-against-the-array-contract case:
            # no safe per-cluster mapping exists -- guessing "the first
            # cluster" invents content (constraint 11).
            logger.error(
                "understand: batch of %d (first cluster %s) response "
                "unparseable -- skipped",
                len(batch), batch[0].key[:8],
            )
            cluster_fates.extend((c.key, "unparseable") for c in batch)
            continue
        if len(result.text or "") > MAX_RESPONSE_CHARS * len(batch):
            # contract.py's raw-length cap scaled by batch size: a ramble
            # hides in ANY field of ANY element (2026-08-30 class).
            logger.error(
                "understand: batch of %d (first cluster %s) response too "
                "long -- skipped",
                len(batch), batch[0].key[:8],
            )
            cluster_fates.extend((c.key, "oversized") for c in batch)
            continue

        mapped = map_results(batch, parsed, logger)
        for cluster in batch:
            element = mapped[cluster.key]
            if element is None:
                # The model omitted this cluster from its array. No content
                # arrived for it; batch-mates ship (brief requirement 1).
                cluster_fates.append((cluster.key, "unavailable"))
                continue
            event, fate = process_element(
                ctx, cluster, element, logger, single_template, body_chars,
            )
            if fate is not None:
                cluster_fates.append((cluster.key, fate))
            else:
                event_provider[cluster.key] = result.provider or ""
                events.append(event)
    return (
        events, cluster_fates, saw_success, unavailable_total,
        skipped_statuses, cluster_provider, event_provider,
    )
