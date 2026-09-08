"""The two-band repeat-gate DECISION: split out of repeats.py 2026-09-06
(round-3 review, fix 2) when adding the matched-prior event_key to the
reason string pushed repeats.py past the ~200-line cap (constraint 12).
repeats.py owns the ORCHESTRATION (read priors, embed, loop, log); this
module owns the single decision a matched event faces once its similarity
band and score are known. See repeats.py's module docstring for the full
two-band design rationale (round-2 review, fix 1) and the volume check
against the review's measured run.
"""

from __future__ import annotations

from typing import Mapping

from agent.memory.event_models import Event
from agent.pipeline.rank import score_event

# rulebook Step 1 claim-status ordering, low to high.
_CLAIM_RANK = {"rumour": 0, "unconfirmed": 1, "likely": 2}


def _claim_rank(status: str) -> int:
    return _CLAIM_RANK.get(status, 0)


def _high_water(matched: list[Event]) -> tuple[int, int]:
    """Max independent_count/claim rank across EVERY matched prior (fix B),
    not just the closest one -- else the same follow-up could re-satisfy
    "development" against a weak prior while a stronger one had already
    delivered more. HIGH band only -- see _decide."""
    if not matched:
        return 0, 0
    return (
        max(p.independent_count for p in matched),
        max(_claim_rank(p.claim_status) for p in matched),
    )


def _decide(
    ctx, credibility: Mapping[str, object], event: Event, cluster,
    matched: list[Event], best_sim: float, best_prior_key: str,
) -> tuple[bool, str, str]:
    """Two-band repeat gate (round-2 review, fix 1). `best_sim` is the
    MAXIMUM cosine across every matched prior -- that decides the band.
    `best_prior_key` is THAT prior's event_key (round-3 review, fix 2):
    without it, `band=/sim=/score=` tells the owner a repeat fired but not
    WHICH prior it matched, so the 0.80 tuning loop had no way to pull up
    the actual pair and judge whether the gate called it right. Truncated
    to 8 chars -- same convention as repeats.py's log lines, still unique
    enough to grep chosen.csv/events for the specific prior row.
    Returns (bypass, band, reason); `band` is "high" or "mid" regardless of
    outcome -- round-4 review, fix 1: repeats.py needs the band on every
    bypass to decide full-entry vs compact-line rendering, not just the
    reason string's `band=` substring. `reason` is machine-greppable on
    BOTH outcomes (fix 5): `band=<high|mid> sim=<f> score=<f> kept=<term>
    prior=<key>` or `blocked=<term> prior=<key>`."""
    settings = ctx.config.settings
    cfg = settings.digest_rank
    score = (
        score_event(event, cluster, credibility, settings, ctx.now)
        if cluster is not None else None
    )
    floor = cfg.repeat_bypass_score
    meets_floor = score is not None and score >= floor
    score_str = f"{score:.2f}" if score is not None else "n/a"
    high_band = best_sim >= cfg.event_repeat_threshold
    band = "high" if high_band else "mid"
    prior_tag = best_prior_key[:8]

    if not high_band:
        # MID band: a related but DIFFERENT story -- no development test,
        # the ratchet has nothing valid to compare against.
        term = "above_floor" if meets_floor else "below_floor"
        prefix = "kept" if meets_floor else "blocked"
        reason = (
            f"band={band} sim={best_sim:.2f} score={score_str} "
            f"{prefix}={term} prior={prior_tag}"
        )
        return meets_floor, band, reason

    # HIGH band: genuinely the same story -- the conjunction (owner
    # decision, fix A) still applies: score floor AND development over the
    # high-water mark across every matched prior (fix B).
    hw_independent, hw_claim = _high_water(matched)
    developed = (
        event.independent_count > hw_independent
        or _claim_rank(event.claim_status) > hw_claim
    )
    bypass = meets_floor and developed
    if bypass:
        return True, band, (
            f"band={band} sim={best_sim:.2f} score={score_str} "
            f"kept=development prior={prior_tag}"
        )
    if not meets_floor and not developed:
        blocked = "both"
    elif not meets_floor:
        blocked = "below_floor"
    else:
        blocked = "no_development"
    return False, band, (
        f"band={band} sim={best_sim:.2f} score={score_str} "
        f"blocked={blocked} prior={prior_tag}"
    )
