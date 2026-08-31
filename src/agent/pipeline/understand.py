"""The understand stage: one router.complete() per cluster, JSON out, the
clickbait/irrelevance filter, and the events write.

Contract with the router (Phase 5): no exception reaches this stage. On
`refused_cap` the loop stops -- the budget is exhausted and the run
continues degraded; on `unavailable` the loop stops for the same reason.
Both are logged once, never raised. A parse failure skips the cluster.

Prompts come from config/prompts/understand.txt (the tone contract's home);
this file renders the template and never contains prompt text itself.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Sequence

from agent.collectors.dates import to_tehran
from agent.llm.errors import REFUSED_CAP
from agent.memory.event_models import Event, insert_events
from agent.pipeline.cluster import Cluster
from agent.pipeline.contract import (  # noqa: F401  (re-exported for callers)
    HEADLINE_WORD_BOUNDS,
    MAX_RESPONSE_CHARS,
    SUMMARY_WORD_BOUNDS,
    extract_json as _extract_json,
    within_bounds,
)
from agent.pipeline.langretry import drifts_from_persian, recovery_payload

# Output-contract enforcement lives in pipeline/contract.py (split out
# 2026-08-30 when this file crossed the ~200-line cap; constraint 12).


def _format_item_line(item, body_chars: int) -> str:
    """One article block inside the prompt. date_only items carry the date
    and the honest note that no time was given -- never a midnight
    placeholder rendered as a Tehran clock time (constraints 10 and 11)."""
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
    lines = [_format_item_line(item, body_chars) for item in cluster.members]
    return template.replace("{items}", "\n".join(lines))


class UnderstandStage:
    """Summarises ctx.clusters through ctx.router; writes kept events to
    ctx.events and, when ctx.db is set, to the events table."""

    name = "understand"

    def __init__(self, prompt_template: str, body_chars: int, logger: logging.Logger) -> None:
        self._template = prompt_template
        self._body_chars = body_chars
        self._logger = logger

    def run(self, ctx) -> None:
        clusters = list(getattr(ctx, "clusters", None) or [])
        events: list[Event] = []
        cluster_fates: list[tuple[str, str]] = []
        saw_success = False
        unavailable_total = 0
        # Provider provenance per cluster/event (owner 2026-08-31: the
        # labeled-last-rung debugging trail -- which model answered what).
        ctx.cluster_provider = {}
        ctx.event_provider = {}
        for index, cluster in enumerate(clusters):
            prompt = render_prompt(self._template, cluster, self._body_chars)
            result = ctx.router.complete(prompt, stage="understand")
            ctx.cluster_provider[cluster.key] = result.provider or ""
            if not result.ok:
                if result.status == REFUSED_CAP:
                    # The budget is exhausted: every further call would be
                    # refused too. Stop the loop, log once, run degrades
                    # (PHASE_6_BRIEF gate 6).
                    self._logger.error(
                        "understand: LLM call cap reached after %d cluster(s) -- "
                        "remaining clusters skipped, run continues degraded",
                        len(events),
                    )
                    cluster_fates.extend(
                        (c.key, "cap_refused") for c in clusters[index:]
                    )
                    break
                unavailable_total += 1
                if unavailable_total == 1:
                    # One per-cluster line names the evidence; the rest are
                    # the same sentence with a different hash (2026-08-30:
                    # 27 identical lines in one run's log).
                    self._logger.error(
                        "understand: cluster %s skipped (status=%s)", cluster.key, result.status
                    )
                cluster_fates.append((cluster.key, result.status))
                continue
            saw_success = True  # the AI answered; parse quality is separate

            try:
                parsed = _extract_json(result.text)
            except ValueError:
                self._logger.error(
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
                self._logger.error(
                    "understand: cluster %s out of contract -- skipped (%s)",
                    cluster.key, bounds_reason,
                )
                cluster_fates.append((cluster.key, "oversized"))
                continue

            if parsed.get("clickbait") or parsed.get("irrelevant"):
                self._logger.info(
                    "understand: cluster %s dropped by content filter "
                    "(clickbait=%s irrelevant=%s)",
                    cluster.key, bool(parsed.get("clickbait")),
                    bool(parsed.get("irrelevant")),
                )
                cluster_fates.append(
                    (cluster.key, "clickbait" if parsed.get("clickbait") else "irrelevant")
                )
                continue

            event = self._build_event(cluster, parsed, ctx.now)
            if drifts_from_persian(event.headline, event.summary):
                # Provider answered in the source language despite the
                # prompt. One retry with a forced-Persian instruction; on
                # any failure the original event survives -- memory keeps
                # the fact, compose's gate keeps the message clean.
                replacement, retry_status = recovery_payload(ctx.router, prompt)
                if replacement is not None:
                    event = self._build_event(cluster, replacement, ctx.now)
                    self._logger.info(
                        "understand: cluster %s retried -- Persian recovered",
                        cluster.key,
                    )
                else:
                    self._logger.warning(
                        "understand: cluster %s language drift -- retry failed "
                        "(%s); original kept, compose gate drops if needed",
                        cluster.key, retry_status,
                    )
            ctx.event_provider[cluster.key] = result.provider or ""
            events.append(event)

        if unavailable_total > 1:
            self._logger.error(
                "understand: %d more cluster(s) skipped (status=unavailable)",
                unavailable_total - 1,
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

    @staticmethod
    def _build_event(cluster: Cluster, parsed: dict, now) -> Event:
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
        # time -- a fact, not an invention (events.first_seen_at is NOT
        # NULL; writing NULL here would make INSERT OR IGNORE drop the row).
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
