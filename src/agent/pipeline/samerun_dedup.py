"""Same-run duplicate collapse: split out of repeats.py 2026-09-06 (round-2
review) when that file crossed the ~200-line cap (constraint 12).

Two events from the SAME run telling the same story must not both reach the
digest (2026-08-30 Hormuz-tanker double-send -- pipeline/repeats.py's
drop_repeats only compares against PREVIOUS runs' delivered summaries, so it
never catches a same-run collision). Pairwise cosine over the LLM-written
summaries, zero LLM calls; the survivor is chosen on (independent_count,
member count), NEVER member count alone (round-2 review, fix 2): three
reposting tier-3/lead channels must not outnumber and delete a single
corroborated tier-1/2 report of the same story.

SESSION 9r (2026-09-08), fix 1 -- THE GATE IS THE HIGH BAND. It previously
deleted on `pipeline.event_match_threshold` (0.55) -- the exact defect 9q's
two-band gate fixed, left live on the one path 9q did not touch: repeats.py
calls 0.55 <= sim < 0.80 a related but DIFFERENT story that must survive, while
this module still treated 0.55 as proof of sameness and deleted one side -- no
score bypass, no compact «پیگیری» render, so the delete was silent and
unrecoverable. A strike and the retaliation answering it sit at cosine 0.6-0.65
and CAN both land inside one 3-hour window during an escalation; that is the
exact story class this system exists to surface. The gate is now
`digest_rank.event_repeat_threshold`, one definition of "the same story" for
both passes and no new knob: within one run MID means "different story, keep
both" and there is no cross-run development test to apply.

UNVERIFIED: the 2026-08-30 Hormuz pair's cosine was never recorded; every drop
writes `same_run_dup sim=<f>` into chosen.csv, so a re-appearing double-send is
measurable from the artifact and the constant is one edit (9q rule: one variable
at a time).

SESSION 22 -- NOVELTY IS ACTED ON, not just logged: a fragment pair (no new
number/entity either side) merges to FRAGMENT_FLOOR, a development pair still
needs the 0.80 band (measured: run 35866102212 Hormuz tanker 0.7658 and Trump-Xi
0.7226, both fragment, both double-sent under a novelty-blind 0.80).

SESSION 9r, fix 2 -- NON-TRANSITIVE DELETION. The inner loop did not stop
when `event` itself lost: an already-dropped event kept being compared as
the SOURCE, so A losing to B did not prevent A from then deleting C. A
beats B, B beats C left only A's winner standing even when B and C were
unrelated -- a corpse deleting survivors. 9q's cluster-cap fix roughly
doubled the exposure by letting more same-family clusters reach understand.
The loop now breaks the moment `event` is the loser.

SESSION 9s -- PAIR LOGGING. Every pairwise comparison at or above
settings.pipeline.samerun_pair_log_floor is recorded into pairs_<ts>.csv,
dropped or not -- it settles the open gap (CLAUDE.md): the tanker cluster
fragmented n=48/12/9/3 and Azraq n=59/18/23/4 with no within-run cosines
recorded, so "pairs sit at 0.60-0.79" vs "MiniLM fails on Persian paraphrase
below 0.55" needed OPPOSITE fixes. log_floor filters row count only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping

from agent.memory.event_models import Event
from agent.pipeline.repeat_decision import _content_novelty
from agent.pipeline.repeats import _cosine

# Session 22: fragment pairs merge from 0.70, NOT lower. Same-story fragments
# sit ABOVE 0.70 (brief-supplied pairs.csv run 35866102212: Hormuz tanker
# sim=0.7658, Trump-Xi sim=0.7226, both fragment, both double-sent). Below it
# sit the different-story class: the strike/retaliation pair (9r, 0.6-0.65, no
# new fact either side) and the B/C different-story fixture
# cos([0.82,0.572364,0],[0.82,0,0.572364])=0.6724 from
# tests/unit/test_pipeline_validate.py:487 -- _content_novelty mislabels those
# "fragment", so 0.60 silently deleted the exact story class this system exists
# to surface. Floor is evidence-bounded (lowest measured fragment 0.7226,
# highest non-merge 0.6724), not a hunch (9q one-variable rule).
FRAGMENT_FLOOR = 0.70


@dataclass(frozen=True, slots=True)
class PairRecord:
    """One pairwise same-run comparison worth keeping. Fields carry the
    pair's TEXT (Masafer Yatta lesson, 9p item 5: a new fate must carry
    its text) so the 9t clusterer decision can be made from the CSV alone.
    The brief sketched score_a/score_b -- deviation, documented: events
    carry NO rank score at validate time (rank_events runs in compose),
    so independent_count (the corroboration this pass actually ranks on)
    stands in."""

    run_at_utc: str
    key_a: str
    key_b: str
    similarity: float
    decision: str  # dropped_b | dropped_a | kept_below_threshold
    threshold: float
    n_members_a: int
    n_members_b: int
    independent_count_a: int
    independent_count_b: int
    headline_a: str
    headline_b: str
    # Session 21: does either side bring a NEW fact (number/entity) the
    # other lacks? "development" = legitimate separate story; "fragment"
    # = same story, should have merged (the fragmentation signal D1 needs).
    novelty: str


def _rank_key(event: Event, clusters_by_key: Mapping[str, object]) -> tuple[int, int]:
    """(independent_count, member count) -- corroboration ranked ahead of
    raw size (round-2 review, fix 2): a pile of tier-3/lead reposts must
    never outrank a single-member tier-1/2 cluster just by outnumbering
    it."""
    cluster = clusters_by_key.get(event.event_key)
    size = len(cluster.members) if cluster is not None else 0
    return (event.independent_count, size)


def drop_same_run_dups(
    ctx, events: list[Event], logger: logging.Logger, *, log_floor: float | None = None
) -> tuple[list[Event], list[Event], dict[str, str], list[PairRecord]]:
    """Survivor is the one with the higher (independent_count, member
    count), ties keep the first. Returns (kept, dropped, reasons, pairs)
    -- reasons is machine-greppable (`same_run_dup sim=<f>`) for
    report_csv.py; pairs feeds pairs_<ts>.csv (session 9s). Drop is the 9r
    band OR a fragment pair at FRAGMENT_FLOOR (session 22); logging never
    reorders or re-compares."""
    if getattr(ctx, "embedder", None) is None or len(events) < 2:
        return events, [], {}, []
    # HIGH band only (9r fix 1) -- see the module docstring. settings.py's
    # cross-section check already refuses a config where this is below
    # pipeline.event_match_threshold, so it can never widen past the
    # cross-run gate's floor.
    threshold = ctx.config.settings.digest_rank.event_repeat_threshold
    floor = (
        ctx.config.settings.pipeline.samerun_pair_log_floor
        if log_floor is None else log_floor
    )
    vectors = ctx.embedder.embed([e.summary for e in events])
    clusters_by_key = {c.key: c for c in getattr(ctx, "clusters", None) or []}
    run_at = ctx.now.isoformat()

    def _size(key: str) -> int:
        cluster = clusters_by_key.get(key)
        return len(cluster.members) if cluster is not None else 0

    def _record(a: Event, b: Event, similarity: float, decision: str) -> PairRecord:
        dev = _content_novelty(a, [b]) or _content_novelty(b, [a])
        return PairRecord(
            run_at_utc=run_at,
            key_a=a.event_key, key_b=b.event_key,
            similarity=round(similarity, 4),
            decision=decision,
            threshold=threshold,
            n_members_a=_size(a.event_key), n_members_b=_size(b.event_key),
            independent_count_a=a.independent_count,
            independent_count_b=b.independent_count,
            headline_a=a.headline or a.summary,
            headline_b=b.headline or b.summary,
            novelty="development" if dev else "fragment",
        )

    dropped_keys: set[str] = set()
    reasons: dict[str, str] = {}
    pairs: list[PairRecord] = []
    for i, event in enumerate(events):
        if event.event_key in dropped_keys:
            continue
        for j in range(i + 1, len(events)):
            other = events[j]
            if other.event_key in dropped_keys:
                continue
            similarity = _cosine(vectors[i], vectors[j])
            # Session 22: novelty is computed BEFORE the drop decision, so a
            # fragment pair merges from FRAGMENT_FLOOR; a development pair
            # still needs the 0.80 band.
            dev = _content_novelty(event, [other]) or _content_novelty(other, [event])
            if similarity < threshold and (dev or similarity < FRAGMENT_FLOOR):
                if similarity >= floor:
                    pairs.append(_record(event, other, similarity,
                                         "kept_below_threshold"))
                continue
            loser = (
                event
                if _rank_key(event, clusters_by_key) < _rank_key(other, clusters_by_key)
                else other
            )
            dropped_keys.add(loser.event_key)
            reasons[loser.event_key] = f"same_run_dup sim={similarity:.2f}"
            # Acted on since session 22: the `dev` computed above is what
            # selected the band this drop came from.
            pairs.append(_record(
                event, other, similarity,
                "dropped_a" if loser is event else "dropped_b",
            ))
            logger.info(
                "validate: %s dropped as same-run duplicate of a more "
                "corroborated/larger cluster (sim %.2f)",
                loser.event_key[:8], similarity,
            )
            if loser is event:
                # 9r fix 2: `event` is dead -- it must not go on deleting
                # later events. Without this break the pass is
                # non-transitive (A loses to B, then still deletes C).
                break
    return (
        [e for e in events if e.event_key not in dropped_keys],
        [e for e in events if e.event_key in dropped_keys],
        reasons,
        pairs,
    )
