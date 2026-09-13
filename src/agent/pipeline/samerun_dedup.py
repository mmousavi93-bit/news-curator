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

SESSION 9r (2026-09-08), fix 1 -- THIS PASS NOW USES THE HIGH BAND.
It previously deleted on `pipeline.event_match_threshold` (0.55). That is
the same defect 9q's two-band gate was built to fix, left live on the one
path 9q did not touch: repeats.py now classifies 0.55 <= sim < 0.80 as a
related but DIFFERENT story that must survive, while this module was still
treating 0.55 as proof of sameness and deleting one side outright -- with
NO score bypass and NO compact «پیگیری» render, so a same-run drop is a
silent, unrecoverable delete. A strike and the retaliation answering it sit
at cosine 0.6-0.65 (repeats.py's measured range) and CAN both land inside
one 3-hour window during an escalation; that is the exact story class this
system exists to surface. The gate is now
`digest_rank.event_repeat_threshold` -- the same constant, one definition of
"the same story" for both passes. No new knob: a two-band same-run gate
would collapse to precisely this, because MID means "different story, keep
both" and there is no cross-run development test to apply within one run.

UNVERIFIED and deliberately measurable: the 2026-08-30 Hormuz pair's actual
cosine was never recorded, so it is not proven to clear 0.80. Every drop
still writes `same_run_dup sim=<f>` into chosen.csv -- if a true double-send
re-appears, its similarity is in the artifact and the constant is one edit.
Do not pre-emptively lower it on a hunch (9q rule: one variable at a time).

SESSION 9r, fix 2 -- NON-TRANSITIVE DELETION. The inner loop did not stop
when `event` itself lost: an already-dropped event kept being compared as
the SOURCE, so A losing to B did not prevent A from then deleting C. A
beats B, B beats C left only A's winner standing even when B and C were
unrelated -- a corpse deleting survivors. 9q's cluster-cap fix roughly
doubled the exposure by letting more same-family clusters reach understand.
The loop now breaks the moment `event` is the loser.

SESSION 9s -- PAIR LOGGING. The drop decision is UNCHANGED; this pass now
also records every pairwise comparison at or above
settings.pipeline.samerun_pair_log_floor into pairs_<ts>.csv, dropped or
not. It exists to settle the open measurement gap (CLAUDE.md pending
item): the tanker cluster fragmented into n=48/12/9/3 and Azraq into
n=59/18/23/4, but within-run pairwise cosines were never recorded, so
"pairs sit at 0.60-0.79 and need a same-story test that is not a single
cosine" vs "MiniLM fails on Persian paraphrase below 0.55" are both live
and need OPPOSITE fixes. log_floor is NOT a tuning variable -- it filters
row count only, and a test pins the surviving set invariant to it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping

from agent.memory.event_models import Event
from agent.pipeline.repeats import _cosine


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
    report_csv.py; pairs feeds pairs_<ts>.csv (session 9s). The DROP
    LOGIC is the 9r logic untouched: logging observes, never reorders,
    re-embeds or re-compares."""
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
            if similarity < threshold:
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
            # Observed, not acted on: the drop itself is the 9r decision.
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
