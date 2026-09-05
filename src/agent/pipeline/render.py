"""Pure text helpers for the compose stage.

Split out of compose.py 2026-09-05 (review) to keep it under the ~200-line
cap (constraint 12): headline trimming, the raw-title fallback and the
Jalali when-text live here; the ComposeStage orchestration stays in
compose.py. Nothing here touches LLMs or state.
"""

from __future__ import annotations

from agent.collectors.tz import to_tehran
from agent.util.jalali import format_jalali


def _headline(summary: str) -> str:
    """First sentence of the summary, capped -- informative, not truncated
    into nonsense: the cap cuts at a word boundary near 140 chars."""
    first = summary.split(". ")[0].strip(" .")
    if len(first) <= 140:
        return first
    cut = first[:137].rsplit(" ", 1)[0]
    return cut + "…"


_RAW_TITLE_CAP = 110
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


def _raw_fallback(clusters: list, event_keys: set, fates: dict, labels: dict,
                  max_items: int) -> str:
    """Raw-title section for clusters the LLM could not cover (move 1,
    2026-08-31: the product survives total LLM loss). Content-filtered
    clusters (clickbait/irrelevant) are judgments, not failures — they
    stay out. Source titles are quoted text, displayed as-is; the
    formatter (or the plain-text path) escapes them."""
    lines: list[str] = []
    for cluster in clusters:
        fate = fates.get(cluster.key)
        if cluster.key in event_keys:
            continue
        if fate not in _UNCOVERED_FATES and fate is not None:
            continue  # judged (clickbait/irrelevant) or another stage's drop
        title = cluster.members[0].title.strip()
        if not title:
            # Telegram posts carry no title: the body lead stands in —
            # an empty bullet is worse than nothing (owner 2026-08-31).
            title = (cluster.members[0].body or "").strip()[:_RAW_TITLE_CAP]
        if not title:
            continue
        if len(title) > _RAW_TITLE_CAP:
            title = title[:_RAW_TITLE_CAP] + "…"
        lines.append(f"• {title}")
        if len(lines) >= max_items:
            break
    if not lines:
        return ""
    return labels["raw_fallback"] + "\n" + "\n".join(lines)


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
