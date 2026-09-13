"""The understand stage: clusters become events through the LLM router.

Two paths (session 9s):

  settings.llm.batch_size == 1 -- the rollback path: one
  router.complete() per cluster, the single-object JSON contract in
  config/prompts/understand.txt. The loop lives in pipeline/single_run.py,
  moved verbatim so it stays byte-identical (brief requirement 3).

  batch_size > 1: one router call per batch, the array contract in
  config/prompts/understand_batch.txt, per-element failure isolation with
  key-echo mapping. Loop: pipeline/batch_run.py; payload + key mapping:
  pipeline/batch.py; the shared event builder: pipeline/batch.py.

Contract with the router (Phase 5): no exception reaches this stage. On
`refused_cap` the loop stops -- the budget is exhausted and the run
continues degraded; on `unavailable` the loop stops for the same reason.
Both are logged once, never raised. A parse failure skips the cluster.

Prompts come from config/prompts/ (the tone contract's home); this file
renders templates and never contains prompt text itself.
"""

from __future__ import annotations

import logging

from agent.memory.event_models import Event, insert_events
from agent.pipeline.batch import _format_item_line, build_event, render_prompt  # noqa: F401
from agent.pipeline.batch_run import run_batches
from agent.pipeline.cluster import Cluster
from agent.pipeline.contract import (  # noqa: F401  (re-exported for callers)
    HEADLINE_WORD_BOUNDS,
    MAX_RESPONSE_CHARS,
    SUMMARY_WORD_BOUNDS,
    extract_json as _extract_json,
    within_bounds,
)
from agent.pipeline.single_run import run_single

# render_prompt/_format_item_line moved to pipeline/batch.py 2026-09-09
# (single + batched prompt builders share one copy); _build_event moved to
# the same file. All three stay importable from here.


class UnderstandStage:
    """Summarises ctx.clusters through ctx.router; writes kept events to
    ctx.events and, when ctx.db is set, to the events table."""

    name = "understand"

    def __init__(
        self,
        prompt_template: str,
        body_chars: int,
        logger: logging.Logger,
        batch_template: str | None = None,
        batch_size: int = 1,
    ) -> None:
        self._template = prompt_template
        self._batch_template = batch_template
        self._batch_size = batch_size
        self._body_chars = body_chars
        self._logger = logger

    def run(self, ctx) -> None:
        clusters = list(getattr(ctx, "clusters", None) or [])
        if self._batch_template is not None and self._batch_size > 1:
            (
                events, cluster_fates, saw_success, unavailable_total,
                skipped_statuses, cluster_provider, event_provider,
            ) = run_batches(
                ctx, clusters,
                batch_size=self._batch_size,
                batch_template=self._batch_template,
                single_template=self._template,
                body_chars=self._body_chars,
                logger=self._logger,
            )
            ctx.cluster_provider = cluster_provider
            ctx.event_provider = event_provider
        else:
            events, cluster_fates, saw_success, unavailable_total, skipped_statuses = (
                run_single(
                    ctx, clusters,
                    template=self._template,
                    body_chars=self._body_chars,
                    logger=self._logger,
                )
            )
        self._finish(ctx, clusters, events, cluster_fates, saw_success,
                     unavailable_total, skipped_statuses)

    # -- shared tail ------------------------------------------------------

    def _finish(self, ctx, clusters, events, cluster_fates, saw_success,
                unavailable_total, skipped_statuses) -> None:
        if unavailable_total > 1:
            # Report the ACTUAL statuses. The old line hardcoded
            # "unavailable", so the 2026-09-05 run's three provider-fatal
            # losses read as generic outage and the real cause (a
            # bai_deepseek 400) stayed invisible until the CSV was diffed.
            breakdown = ", ".join(
                f"{status}={count}"
                for status, count in sorted(skipped_statuses.items())
            )
            self._logger.error(
                "understand: %d more cluster(s) skipped without an LLM answer (%s)",
                unavailable_total - 1, breakdown,
            )
        ctx.events = events
        ctx.cluster_fates = cluster_fates
        # Honesty flag for the composer: clusters existed but NO LLM call
        # succeeded. "Nothing new" would be a lie about the world -- the
        # truth is "the AI was unavailable" (ARCHITECTURE.md §8).
        ctx.llm_failed = bool(clusters) and not saw_success
        if events:
            self._logger.info("understand: %d events from %d clusters", len(events), len(clusters))
        if getattr(ctx, "db", None) is not None and events:
            insert_events(ctx.db, events)

    _build_event = staticmethod(build_event)  # moved to pipeline/batch.py
