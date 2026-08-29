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

_FENCE_RE_OPEN = "```"


def _extract_json(text: str) -> dict:
    """The model may wrap JSON in markdown fences. Strip them, then parse.
    Raises ValueError on anything unparseable -- the caller skips the
    cluster, because feeding a half-parse downstream invents content."""
    stripped = text.strip()
    if stripped.startswith(_FENCE_RE_OPEN):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1:] if first_newline != -1 else stripped[3:]
    if stripped.endswith(_FENCE_RE_OPEN):
        stripped = stripped[: stripped.rfind(_FENCE_RE_OPEN)].strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("response is not a JSON object")
    return parsed


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
        saw_success = False
        for cluster in clusters:
            result = ctx.router.complete(
                render_prompt(self._template, cluster, self._body_chars),
                stage="understand",
            )
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
                    break
                self._logger.error(
                    "understand: cluster %s skipped (status=%s)", cluster.key, result.status
                )
                continue
            saw_success = True  # the AI answered; parse quality is separate

            try:
                parsed = _extract_json(result.text)
            except ValueError:
                self._logger.error(
                    "understand: cluster %s response unparseable -- skipped", cluster.key
                )
                continue

            if parsed.get("clickbait") or parsed.get("irrelevant"):
                self._logger.info(
                    "understand: cluster %s dropped by content filter "
                    "(clickbait=%s irrelevant=%s)",
                    cluster.key, bool(parsed.get("clickbait")),
                    bool(parsed.get("irrelevant")),
                )
                continue

            events.append(self._build_event(cluster, parsed, ctx.now))

        ctx.events = events
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
