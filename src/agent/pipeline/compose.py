"""The compose stage: ranked events become one or more budgeted Telegram
messages, or an honest one-liner.

Owner's output contract (2026-08-29): Persian, informative headlines (the
reader decides from the headline whether to read on), concise details,
category symbols, importance-sorted, multi-message when the day is busy,
and never "news anchor" dramatisation. Tone lives in config/prompts; this
stage owns ORDER, LABELS and BUDGET. Order comes from pipeline/rank.py
(deterministic -- NOT the Phase-11 risk engine). Dates are Jalali, Tehran
wall-clock, display-only; date_only items say the time was not stated.
"""

from __future__ import annotations

import logging

from agent.collectors.tz import to_tehran
from agent.delivery.formatter import format_split
from agent.delivery.message import Item, Message
from agent.pipeline.labels import category_icon, category_name, labels_for
from agent.pipeline.langgate import split_persian
from agent.pipeline.rank import rank_events
from agent.util.jalali import format_jalali


def _headline(summary: str) -> str:
    """First sentence of the summary, capped -- informative, not truncated
    into nonsense: the cap cuts at a word boundary near 140 chars."""
    first = summary.split(". ")[0].strip(" .")
    if len(first) <= 140:
        return first
    cut = first[:137].rsplit(" ", 1)[0]
    return cut + "…"


def _when_text(cluster, labels) -> str:
    """Jalali display of the event's latest time. If EVERY member is
    date_only, the feed gave no time -- say so rather than invent one."""
    dated = [m for m in cluster.members if m.published_at is not None]
    if not dated:
        return labels["date_unknown"]
    latest = max(m.published_at for m in dated)
    shown = to_tehran(latest)
    when = format_jalali(shown, with_time=True)
    if all(m.date_only for m in dated):
        return f"{when.split(' — ')[0]} ({labels['time_not_stated']})"
    return when


class ComposeStage:
    """Writes ctx.messages (formatted, budgeted, ranked) and the optional
    lead message; `compose` counter = events included."""

    name = "compose"

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def run(self, ctx) -> None:
        settings = ctx.config.settings
        labels = labels_for(settings.delivery.output_language)
        events = list(getattr(ctx, "events", None) or [])
        clusters = {c.key: c for c in getattr(ctx, "clusters", None) or []}
        ctx.compose_kept_keys = []
        # Built FIRST: a lead-only run (main events empty -- nothing
        # corroborated) must still deliver leads, which is exactly the
        # scenario the leads channel exists for (fix 2026-08-30).
        self._build_lead_message(ctx, settings)

        lang_dropped: list = []
        if events:
            events, lang_dropped = split_persian(events)
            if lang_dropped:
                ctx.counters["compose_lang_drops"] = len(lang_dropped)
                self._logger.warning(
                    "compose: %d event(s) dropped -- output not Persian: %s",
                    len(lang_dropped),
                    ", ".join(e.event_key[:8] for e in lang_dropped),
                )
        ctx.lang_dropped = lang_dropped

        if not events:
            if lang_dropped:
                # Events existed but none rendered Persian: say so, never
                # "nothing new" -- that would be a lie about the world
                # (constraint 11).
                text = labels["lang_dropped"]
            elif getattr(ctx, "llm_failed", False):
                text = labels["ai_unavailable"]
            else:
                text = labels["nothing_new"]
            ctx.messages = [text]
            ctx.counters["compose"] = 0
            self._logger.info("compose: no events -- honest one-liner")
            return

        kept, dropped, gated = rank_events(
            events, clusters, ctx.config.credibility, settings, ctx.now,
            self._logger, ctx.config.relevance,
        )
        ctx.rank_dropped = dropped
        ctx.relevance_dropped = gated

        if not kept:
            # Everything fell below the importance threshold: the honest
            # one-liner, never a bare header with nothing under it.
            text = (
                labels["ai_unavailable"] if getattr(ctx, "llm_failed", False)
                else labels["nothing_new"]
            )
            ctx.messages = [text]
            ctx.counters["compose"] = 0
            self._logger.info(
                "compose: all %d event(s) below min_score -- honest one-liner", len(events)
            )
            return

        now_tehran = to_tehran(ctx.now)
        marker = (
            f" — {labels['digest_marker']}"
            if getattr(ctx, "daily_digest", False) else ""
        )
        header = (
            f"{labels['header']} — {format_jalali(now_tehran, with_time=True)}"
            f" {labels['tehran']}{marker}"
        )

        items = []
        for index, event in enumerate(kept):
            cluster = clusters.get(event.event_key)
            when = _when_text(cluster, labels) if cluster is not None else ""
            name = category_name(settings.delivery.output_language, event.category)
            # The LLM's own informative headline is the title; the summary
            # is the detail BEYOND it. Fallback (no headline field): the
            # summary's first sentence, as before.
            headline = event.headline.strip() if event.headline else _headline(event.summary)
            if event.claim_status == "rumour":
                headline = f"{labels['rumour']} · {headline}"
            headline = f"{category_icon(event.category)} {headline}"
            detail_bits = [name, when, event.summary] if when else [name, event.summary]
            items.append(Item(
                headline=headline,
                priority=index,
                detail=" · ".join(detail_bits),
            ))

        message = Message(header=header, items=tuple(items), footer=None)
        max_units = settings.delivery.telegram_max_chars
        # format_split budgets and splits by priority; the digest may span
        # up to digest_rank.max_messages messages (owner decision).
        ctx.messages = format_split(
            message, max_units=max_units, max_messages=settings.digest_rank.max_messages
        )
        ctx.counters["compose"] = len(kept)
        # Received-marker keys for the anti-repetition window, recorded
        # after the rank cut (below-threshold events were never seen and
        # must not suppress their own follow-ups). The deliver stage writes
        # the markers itself, only after real sends succeeded -- marking
        # here would re-create the ghost suppression on send failure
        # (review finding 2026-08-30). Known edge: on the busiest days
        # format_split may truncate the lowest-priority items -- those are
        # then over-marked for at most the 72h repeat window. Accepted
        # (rare, low-priority, self-correcting).
        ctx.compose_kept_keys = [e.event_key for e in kept]
        self._logger.info(
            "compose: %d event(s) -> %d message(s)", len(kept), len(ctx.messages)
        )

    def _build_lead_message(self, ctx, settings) -> None:
        """Leads channel message. Lead events are gated by the Persian
        output gate like main events, but are NEVER marked delivered:
        their corroborated confirmation must reach the main feed
        (schema.sql note)."""
        labels = labels_for(settings.delivery.output_language)
        lead_events = list(getattr(ctx, "lead_events", None) or [])
        if lead_events:
            lead_events, lead_lang_dropped = split_persian(lead_events)
            if lead_lang_dropped:
                self._logger.warning(
                    "compose: %d lead event(s) dropped -- output not Persian: %s",
                    len(lead_lang_dropped),
                    ", ".join(e.event_key[:8] for e in lead_lang_dropped),
                )
        if not (lead_events and getattr(ctx, "leads_channel_id", None)):
            return
        lead_items = []
        for index, event in enumerate(lead_events):
            lead_items.append(Item(
                headline=f"📡 {labels['lead_prefix']} · {_headline(event.summary)}",
                priority=index,
                detail=event.summary,
            ))
        lead_message = Message(
            header=labels["lead_header"], items=tuple(lead_items), footer=None
        )
        ctx.lead_message = format_split(
            lead_message,
            max_units=settings.delivery.telegram_max_chars,
            max_messages=1,
        )[0]
