"""Cross-run repeat gate: split out of validate.py 2026-09-06 (fix 1) when
it crossed the ~200-line cap. validate.py owns classification (claim_status
/ independent_count / lead split); this module owns drop_repeats, the
cross-run anti-repetition pass over already-classified events -- a
follow-up on an event the owner already RECEIVED is judged by a TWO-BAND
similarity gate, see pipeline/repeat_decision.py's _decide. The SAME-run
duplicate pass (drop_same_run_dups) lives in pipeline/samerun_dedup.py --
split out 2026-09-06 when this file ALSO crossed the cap on its own; it
imports `_cosine` from here. The decision function itself (_decide,
_high_water, _claim_rank) moved to pipeline/repeat_decision.py 2026-09-06
(round-3 review, fix 2) when threading the matched prior's event_key
through the reason string pushed this file over the cap again.

drop_repeats returns a third value: {event_key: reason}, machine-greppable
(`band=`, `sim=`, `score=`, `kept=`/`blocked=`) so report_csv.py can write
it straight into chosen.csv's `reason` column -- for BOTH outcomes (round-2
review, fix 5): a survived bypass needs a reason exactly as much as a drop
does, or the owner can only calibrate from one side of the gate.

MUST run after validate.py's classification loop: the bypass conditions
compare the new event's independent_count/claim_status against the priors
it matched, so both sides need real values, not Event() constructor
defaults.

ROUND-2 REVIEW (2026-09-06), fix 1 -- TWO-BAND GATE replaces the single
conjunction. The reviewer proved the old design (one threshold,
`event_match_threshold` 0.55, doubling as both "worth comparing" and "same
story") blacked out real developments: a strike and the retaliation
answering it share vocabulary and land around cosine 0.6-0.65, but the
delivered prior is systematically the highest-corroboration member of its
story family (delivery is score-selected, score rewards corroboration), so
the high-water ratchet started at the family maximum and no later
development could clear it -- a 72h blackout on the exact story class this
system exists to surface.

  HIGH band  sim >= event_repeat_threshold -- genuinely the same story
             retold. The conjunction (score floor AND development over the
             high-water mark) is correct HERE: a retelling that adds
             nothing really is nothing.
  MID band   event_match_threshold <= sim < event_repeat_threshold --
             related but a DIFFERENT story. No development test -- the
             events are demonstrably not the same story, so ratcheting a
             new story against an old one's corroboration is meaningless.
             Survives as a follow-up on the score floor alone.

Volume check against the review's measured run (owner evidence, not a
guess): all 12 repeat-drops sat at similarity 0.55-0.70 -- entirely inside
the MID band as now defined, and ZERO traffic ever reached the HIGH band,
which is the proof 0.55 was being used as a same-story test it cannot
support. Of those 12: 5 score >= repeat_bypass_score (11.998, 11.972,
11.748, 11.740, 11.300) now survive as compact follow-up lines; 7 stay
below it (10.844, 10.549, 8.852, 7.914, 7.844, 7.644, 7.447) and stay
dropped -- net +5 compact lines on that run, not a flood.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Mapping

from agent.memory.event_models import Event, read_recent_events
from agent.pipeline.repeat_decision import _decide


def _cosine(a, b) -> float:
    # Vectors are unit-normalised upstream (pipeline/cluster.py contract).
    return sum(x * y for x, y in zip(a, b))


def drop_repeats(
    ctx,
    credibility: Mapping[str, object],
    events: list[Event],
    logger: logging.Logger,
) -> tuple[list[Event], list[Event], dict[str, str]]:
    """Match each new event against PREVIOUS runs' DELIVERED summaries
    (local embedder, zero LLM); collect EVERY prior at/above
    event_match_threshold (fix B -- not just the argmax) since the HIGH
    band's development test ratchets against the high-water mark across
    that set. This run's own rows are excluded by event_key (understand.py
    inserts them first, so an unfiltered read self-matches at sim 1.0).
    Returns (kept, dropped, reasons) -- reasons now covers EVERY matched
    event, survivors included (fix 5). Skipped when db/embedder absent
    (dry-run / mock)."""
    if getattr(ctx, "db", None) is None or getattr(ctx, "embedder", None) is None:
        return events, [], {}
    window = ctx.config.settings.digest_rank.repeat_window_hours
    new_keys = {e.event_key for e in events}
    recent = [
        e for e in read_recent_events(
            ctx.db, hours=window, now=ctx.now, delivered_only=True
        )
        if e.event_key not in new_keys
    ]
    if not recent or not events:
        return events, [], {}
    threshold = ctx.config.settings.pipeline.event_match_threshold
    clusters_by_key = {c.key: c for c in getattr(ctx, "clusters", None) or []}
    new_vectors = ctx.embedder.embed([e.summary for e in events])
    old_vectors = ctx.embedder.embed([e.summary for e in recent])
    kept: list[Event] = []
    dropped: list[Event] = []
    reasons: dict[str, str] = {}
    for event, vector in zip(events, new_vectors):
        matched: list[Event] = []
        best = 0.0
        best_prior_key = ""
        for prior, old_vector in zip(recent, old_vectors):
            sim = _cosine(vector, old_vector)
            if sim >= threshold:
                matched.append(prior)
                if sim > best:
                    best = sim
                    best_prior_key = prior.event_key
        if not matched:
            kept.append(event)
            continue
        bypass, band, reason = _decide(
            ctx, credibility, event, clusters_by_key.get(event.event_key), matched,
            best, best_prior_key,
        )
        reasons[event.event_key] = reason
        if bypass:
            # Band split (round-4 review, fix 1): MID renders as a full
            # normal entry (a different story), HIGH as the compact
            # one-liner (the same story retold) -- see Event.follow_up_high.
            kept.append(replace(event, follow_up=True, follow_up_high=(band == "high")))
            logger.info(
                "validate: %s matched a repeat (sim %.2f) but bypassed "
                "as a follow-up (band=%s) (%s)", event.event_key[:8], best, band, reason,
            )
            continue
        logger.info(
            "validate: %s dropped as a repeat (sim %.2f, %s)",
            event.event_key[:8], best, reason,
        )
        dropped.append(event)
    return kept, dropped, reasons
