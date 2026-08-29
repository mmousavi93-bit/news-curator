"""The compose stage: ctx.events become ONE budgeted Telegram message, or
an honest one-liner when nothing changed (constraint 11).

Tone contract (CLAUDE.md): calm, factual, no drama -- the message text this
stage builds IS the tone surface, alongside config/prompts. Alert tiers are
the scorer's job (v1.5); v1 framing is the run date and, on the 07:00 run,
the daily digest marker.

Dates are Tehran wall-clock, display-only, and date_only is respected: when
every member of a cluster is date-only, the time is stated as unknown --
never a midnight placeholder rendered as 03:30 Tehran (constraints 10, 11).
"""

from __future__ import annotations

import logging

from agent.collectors.tz import to_tehran
from agent.delivery.formatter import format_single
from agent.delivery.message import Item, Message

_NOTHING_NEW = "Nothing new since the last run."


def _headline(summary: str) -> str:
    """First sentence of the summary, capped -- a headline, not a cut."""
    first = summary.split(". ")[0].strip(" .")
    return first if len(first) <= 120 else first[:117] + "..."


def _when_text(cluster) -> str:
    """Tehran display for the event's latest time. If EVERY member is
    date_only, the feed gave no time -- say so rather than invent one."""
    dated = [m for m in cluster.members if m.published_at is not None]
    if not dated:
        return "date unknown"
    latest = max(m.published_at for m in dated)
    shown = to_tehran(latest)
    if all(m.date_only for m in dated):
        return f"{shown:%Y-%m-%d} (time not stated)"
    return f"{shown:%Y-%m-%d %H:%M} Tehran"


class ComposeStage:
    """Writes ctx.message (the formatted, budgeted text) and sets the
    `compose` counter to the number of events included."""

    name = "compose"

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def run(self, ctx) -> None:
        events = list(getattr(ctx, "events", None) or [])
        clusters = {c.key: c for c in getattr(ctx, "clusters", None) or []}

        if not events:
            ctx.message = _NOTHING_NEW
            ctx.counters["compose"] = 0
            self._logger.info("compose: no events -- honest one-liner")
            return

        marker = " — daily digest" if getattr(ctx, "daily_digest", False) else ""
        header = f"News Curator — {to_tehran(ctx.now):%Y-%m-%d %H:%M} Tehran{marker}"

        items = []
        for index, event in enumerate(events):
            # ctx.events is already in cluster-priority order (tier, then
            # recency); enumerate keeps that order as explicit priorities.
            cluster = clusters.get(event.event_key)
            when = _when_text(cluster) if cluster is not None else ""
            detail = f"{when} — {event.summary}" if when else event.summary
            # Constraint 10, made visible: a single-source event is labelled
            # RUMOUR in the message itself, not just in the database.
            headline = _headline(event.summary)
            if event.claim_status == "rumour":
                headline = f"[RUMOUR] {headline}"
            items.append(Item(
                headline=headline,
                priority=index,
                detail=detail,
            ))

        message = Message(header=header, items=tuple(items), footer=None)
        # format_single budgets internally (Phase 2): truncation by
        # priority, never mid-tag, capped at Telegram's own limit.
        max_units = ctx.config.settings.delivery.telegram_max_chars
        ctx.message = format_single(message, max_units=max_units)
        ctx.counters["compose"] = len(events)
        self._logger.info("compose: %d events -> message (%d chars)",
                          len(events), len(ctx.message))

        # Lead channel (optional, LEAD_HANDLING.md v1): only when the
        # owner configured TELEGRAM_LEADS_CHANNEL_ID. Lead-only events never
        # reach the MAIN message -- the validate stage split them out.
        lead_events = list(getattr(ctx, "lead_events", None) or [])
        if lead_events and ctx.leads_channel_id:
            lead_items = []
            for index, event in enumerate(lead_events):
                lead_items.append(Item(
                    headline=f"[LEAD] {_headline(event.summary)}",
                    priority=index,
                    detail=f"{event.summary}",
                ))
            lead_message = Message(
                header="News Curator — lead channel (unverified, weight 0)",
                items=tuple(lead_items),
                footer=None,
            )
            ctx.lead_message = format_single(lead_message, max_units=max_units)
            self._logger.info("compose: %d lead event(s) -> lead message", len(lead_events))
