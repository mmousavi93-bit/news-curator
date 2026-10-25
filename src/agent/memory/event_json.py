"""Full-fidelity Event <-> JSON for the pending queue (Session 25).

The `events` table stores only the persisted subset (summary/entities/claim/
counts); the digest queue must re-render a deferred event exactly as it was
ranked, so this carries the in-memory fields too (headline/category/
significance/why_matters/follow_up/prior_context). Split out of
event_models.py 2026-10-25 to keep that file under the ~200-line cap
(constraint 12).
"""

from __future__ import annotations

import json

from agent.memory.event_models import Event
from agent.memory.models import from_utc_iso, to_utc_iso


def event_to_json(event: Event) -> str:
    """Full-fidelity JSON for the pending queue -- the in-memory fields the
    `events` table does not persist, so a deferred event re-renders exactly as
    it was ranked."""
    return json.dumps({
        "event_key": event.event_key,
        "summary": event.summary,
        "headline": event.headline,
        "entities": list(event.entities),
        "category": event.category,
        "significance": event.significance,
        "why_matters": event.why_matters,
        "claim_status": event.claim_status,
        "source_count": event.source_count,
        "independent_count": event.independent_count,
        "follow_up": event.follow_up,
        "follow_up_high": event.follow_up_high,
        "prior_context": event.prior_context,
        "first_seen_at": to_utc_iso(event.first_seen_at),
        "last_updated_at": to_utc_iso(event.last_updated_at),
    })


def event_from_json(raw: str) -> Event:
    d = json.loads(raw)
    return Event(
        event_key=d["event_key"],
        summary=d["summary"],
        headline=d.get("headline", ""),
        entities=tuple(d.get("entities") or []),
        category=d.get("category", "other"),
        significance=d.get("significance", "economy"),
        why_matters=d.get("why_matters", ""),
        claim_status=d.get("claim_status", "unconfirmed"),
        source_count=int(d.get("source_count", 0)),
        independent_count=int(d.get("independent_count", 0)),
        follow_up=bool(d.get("follow_up", False)),
        follow_up_high=bool(d.get("follow_up_high", False)),
        prior_context=d.get("prior_context", ""),
        first_seen_at=from_utc_iso(d["first_seen_at"]) if d.get("first_seen_at") else None,
        last_updated_at=from_utc_iso(d["last_updated_at"]) if d.get("last_updated_at") else None,
    )
