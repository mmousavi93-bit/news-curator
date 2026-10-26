"""Pure queue/render/receipt fields shared by the detailed CSV writers."""
from agent.pipeline.rank import score_event

LIFECYCLE_COLUMNS = ("composition_state", "delivery_state", "origin")


def event_map(ctx):
    events = {}
    # The actual selected/stored snapshot beats a rejected current same-key copy.
    for attr in ("compose_selected", "compose_truncated", "queue_selected",
                 "queue_deferred", "queue_expired", "events", "repeat_dropped",
                 "lang_dropped", "rank_dropped", "relevance_dropped", "lead_events"):
        for event in getattr(ctx, attr, None) or []:
            events.setdefault(event.event_key, event)
    return events


def pending_keys(ctx):
    keys = set(getattr(ctx, "queue_pending_keys", None) or [])
    keys.update(e.event_key for e in getattr(ctx, "queue_expired", None) or [])
    return keys


def lifecycle_columns(ctx, key):
    forms = getattr(ctx, "compose_render_forms", None) or {}
    if key in forms:
        composition = forms[key]
    elif key in {e.event_key for e in getattr(ctx, "compose_truncated", None) or []}:
        composition = "budget_excluded"
    elif key in {e.event_key for e in getattr(ctx, "queue_deferred", None) or []}:
        composition = "deferred"
    elif key in {e.event_key for e in getattr(ctx, "queue_expired", None) or []}:
        composition = "expired"
    elif key in (getattr(ctx, "compose_kept_keys", None) or []):
        composition = "rendered_legacy"
    else:
        composition = "not_selected"
    states = getattr(ctx, "delivery_states", None) or {}
    origin = ("pending" if key in pending_keys(ctx) else "fresh" if key in {
        e.event_key for e in getattr(ctx, "events", None) or []} else "unknown")
    return composition, states.get(key, "not_attempted"), origin


def score_text(ctx, event, cluster):
    if event is None:
        return ""
    stored = getattr(ctx, "queue_scores", None) or {}
    if event.event_key in stored:
        return f"{stored[event.event_key]:.3f}"
    if cluster is None:
        return ""
    return f"{score_event(event, cluster, ctx.config.credibility, ctx.config.settings, ctx.now):.3f}"


def receipt_metrics(ctx):
    pages = getattr(ctx, "delivery_page_states", None) or []
    return [
        ("selected", len(getattr(ctx, "compose_selected", None) or [])),
        ("rendered", len(getattr(ctx, "compose_kept_keys", None) or [])),
        ("delivered_events", len(set(getattr(ctx, "delivered_keys", None) or []))),
        ("acknowledged_messages", pages.count("acknowledged")),
        ("failed_or_unknown_messages", pages.count("failed_or_unknown")),
        ("mocked_messages", pages.count("mocked")),
        ("dry_run_messages", pages.count("dry_run")),
    ]
