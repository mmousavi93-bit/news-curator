"""The single-cluster understand LOOP -- the pre-session-9s path, moved
verbatim out of understand.py 2026-09-09.

Byte-identical on purpose: settings.llm.batch_size=1 must reproduce the
old one-call-per-cluster behaviour EXACTLY (brief requirement 3 -- the
rollback path), so this loop is the tested battle code, log lines
included, not a re-implementation. understand.py dispatches to it
whenever batching is off.
"""

from __future__ import annotations

import logging
from typing import Sequence

from agent.llm.errors import REFUSED_CAP
from agent.memory.event_models import Event
from agent.pipeline.batch import build_event, render_prompt
from agent.pipeline.cluster import Cluster
from agent.pipeline.contract import extract_json, within_bounds
from agent.pipeline.langretry import drifts_from_persian, recovery_payload


def run_single(
    ctx,
    clusters: Sequence[Cluster],
    *,
    template: str,
    body_chars: int,
    logger: logging.Logger,
) -> tuple[list[Event], list[tuple[str, str]], bool, int, dict[str, int]]:
    """The 9r-era loop, unchanged. Returns (events, cluster_fates,
    saw_success, unavailable_total, skipped_statuses) -- exactly the
    state understand.run() merges into ctx. Provider provenance is set
    on ctx directly, as before."""
    events: list[Event] = []
    cluster_fates: list[tuple[str, str]] = []
    saw_success = False
    unavailable_total = 0
    skipped_statuses: dict[str, int] = {}
    # Provider provenance per cluster/event (owner 2026-08-31: the
    # labeled-last-rung debugging trail -- which model answered what).
    ctx.cluster_provider = {}
    ctx.event_provider = {}
    for index, cluster in enumerate(clusters):
        prompt = render_prompt(template, cluster, body_chars)
        result = ctx.router.complete(prompt, stage="understand")
        ctx.cluster_provider[cluster.key] = result.provider or ""
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
                cluster_fates.extend(
                    (c.key, "cap_refused") for c in clusters[index:]
                )
                break
            unavailable_total += 1
            skipped_statuses[result.status] = (
                skipped_statuses.get(result.status, 0) + 1
            )
            if unavailable_total == 1:
                # One per-cluster line names the evidence; the rest are
                # the same sentence with a different hash (2026-08-30:
                # 27 identical lines in one run's log).
                logger.error(
                    "understand: cluster %s skipped (status=%s)", cluster.key, result.status
                )
            cluster_fates.append((cluster.key, result.status))
            continue
        saw_success = True  # the AI answered; parse quality is separate

        try:
            parsed = extract_json(result.text)
        except ValueError:
            logger.error(
                "understand: cluster %s response unparseable -- skipped", cluster.key
            )
            cluster_fates.append((cluster.key, "unparseable"))
            continue

        ok_bounds, bounds_reason = within_bounds(
            parsed, len(result.text or ""))
        if not ok_bounds:
            # A parseable JSON that violates the output contract -- the
            # ramble class (2026-08-30: bai's 6.5K-token summary). The
            # event never renders; the cluster is skipped, not crashed.
            logger.error(
                "understand: cluster %s out of contract -- skipped (%s)",
                cluster.key, bounds_reason,
            )
            cluster_fates.append((cluster.key, "oversized"))
            continue

        if parsed.get("clickbait") or parsed.get("irrelevant"):
            logger.info(
                "understand: cluster %s dropped by content filter "
                "(clickbait=%s irrelevant=%s)",
                cluster.key, bool(parsed.get("clickbait")),
                bool(parsed.get("irrelevant")),
            )
            cluster_fates.append(
                (cluster.key, "clickbait" if parsed.get("clickbait") else "irrelevant")
            )
            continue

        event = build_event(cluster, parsed, ctx.now)
        if drifts_from_persian(event.headline, event.summary):
            # Provider answered in the source language despite the
            # prompt. One retry with a forced-Persian instruction; on
            # any failure the original event survives -- memory keeps
            # the fact, compose's gate keeps the message clean.
            replacement, retry_status = recovery_payload(ctx.router, prompt)
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
        ctx.event_provider[cluster.key] = result.provider or ""
        events.append(event)
    return events, cluster_fates, saw_success, unavailable_total, skipped_statuses
