"""The validate stage: deterministic credibility arithmetic over clusters
(constraint 3 -- no LLM calls anywhere in this package).

Per event: independent_count = distinct credibility GROUPS among tier-1/2
members (decision 4 -- two outlets of one newsroom are one report, not
confirmation); claim_status = likely / unconfirmed / rumour from that
count; lead-only clusters split into ctx.lead_events and never reach the
main message (gate: a lead alone never reaches output).

This is NOT the same count as pipeline/cluster.py's
`Cluster.independent_count()`, which spans every non-lead tier (1/2/3) for
chosen.csv's raw corroboration-plus-amplification column.
`Cluster.corroborating_count()` is the method over there that mirrors
THIS module's rule exactly (round-3 review, fix 5 -- the two docstrings
had drifted apart and cluster.py wrongly claimed parity with this file).

CLASSIFICATION RUNS BEFORE THE REPEAT GATE (fix 1, 2026-09-06, reordered
from the original run()): pipeline/repeats.py's bypass logic needs
independent_count and claim_status to decide whether a matched "repeat" is
a development worth keeping, and the OLD order dropped repeats first, so a
matched event was judged on Event() constructor defaults
(independent_count=0, claim_status="unconfirmed") rather than its real
corroboration -- this is exactly how the 2026-09-05 run suppressed a
genuine IRGC-retaliation follow-up. Both loops are pure functions of
ctx.clusters/ctx.events and neither depends on the OTHER's output except
in this one direction, so the reorder has no other effect.

The two anti-repetition passes live in their own modules -- split out
2026-09-06 when this file crossed the ~200-line cap (constraint 12):
cross-run repeat gate in pipeline/repeats.py, same-run duplicate collapse
in pipeline/samerun_dedup.py (repeats.py itself later crossed the cap on
its own, hence the further split).

Lead outcomes are written silently -- see memory/lead_models.py.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Mapping

from agent.memory.event_models import Event, update_validation
from agent.memory.lead_models import LeadOutcome, insert_lead_outcomes
from agent.pipeline.independence import _tier, independent_groups
from agent.pipeline.repeats import drop_repeats
from agent.pipeline.samerun_dedup import drop_same_run_dups


def _merge_reasons(
    repeat_reasons: dict[str, str], same_run_reasons: dict[str, str]
) -> dict[str, str]:
    """Union of both drop-reason dicts, APPENDING rather than overwriting
    when the same event_key appears in both (round-3 review, fix 4): an
    event that survived the cross-run repeat gate as a MID-band follow-up
    (recorded in repeat_reasons -- band=/sim=/score=/prior=) can still be
    dropped moments later as a same-run duplicate of another kept event
    (same_run_reasons) -- the old `{**repeat_reasons, **same_run_reasons}`
    let the same-run reason silently overwrite the repeat-gate context for
    that key, so the owner lost exactly the information fix 2 just added.
    Repeat-gate reason first (it ran first); both stay independently
    greppable."""
    merged = dict(repeat_reasons)
    for key, reason in same_run_reasons.items():
        merged[key] = f"{merged[key]} | {reason}" if key in merged else reason
    return merged


def classify_event(
    cluster, credibility: Mapping[str, object]
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
    lead-only into ctx.lead_events. The repeat gate (owner decision
    2026-08-29, refined 2026-09-06 -- fix 1, pipeline/repeats.py) drops a
    matched follow-up ONLY IF it shows no material development; a
    developing story ships as a compact `follow_up` line instead
    (compose.py), never a full entry ("a change that produces more output
    is probably wrong"). Persists claim_status/independent_count and
    lead_outcomes when ctx.db is present."""

    name = "validate"

    def __init__(self, credibility: Mapping[str, object], logger: logging.Logger) -> None:
        self._credibility = credibility
        self._logger = logger

    def _classify(
        self, ctx, events: list[Event]
    ) -> tuple[list[Event], list[Event], list[LeadOutcome]]:
        """Splits events into (main, lead_only) and stamps claim_status /
        independent_count on the main set. MUST run before the repeat gate
        -- see the module docstring."""
        clusters_by_key = {c.key: c for c in getattr(ctx, "clusters", None) or []}
        classified: list[Event] = []
        lead_events: list[Event] = []
        lead_outcomes: list[LeadOutcome] = []
        for event in events:
            cluster = clusters_by_key.get(event.event_key)
            if cluster is None:
                classified.append(event)
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
            updated = replace(event, claim_status=status, independent_count=len(groups))
            classified.append(updated)
            for lead_id in leads:
                lead_outcomes.append(LeadOutcome(
                    lead_source_id=lead_id, event_key=event.event_key,
                    outcome="confirmed" if status == "likely" else "unconfirmed",
                    observed_at=ctx.now,
                ))
        return classified, lead_events, lead_outcomes

    def run(self, ctx) -> None:
        events = list(getattr(ctx, "events", None) or [])
        classified, lead_events, lead_outcomes = self._classify(ctx, events)
        # Fix D REVERTED, round-2 review 2026-09-06: leads must NOT enter
        # the repeat gate. Two independent reasons the 2026-09-06 "fix D"
        # was wrong: (a) it never achieved its stated purpose -- a lead
        # event never enters ctx.compose_kept_keys (compose.py excludes
        # leads from the received-marker keys by design), so it is never
        # mark_delivered, so read_recent_events(delivered_only=True) could
        # never return a lead prior anyway -- leads kept repeating
        # regardless of whether they passed through drop_repeats; (b)
        # drop_same_run_dups picks its survivor on cluster size with no
        # tier awareness (fixed below to rank corroboration first, but
        # leads still contribute zero corroboration by definition), so
        # letting leads INTO that pass risked several reposting lead
        # channels outnumbering and deleting a genuinely corroborated
        # tier-1/2 report on the same story -- inverting the rule that a
        # lead alone must never reach output. Leads are therefore split out
        # and left untouched; only classified (tier-1/2/3) events pass
        # through the anti-repetition gates.
        kept, repeat_dropped, repeat_reasons = drop_repeats(
            ctx, self._credibility, classified, self._logger
        )
        kept, same_run_dropped, same_run_reasons = drop_same_run_dups(ctx, kept, self._logger)
        ctx.repeat_dropped = repeat_dropped + same_run_dropped
        # Fix E, 2026-09-06 review: per-event drop reasons for chosen.csv's
        # `reason` column (report_csv.py's repeat_dropped fate). Merge
        # APPENDS rather than overwrites on key collision (fix 4, round-3
        # review) -- see _merge_reasons.
        ctx.repeat_drop_reasons = _merge_reasons(repeat_reasons, same_run_reasons)

        ctx.events = kept
        ctx.lead_events = lead_events
        ctx.counters["validate"] = len(ctx.events)
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
            len(classified),
            sum(1 for e in kept if e.claim_status == "likely"),
            sum(1 for e in kept if e.claim_status == "unconfirmed"),
            sum(1 for e in kept if e.claim_status == "rumour"),
        )
