"""The validate stage: deterministic credibility arithmetic over clusters
(constraint 3 -- no LLM calls anywhere in this package).

Per event: independent_count = distinct credibility GROUPS among tier-1/2
members (decision 4 -- two outlets of one newsroom are one report, not
confirmation); claim_status = likely / unconfirmed / rumour from that
count; lead-only clusters split into ctx.lead_events and never reach the
main message (gate: a lead alone never reaches output).

Lead outcomes are written silently -- see memory/lead_models.py.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Mapping, Sequence

from agent.memory.event_models import Event, read_recent_events, update_validation
from agent.memory.lead_models import LeadOutcome, insert_lead_outcomes
from agent.pipeline.cluster import Cluster


def _tier(credibility: Mapping[str, object], source_id: str) -> str:
    entry = credibility.get(source_id)
    if entry is None:
        return "3"  # unlisted = tier 3 fallback (the join check prevents this)
    return str(getattr(entry, "tier", "3"))


def _group(credibility: Mapping[str, object], source_id: str) -> str:
    entry = credibility.get(source_id)
    group = getattr(entry, "group", None) if entry is not None else None
    # group: null resolves to the source's own id -- fully independent by
    # default (credibility.yaml's documented fallback).
    return group if isinstance(group, str) and group else source_id


def independent_groups(
    source_ids: Sequence[str], credibility: Mapping[str, object]
) -> set[str]:
    """Distinct corroborating groups among tier-1/2 members. Tier 3 and
    lead members never corroborate (rulebook Step 1)."""
    groups: set[str] = set()
    for sid in source_ids:
        if _tier(credibility, sid) in ("1", "2"):
            groups.add(_group(credibility, sid))
    return groups


def classify_event(
    cluster: Cluster, credibility: Mapping[str, object]
) -> tuple[str, set[str], set[str]]:
    """Returns (claim_status, corroborating_groups, lead_source_ids).
    Lead-only clusters return claim_status 'lead_only' -- they are split
    out, never rumoured into the main feed."""
    source_ids = [m.source_id for m in cluster.members]
    leads = {sid for sid in source_ids if _tier(credibility, sid) == "lead"}
    groups = independent_groups(source_ids, credibility)
    if groups:
        status = "likely" if len(groups) >= 2 else "unconfirmed"
    elif leads and leads == set(source_ids):
        status = "lead_only"
    else:
        status = "rumour"
    return status, groups, leads


class ValidateStage:
    """Splits ctx.clusters' events: validated events into ctx.events,
    lead-only into ctx.lead_events; drops repeat follow-ups (owner decision
    2026-08-29); persists claim_status/independent_count and lead_outcomes
    when ctx.db is present."""

    name = "validate"

    def __init__(self, credibility: Mapping[str, object], logger: logging.Logger) -> None:
        self._credibility = credibility
        self._logger = logger

    @staticmethod
    def _cosine(a, b) -> float:
        # Vectors are unit-normalised upstream (same contract as
        # pipeline/cluster.py) -- cosine is the dot product.
        return sum(x * y for x, y in zip(a, b))

    def _drop_repeats(self, ctx, events: list[Event]) -> list[Event]:
        """Anti-repetition: a follow-up story on an event the owner already
        saw must not appear again. Match each new event's summary against
        recent stored events' summaries with the LOCAL embedder (zero LLM
        calls, zero quota); drop matches at/above event_match_threshold.
        Skipped when the db or embedder is absent (dry-run / mock)."""
        if getattr(ctx, "db", None) is None or getattr(ctx, "embedder", None) is None:
            return events
        window = ctx.config.settings.digest_rank.repeat_window_hours
        recent = read_recent_events(ctx.db, hours=window, now=ctx.now)
        if not recent or not events:
            return events
        threshold = ctx.config.settings.pipeline.event_match_threshold
        new_vectors = ctx.embedder.embed([e.summary for e in events])
        old_vectors = ctx.embedder.embed([e.summary for e in recent])
        kept: list[Event] = []
        for event, vector in zip(events, new_vectors):
            best = max((self._cosine(vector, old) for old in old_vectors), default=0.0)
            if best >= threshold:
                self._logger.info(
                    "validate: %s dropped as a repeat (sim %.2f)", event.event_key[:8], best
                )
                continue
            kept.append(event)
        return kept

    def run(self, ctx) -> None:
        clusters = list(getattr(ctx, "clusters", None) or [])
        events = list(getattr(ctx, "events", None) or [])
        by_key = {c.key: c for c in clusters}
        events = self._drop_repeats(ctx, events)

        kept: list[Event] = []
        lead_events: list[Event] = []
        lead_outcomes: list[LeadOutcome] = []

        for event in events:
            cluster = by_key.get(event.event_key)
            if cluster is None:
                kept.append(event)
                continue
            status, groups, leads = classify_event(cluster, self._credibility)
            if status == "lead_only":
                lead_events.append(event)
                for lead_id in leads:
                    lead_outcomes.append(LeadOutcome(
                        lead_source_id=lead_id, event_key=event.event_key,
                        outcome="raised", observed_at=ctx.now,
                    ))
                continue
            updated = replace(event, claim_status=status,
                              independent_count=len(groups))
            kept.append(updated)
            for lead_id in leads:
                lead_outcomes.append(LeadOutcome(
                    lead_source_id=lead_id, event_key=event.event_key,
                    outcome="confirmed" if status == "likely" else "unconfirmed",
                    observed_at=ctx.now,
                ))

        ctx.events = kept
        ctx.lead_events = lead_events
        ctx.counters["validate"] = len(kept)
        if ctx.db is not None:
            if kept:
                update_validation(ctx.db, kept)
            if lead_outcomes:
                insert_lead_outcomes(ctx.db, lead_outcomes)
        if lead_events:
            self._logger.info(
                "validate: %d lead-only event(s) split out of the main feed", len(lead_events)
            )
        self._logger.info(
            "validate: %d event(s) -> %d likely / %d unconfirmed / %d rumour",
            len(events),
            sum(1 for e in kept if e.claim_status == "likely"),
            sum(1 for e in kept if e.claim_status == "unconfirmed"),
            sum(1 for e in kept if e.claim_status == "rumour"),
        )
