"""The leads-channel message builder.

Split out of render.py 2026-09-08 (round-4 review, fix 1 pushed render.py
to 209 lines, over the ~200-line cap -- constraint 12: a file past the cap
is doing two jobs). render.py owns the MAIN digest's per-event rendering
(build_digest_items and its helpers); this module owns the separate leads
channel, which has its own message shape (no bands, no repeat gate, no
category icons) and is never touched by the main-digest band logic. Nothing
here touches LLMs or state.
"""

from __future__ import annotations

from agent.delivery.formatter import format_split
from agent.delivery.message import Item, Message
from agent.pipeline.labels import labels_for
from agent.pipeline.langgate import split_persian
from agent.pipeline.render import _headline


def build_lead_message(ctx, settings, logger) -> None:
    """Leads channel message. Lead events are gated by the Persian output
    gate like main events, but are NEVER marked delivered: their
    corroborated confirmation must reach the main feed (schema.sql note).
    Moved out of compose.py 2026-09-06 (fix 1) when that file crossed the
    ~200-line cap (constraint 12); moved again out of render.py 2026-09-08
    (round-4 review) for the same reason."""
    labels = labels_for(settings.delivery.output_language)
    lead_events = list(getattr(ctx, "lead_events", None) or [])
    if lead_events:
        lead_events, lead_lang_dropped = split_persian(lead_events)
        if lead_lang_dropped:
            logger.warning(
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
