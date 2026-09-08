"""chosen.csv: one row per CLUSTER with its FATE and the deterministic
reason why -- split out of report_csv.py 2026-09-06 (round-2 review, fix 5)
when that file crossed the ~200-line cap (constraint 12). Same no-LLM/
no-clock-read contract as the parent module; report_csv.write_run_reports
calls _write_chosen from here.
"""

from __future__ import annotations

import csv
from pathlib import Path

from agent.pipeline.priority import _cluster_text
from agent.pipeline.rank import score_event
from agent.pipeline.relevance import score_relevance
from agent.report_csv_helpers import _best_tier, _sources


def _fate_for(cluster_key: str, events_by_key: dict, ctx) -> tuple[str, str]:
    """(fate, reason) for one cluster -- the deterministic story of what
    happened to it this run. Precedence follows the pipeline order."""
    fates = dict(getattr(ctx, "cluster_fates", None) or [])
    if cluster_key in fates:
        return fates[cluster_key], ""
    sent_keys = set(getattr(ctx, "compose_kept_keys", None) or [])
    if cluster_key in sent_keys:
        # Fix 1, 2026-09-06: a bypassed repeat renders as a compact
        # follow-up line (compose.py), not a full entry -- distinguish it
        # in the audit trail from a normal "sent" so the CSV can tell the
        # two apart without re-deriving it from priority.py's output.
        # Fix 5, round-2 review: a SURVIVING event that matched the repeat
        # gate carries the same band/sim/score reason a dropped one does
        # (pipeline/repeats.py's `reasons` dict is populated for every
        # matched event, bypassed or not) -- the owner can only calibrate
        # event_repeat_threshold/repeat_bypass_score from BOTH sides of
        # the gate, not just the drops.
        reasons = getattr(ctx, "repeat_drop_reasons", None) or {}
        reason = reasons.get(cluster_key, "")
        event = events_by_key.get(cluster_key)
        if event is not None and getattr(event, "follow_up", False):
            return "sent_followup", reason
        return "sent", reason
    # Fix 3/5, round-2 review: an event that survived every gate but was cut
    # by the character budget (compose.py's format_split_tracked) is
    # neither "sent" (compose_kept_keys excludes it on purpose -- it never
    # rendered) nor one of the earlier stages' drops -- its own fate, so a
    # truncated follow-up doesn't masquerade as a normal drop or a silent
    # "event_unresolved" anomaly.
    truncated = {e.event_key for e in getattr(ctx, "compose_truncated", None) or []}
    if cluster_key in truncated:
        return "truncated", "cut by the character budget; not marked delivered"
    for fate_attr, fate in (
        ("lang_dropped", "lang_dropped"),
        ("rank_dropped", "rank_dropped"),
        ("relevance_dropped", "relevance_dropped"),
        ("repeat_dropped", "repeat_dropped"),
        ("lead_events", "lead_only"),
    ):
        dropped = getattr(ctx, fate_attr, None) or []
        if cluster_key in {e.event_key for e in dropped}:
            if fate in ("repeat_dropped", "rank_dropped", "relevance_dropped"):
                # Fix E, 2026-09-06 review, extended round-3 review fix 4:
                # a MID-band repeat survivor carries a band=/sim=/score=/
                # prior= reason in ctx.repeat_drop_reasons (validate.py) --
                # if compose.py later cuts that SAME event on rank or
                # relevance, the old code threw that context away and
                # wrote reason="". Falling back to the same dict for all
                # three fates means the repeat-gate reason (if any)
                # survives however the event finally left the pipeline.
                reasons = getattr(ctx, "repeat_drop_reasons", None) or {}
                return fate, reasons.get(cluster_key, "")
            return fate, ""
    if cluster_key in events_by_key:
        return "event_unresolved", "event exists but no fate recorded -- anomaly"
    return "no_event", "cluster produced no event and no recorded drop"


def _write_chosen(ctx, path: Path) -> Path:
    clusters = list(getattr(ctx, "clusters", None) or [])
    # ctx.events is the SURVIVING set -- validate physically removes repeats
    # from it, so a repeat_dropped row used to be written with empty
    # headline/summary and the drop was unjudgeable (2026-09-05: seven
    # events dropped at cosine 0.56-0.67, one of them the run's largest
    # cluster, and there was no text to tell over-cut from correct). Fold
    # the dropped sets back in for TEXT only; _fate_for still decides fate.
    events_by_key = {e.event_key: e for e in getattr(ctx, "events", None) or []}
    for attr in ("repeat_dropped", "lang_dropped", "rank_dropped",
                 "relevance_dropped", "lead_events", "compose_truncated"):
        for event in getattr(ctx, attr, None) or []:
            events_by_key.setdefault(event.event_key, event)
    credibility = ctx.config.credibility
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["cluster_key", "fate", "reason", "n_members", "sources",
                         "provider", "best_tier", "category", "claim_status",
                         "independent_count", "score", "headline", "summary"])
        providers = getattr(ctx, "cluster_provider", None) or {}
        for cluster in clusters:
            event = events_by_key.get(cluster.key)
            fate, reason = _fate_for(cluster.key, events_by_key, ctx)
            score = ""
            if event is not None:
                score = f"{score_event(event, cluster, credibility, ctx.config.settings, ctx.now):.3f}"
            writer.writerow([
                cluster.key, fate, reason, len(cluster.members),
                _sources(cluster), providers.get(cluster.key, ""),
                _best_tier(cluster, credibility),
                getattr(event, "category", "") if event else "",
                getattr(event, "claim_status", "") if event else "",
                getattr(event, "independent_count", "") if event else "",
                score,
                # Gate forensics need the text (the Masafer Yatta lesson:
                # a drop is unjudgeable without the words the gate saw).
                # Empty for fates recorded before an event existed.
                getattr(event, "headline", "") if event else "",
                getattr(event, "summary", "") if event else "",
            ])
        # Clusters the cap cut before any LLM saw them. They are not in
        # ctx.clusters, so without this block they left only hex keys in one
        # log line -- 36 of them on 2026-09-05, unauditable. The raw source
        # title goes in the headline column on purpose: `grep` over this one
        # file is the audit, and it has to reach cap losses too.
        multipliers = ctx.config.settings.scoring.tier_multipliers
        relevance = ctx.config.relevance
        for cluster in getattr(ctx, "clusters_cap_dropped", None) or []:
            title = (cluster.members[0].title or cluster.members[0].body or "").strip()
            # Fix 5, round-4 review: priority.py's actual cap sort key is
            # (on_mission, tier_weight, corroborating_count, recency, size)
            # -- NOT the independent_count column below, which is a
            # different, wider count (every tier, leads excluded) kept for
            # its own stable meaning (independent_count docstring,
            # cluster.py). Without on_mission/corroborating_count in the
            # reason, a cap_dropped row could not be checked against the
            # sort that actually produced it -- the owner would have to
            # re-derive both from read.csv by hand.
            tier_weight = cluster.max_tier_weight(credibility, multipliers)
            on_mission = 1 if (
                tier_weight > 0
                and score_relevance(relevance, _cluster_text(cluster)) > 0
            ) else 0
            corroborating = cluster.corroborating_count(credibility)
            reason = (
                f"over max_clusters_per_run; no LLM call. "
                f"on_mission={on_mission} corroborating_count={corroborating}"
            )
            writer.writerow([
                cluster.key, "cap_dropped", reason,
                len(cluster.members), _sources(cluster), "",
                _best_tier(cluster, credibility),
                "", "", cluster.independent_count(credibility), "",
                title[:300], "",
            ])
    return path
