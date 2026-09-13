"""Pure text helpers for the compose stage.

Split out of compose.py 2026-09-05 (review) to keep it under the ~200-line
cap (constraint 12): headline trimming, the raw-title fallback and the
Jalali when-text live here; the ComposeStage orchestration stays in
compose.py. Nothing here touches LLMs or state.

build_lead_message moved out to render_leads.py 2026-09-08 (round-4 review):
fix 1's rendering-order fix and its documentation pushed this file to 209
lines, over the ~200-line cap. The leads channel is a distinct concern (its
own message shape, no bands, no repeat gate) from the main digest's
per-event rendering that stays here.
"""

from __future__ import annotations

from agent.collectors.tz import to_tehran
from agent.delivery.message import Item
from agent.pipeline.labels import category_icon, category_name
from agent.util.jalali import format_jalali, to_persian_digits


def _headline(summary: str) -> str:
    """First sentence of the summary, capped -- informative, not truncated
    into nonsense: the cap cuts at a word boundary near 140 chars."""
    first = summary.split(". ")[0].strip(" .")
    if len(first) <= 140:
        return first
    cut = first[:137].rsplit(" ", 1)[0]
    return cut + "…"


# Understand-stage PROVIDER failures only -- an inclusion list on purpose:
# a repeat-drop is a validate judgment, not a provider failure, so it stays
# out. These are the fates understand.py actually writes for clusters the
# LLM could NOT cover (2026-09-05 review: the old tokens were wrong --
# "refused_cap" is never written, the cap-exhaustion fate is "cap_refused",
# a fatal 403 writes "fatal", and "lang_dropped" is dead here: a
# lang-dropped event's cluster has NO fate and reaches the fallback via the
# exclusion-set change in compose.py, not via this list).
_UNCOVERED_FATES = {
    "unavailable", "fatal", "cap_refused", "unparseable", "oversized",
}


def _raw_fallback(clusters: list, event_keys: set, fates: dict, labels: dict) -> str:
    """Count-only line for clusters the LLM could not cover (session 9s
    replaces the raw-title section -- the 22:56Z escalation run dumped
    untranslated English and Arabic headlines into both Persian messages,
    which is exactly the raw-source-titles class the tone contract
    forbids). Inclusion logic is the 2026-08-31 one UNCHANGED (provider
    failures and fate-less clusters only; content-filtered clusters are
    judgments, not failures); the render is now a count, which is a fact
    (constraint 11). Batching makes this line more prominent, not less --
    one lost batch voids several clusters at once."""
    uncovered = 0
    for cluster in clusters:
        fate = fates.get(cluster.key)
        if cluster.key in event_keys:
            continue
        if fate not in _UNCOVERED_FATES and fate is not None:
            continue  # judged (clickbait/irrelevant) or another stage's drop
        uncovered += 1
    if not uncovered:
        return ""
    return labels["raw_fallback"].format(
        count=to_persian_digits(str(uncovered))
    )


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


def build_digest_items(kept: list, clusters: dict, settings, labels: dict) -> tuple[list, list]:
    """Returns (items, ordered_events): one Item per event, no skips, but
    NOT in `kept`'s order any more (round-4 review fix 1 below reorders
    normal/MID entries ahead of every HIGH-band compact line) -- so
    `ordered_events[i]` is the event `items[i]` was built from, 1:1, and
    callers (compose.py's cap-truncation tracking) must enumerate
    `ordered_events`, never `kept` directly. Moved out of compose.py
    2026-09-06 (round-2 review) when that file crossed the ~200-line cap.

    Follow-ups, band-split (fix 1, 2026-09-06; priority corrected
    2026-09-06 review, fix C; MID/HIGH split round-4 review, fix 1):
    repeat_decision.py's two bands are different CLAIMS and must render
    differently.

      MID band  (`event.follow_up` True, `event.follow_up_high` False): a
        related but DIFFERENT story (repeat_decision.py's own docstring).
        Renders as a FULL normal entry -- detail line, natural importance
        rank among the other normal entries -- carrying only the
        "پیگیری · " continuity marker on the headline so the reader knows
        it relates to something already sent. The round-3 defect gave
        EVERY survivor (both bands) the worst priority and no detail,
        which pinned the day's highest-scoring stories under a full entry
        that scored lower, with zero character pressure involved.
      HIGH band (`event.follow_up_high` True): genuinely the SAME story
        retold with material development. Renders as the historical
        compact "پیگیری · headline" line, no summary, no detail. It gets a
        priority STRICTLY WORSE than every normal entry's (MID-band
        survivors now count as normal for this purpose), so under
        character pressure it is always the first thing cut -- never a
        normal, never-before-delivered story or a MID-band different
        story. The old value was `max(normal_count - 1, 0)`, i.e. the SAME
        priority as the last normal entry; with equal priority budget.py's
        (priority, order) tie-break let a follow-up earlier in `kept`
        displace a lower-ranked normal entry that had never been delivered
        at all. `normal_count` itself is one past every normal index, so
        it never ties.

    Round-4 review follow-up (caught by
    test_high_band_follow_up_renders_below_every_normal_entry_regardless_of_score):
    `priority` only governs which fragments SURVIVE the character budget
    (budget.py's `_greedy_pack` ranks by `(priority, order)` for
    inclusion) -- the ASSEMBLED message renders fragments back in `order`
    (formatter.py's enumerate index), regardless of priority. Giving a
    HIGH-band item the worst priority while leaving it at its natural
    importance-ranked POSITION in `kept` therefore left it printed wherever
    its score put it, priority only deciding it would be the first thing
    cut if the message ever ran tight -- never actually moving it below the
    normal entries in a message that fits. Fix: emit every normal/MID entry
    FIRST, in `kept`'s importance order, THEN every HIGH-band compact line,
    in `kept`'s relative order among themselves -- `order` (list position)
    now matches `priority`'s intent for both bands.
    """
    normal_events = [e for e in kept if not getattr(e, "follow_up_high", False)]
    high_events = [e for e in kept if getattr(e, "follow_up_high", False)]
    normal_count = len(normal_events)
    worst_normal_priority = normal_count
    ordered_events = normal_events + high_events
    items = []
    for normal_index, event in enumerate(normal_events):
        cluster = clusters.get(event.event_key)
        headline = event.headline.strip() if event.headline else _headline(event.summary)
        when = _when_text(cluster, labels) if cluster is not None else ""
        name = category_name(settings.delivery.output_language, event.category)
        # The LLM's own informative headline is the title; the summary is
        # the detail BEYOND it. Fallback (no headline field): the
        # summary's first sentence, as before.
        if getattr(event, "follow_up", False):
            # MID-band survivor: full entry, continuity marker only.
            headline = f"{labels['follow_up']} · {headline}"
        if event.claim_status == "rumour":
            headline = f"{labels['rumour']} · {headline}"
        elif event.claim_status == "unconfirmed":
            headline = f"{labels['unconfirmed']} · {headline}"
        headline = f"{category_icon(event.category)} {headline}"
        detail_bits = [name, when, event.summary] if when else [name, event.summary]
        items.append(Item(
            headline=headline,
            priority=normal_index,
            detail=" · ".join(detail_bits),
        ))
    for event in high_events:
        headline = event.headline.strip() if event.headline else _headline(event.summary)
        items.append(Item(
            headline=f"{labels['follow_up']} · {headline}",
            priority=worst_normal_priority,
        ))
    return items, ordered_events
