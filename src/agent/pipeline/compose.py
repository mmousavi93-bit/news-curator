"""The compose stage: ranked events become one or more budgeted Telegram
messages, or an honest one-liner.

Owner's output contract (2026-08-29): Persian, informative headlines (the
reader decides from the headline whether to read on), concise details,
category symbols, importance-sorted, multi-message when the day is busy,
and never "news anchor" dramatisation. Tone lives in config/prompts; this
stage owns ORDER, LABELS and BUDGET. Order comes from pipeline/rank.py
(deterministic -- NOT the Phase-11 risk engine). Dates are Jalali, Tehran
wall-clock, display-only; date_only items say the time was not stated.

The pure text helpers (_headline, _raw_fallback, _when_text, build_digest_
items) live in pipeline/render.py; the lead channel message builder
(build_lead_message) lives in pipeline/render_leads.py -- split out
2026-09-05 / 2026-09-06 / 2026-09-08 to keep these files under the
~200-line cap (constraint 12).
"""

from __future__ import annotations

import logging

from agent.collectors.tz import to_tehran
from agent.delivery.formatter import escape_html, format_split_tracked
from agent.delivery.message import Message
from agent.pipeline.flash_watchdog import flash_warning
from agent.pipeline.labels import labels_for
from agent.pipeline.langgate import split_persian
from agent.pipeline.rank import rank_events
from agent.pipeline.render import _raw_fallback, build_digest_items
from agent.pipeline.render_leads import build_lead_message
from agent.util.jalali import format_jalali


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
        # Flash-monitor liveness (9r). Computed BEFORE any budgeting so the
        # line is inside the character budget, never appended after the split
        # (constraint 8: never discover the 4,096 cap at send time). It rides
        # the honest one-liner too -- a "nothing new" run is exactly when a
        # dead alerter is most dangerous and least visible.
        warning = flash_warning(settings, labels)
        # Built FIRST: a lead-only run (main events empty -- nothing
        # corroborated) must still deliver leads, which is exactly the
        # scenario the leads channel exists for (fix 2026-08-30).
        build_lead_message(ctx, settings, self._logger)

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

        # Raw fallback section (move 1, 2026-08-31): clusters the LLM
        # could not cover, as escaped raw source titles. lang-dropped
        # events are INCLUDED: their prose failed the Persian gate, but
        # the event is real and its raw source title still informs (the
        # 2026-09-05 run lost its best item -- Israel/Lebanon, 17.794 --
        # this way).
        fates = dict(getattr(ctx, "cluster_fates", None) or [])
        raw_fallback = _raw_fallback(
            list(getattr(ctx, "clusters", None) or []),
            {e.event_key for e in events},
            fates, labels, settings.digest_rank.fallback_max_items,
        )

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
            if raw_fallback:
                text += "\n\n" + escape_html(raw_fallback)
            ctx.messages = [f"{warning}\n\n{text}" if warning else text]
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
            if raw_fallback:
                text += "\n\n" + escape_html(raw_fallback)
            ctx.messages = [f"{warning}\n\n{text}" if warning else text]
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
        if warning:
            # Above the digest header on purpose: it is a statement about the
            # system, not about the news, and it must be the first thing read.
            header = f"{warning}\n{header}"

        # Follow-up priority + item construction: pipeline/render.py's
        # build_digest_items (moved out 2026-09-06 when this file crossed
        # the ~200-line cap -- see that function's docstring for the
        # fix-1/fix-C rationale). Builds exactly one Item per event, no
        # skips, but round-4 review's fix 1 reorders HIGH-band compact
        # lines to the end -- `ordered_events` is the event list in the
        # SAME order as `items`, not `kept`'s order, and the
        # truncation-tracking below must enumerate `ordered_events`.
        items, ordered_events = build_digest_items(kept, clusters, settings, labels)

        message = Message(header=header, items=tuple(items),
                          footer=raw_fallback or None)
        max_units = settings.delivery.telegram_max_chars
        # format_split_tracked budgets and splits by priority; the digest may
        # span up to digest_rank.max_messages messages (owner decision).
        # `truncated_orders` are `message.items` indices that never rendered
        # into any page (round-2 review, fix 3): the OLD unconditional
        # `ctx.compose_kept_keys = [e.event_key for e in kept]` marked EVERY
        # kept event delivered regardless, including ones the budget cut
        # entirely. Fix 1 makes a follow-up the always-first-cut priority
        # class, so a truncated follow-up would have been marked delivered
        # unseen AND permanently raised its story's high-water mark --
        # silently suppressing it for the rest of the 72h window. `items`
        # was built with exactly one Item per event, in order, with no
        # skips, matching `ordered_events` (round-4 review, fix 1: HIGH-band
        # compact lines move to the end of `items`, so `ordered_events` --
        # NOT `kept` -- is the list whose index i maps 1:1 to items[i]).
        ctx.messages, truncated_orders = format_split_tracked(
            message, max_units=max_units, max_messages=settings.digest_rank.max_messages
        )
        ctx.counters["compose"] = len(kept)
        delivered = [e for i, e in enumerate(ordered_events) if i not in truncated_orders]
        ctx.compose_truncated = [
            e for i, e in enumerate(ordered_events) if i in truncated_orders
        ]
        # Received-marker keys for the anti-repetition window, recorded
        # after the rank cut (below-threshold events were never seen and
        # must not suppress their own follow-ups) AND after truncation
        # (fix 3 above) -- only events that actually rendered into a sent
        # message may enter this list. The deliver stage writes the markers
        # itself, only after real sends succeeded -- marking here would
        # re-create the ghost suppression on send failure (review finding
        # 2026-08-30).
        ctx.compose_kept_keys = [e.event_key for e in delivered]
        if ctx.compose_truncated:
            self._logger.info(
                "compose: %d event(s) truncated by the character budget -- "
                "not marked delivered: %s",
                len(ctx.compose_truncated),
                ", ".join(e.event_key[:8] for e in ctx.compose_truncated),
            )
        self._logger.info(
            "compose: %d event(s) -> %d message(s)", len(kept), len(ctx.messages)
        )
