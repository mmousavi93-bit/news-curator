"""Compose Persian digests from fresh and pending candidates, or an honest
one-liner. Deterministic ranking, labels, Jalali dates, whole-item packing
and exact per-message event mappings belong here. Text construction lives
in render.py/render_leads.py; configurable prose stays in config/prompts.
"""

from __future__ import annotations

import logging

from agent.collectors.tz import to_tehran
from agent.delivery.formatter import escape_html, format_split_tracked
from agent.delivery.message import Message
from agent.pipeline.deescalation import is_escalation, maybe_deescalation_notice
from agent.pipeline.flash_watchdog import flash_warning
from agent.pipeline.labels import labels_for
from agent.pipeline.langgate import split_persian
from agent.pipeline.queue import select_digest
from agent.pipeline.rank import rank_events
from agent.pipeline.render import _raw_fallback, build_digest_items
from agent.pipeline.render_leads import build_lead_message
from agent.pipeline.synthesis import build_synthesis
from agent.util.jalali import format_jalali


class ComposeStage:
    """Writes ctx.messages (formatted, budgeted, ranked) and the optional
    lead message; `compose` counter = events included."""

    name = "compose"

    def __init__(self, logger: logging.Logger, synthesis_template: str = "") -> None:
        self._logger = logger
        self._synthesis_template = synthesis_template

    def run(self, ctx) -> None:
        settings = ctx.config.settings
        labels = labels_for(settings.delivery.output_language)
        events = list(getattr(ctx, "events", None) or [])
        clusters = {c.key: c for c in getattr(ctx, "clusters", None) or []}
        ctx.compose_kept_keys = []
        ctx.compose_selected = []
        ctx.compose_truncated = []
        ctx.compose_render_forms = {}
        ctx.message_event_keys = []
        ctx.delivery_states = {}
        ctx.delivered_keys = []
        ctx.delivery_page_states = []
        # Flash-monitor liveness (9r). Computed BEFORE any budgeting so the
        # line is inside the character budget, never appended after the split
        # (constraint 8: never discover the 4,096 cap at send time). It rides
        # the honest one-liner too -- a "nothing new" run is exactly when a
        # dead alerter is most dangerous and least visible.
        warning = flash_warning(settings, labels)
        # De-escalation notice (9z): computed once, prepended above the
        # header on every path -- it fires precisely when the military/
        # security stream has been QUIET, which is often a "nothing new"
        # run. Missing db / disabled / no history -> None, never a crash.
        notice = maybe_deescalation_notice(
            getattr(ctx, "db", None), ctx.now, ctx.config.deescalation, labels
        )
        ctx.deescalation_notice = notice
        ctx.escalation_delivered = False
        preamble_parts = [p for p in (warning, notice) if p]
        preamble = "\n\n".join(preamble_parts) if preamble_parts else ""
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
        # Repeat-drops carry no fate (a summarized event is never a provider
        # failure) and validate.py strips them from ctx.events before this
        # point, so they used to slip past the exclusion set below and inflate
        # the "بدون خلاصه ماند" count (18 vs 5 in run 34846355119). Exclude
        # their keys explicitly: a duplicate DID get a summary, it was just
        # not re-sent. lang-dropped events stay OUT of this set on purpose --
        # their prose failed the Persian gate, so they still count as uncovered.
        excluded = {e.event_key for e in events}
        excluded.update(getattr(ctx, "repeat_drop_reasons", None) or {})
        raw_fallback = _raw_fallback(
            list(getattr(ctx, "clusters", None) or []),
            excluded,
            fates, labels,
        )

        fresh, dropped, gated = rank_events(
            events, clusters, ctx.config.credibility, settings, ctx.now,
            self._logger,
        )
        ctx.rank_dropped = dropped
        ctx.relevance_dropped = gated
        # Selection always considers pending, even on quiet/degraded runs.
        kept = select_digest(ctx, fresh, settings, self._logger)
        coverage = (labels["lang_dropped"] if lang_dropped and not events
                    else labels["ai_unavailable"] if getattr(ctx, "llm_failed", False)
                    else "")
        if not kept:
            text = coverage or labels["nothing_new"]
            if raw_fallback:
                text += "\n\n" + escape_html(raw_fallback)
            ctx.messages = [f"{preamble}\n\n{text}" if preamble else text]
            ctx.counters["compose"] = 0
            self._logger.info("compose: no eligible fresh or pending events")
            return
        # Earlier summaries do not imply this run's fresh coverage succeeded.
        if coverage:
            preamble = "\n\n".join(p for p in (preamble, coverage) if p)

        now_tehran = to_tehran(ctx.now)
        marker = (
            f" — {labels['digest_marker']}"
            if getattr(ctx, "daily_digest", False) else ""
        )
        header = (
            f"{labels['header']} — {format_jalali(now_tehran, with_time=True)}"
            f" {labels['tehran']}{marker}"
        )
        # Session 25: the one-line synthesis ("big picture") under the header.
        synthesis = ""
        if (settings.digest_rank.synthesis_enabled and len(kept) >= 2
                and self._synthesis_template):
            synthesis = build_synthesis(
                ctx, kept, self._synthesis_template, self._logger
            )
        if synthesis:
            header = f"{header}\n{labels['synthesis_header']}: {synthesis}"
        if preamble:
            # Above the digest header on purpose: the watchdog warning is a
            # statement about the system, the de-escalation notice about the
            # news -- both must be the first thing read.
            header = f"{preamble}\n{header}"

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
        # The packer supplies exact item indices and full/headline-only forms.
        # This follows ordered_events, not rank order or text/headline matching.
        page_forms: list[dict[int, str]] = []
        ctx.messages, truncated_orders = format_split_tracked(
            message, max_units=max_units, max_messages=settings.digest_rank.max_messages,
            page_forms=page_forms,
        )
        ctx.message_event_keys = [
            [ordered_events[i].event_key for i in sorted(forms)] for forms in page_forms
        ]
        ctx.compose_render_forms = {
            ordered_events[i].event_key: form for forms in page_forms for i, form in forms.items()
        }
        ctx.compose_selected = ordered_events
        ctx.counters["compose"] = len(kept)
        delivered = [e for i, e in enumerate(ordered_events) if i not in truncated_orders]
        ctx.escalation_delivered = any(
            is_escalation(e, ctx.config.relevance) for e in delivered
        )
        ctx.compose_truncated = [
            e for i, e in enumerate(ordered_events) if i in truncated_orders
        ]
        # Only actually rendered keys may receive delivery markers; DeliverStage
        # writes those markers after each real-success page, never at composition.
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
