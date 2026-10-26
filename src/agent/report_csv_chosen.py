"""Chosen clusters and queue-only snapshots with composition/receipt states.

Legacy fate `sent` means composed, NOT a delivery receipt. Only the explicit
`delivery_state` column establishes an acknowledged real send.
"""
from __future__ import annotations

import csv
from pathlib import Path

from agent.pipeline.priority import is_on_mission
from agent.report_csv_helpers import _best_tier, _sources
from agent.report_csv_lifecycle import LIFECYCLE_COLUMNS, event_map, lifecycle_columns, pending_keys, score_text


def _fate_for(cluster_key: str, events_by_key: dict, ctx) -> tuple[str, str]:
    """Stage fates stay backward-compatible; receipts are separate columns."""
    fates = dict(getattr(ctx, "cluster_fates", None) or [])
    queued = {e.event_key for attr in ("queue_selected", "queue_deferred", "queue_expired")
              for e in getattr(ctx, attr, None) or []}
    if cluster_key in fates and cluster_key not in queued:
        reasons = getattr(ctx, "fate_reasons", None) or {}
        return fates[cluster_key], reasons.get(cluster_key, "")
    sent_keys = set(getattr(ctx, "compose_kept_keys", None) or [])
    if cluster_key in sent_keys:
        reasons = getattr(ctx, "repeat_drop_reasons", None) or {}
        reason = reasons.get(cluster_key, "")
        event = events_by_key.get(cluster_key)
        if event is not None and getattr(event, "follow_up", False):
            return "sent_followup", reason
        return "sent", reason
    truncated = {e.event_key for e in getattr(ctx, "compose_truncated", None) or []}
    if cluster_key in truncated:
        return "truncated", "cut by the character budget; not marked delivered"
    for attr, fate, reason in (
        ("queue_deferred", "deferred", "eligible; outside this run's selection cap; retained in queue"),
        ("queue_expired", "expired", "first-admission queue lifetime elapsed; not delivered"),
    ):
        if cluster_key in {e.event_key for e in getattr(ctx, attr, None) or []}:
            return fate, reason
    for fate_attr, fate in (
        ("lang_dropped", "lang_dropped"), ("rank_dropped", "rank_dropped"),
        ("relevance_dropped", "relevance_dropped"), ("repeat_dropped", "repeat_dropped"),
        ("lead_events", "lead_only"),
    ):
        dropped = getattr(ctx, fate_attr, None) or []
        if cluster_key in {e.event_key for e in dropped}:
            if fate in ("repeat_dropped", "rank_dropped", "relevance_dropped"):
                reasons = getattr(ctx, "repeat_drop_reasons", None) or {}
                return fate, reasons.get(cluster_key, "")
            return fate, ""
    if cluster_key in events_by_key:
        return "event_unresolved", "event exists but no fate recorded -- anomaly"
    return "no_event", "cluster produced no event and no recorded drop"


def _write_chosen(ctx, path: Path) -> Path:
    clusters = list(getattr(ctx, "clusters", None) or [])
    events_by_key = event_map(ctx)
    pending = pending_keys(ctx)
    credibility = ctx.config.credibility
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["cluster_key", "fate", "reason", "n_members", "sources",
                         "provider", "best_tier", "category", "significance",
                         "claim_status", "independent_count", "score",
                         "prellm_score", "on_mission", "headline", "summary", *LIFECYCLE_COLUMNS])
        providers = getattr(ctx, "cluster_provider", None) or {}
        prellm_scores = getattr(ctx, "prellm_scores", None) or {}
        written_keys = set()
        for cluster in clusters:
            if cluster.key in pending:
                continue  # Write the queue snapshot below, without current-run provenance.
            written_keys.add(cluster.key)
            event = events_by_key.get(cluster.key)
            fate, reason = _fate_for(cluster.key, events_by_key, ctx)
            raw_prellm = prellm_scores.get(cluster.key)
            prellm_score = f"{raw_prellm:.4f}" if raw_prellm is not None else ""
            on_mission = 1 if is_on_mission(cluster, ctx.config) else 0
            writer.writerow([
                cluster.key, fate, reason, len(cluster.members),
                _sources(cluster), providers.get(cluster.key, ""),
                _best_tier(cluster, credibility),
                getattr(event, "category", "") if event else "",
                getattr(event, "significance", "") if event else "",
                getattr(event, "claim_status", "") if event else "",
                getattr(event, "independent_count", "") if event else "",
                score_text(ctx, event, cluster), prellm_score, on_mission,
                getattr(event, "headline", "") if event else "",
                getattr(event, "summary", "") if event else "",
                *lifecycle_columns(ctx, cluster.key),
            ])
        # Cap/embedding losses retain raw source text and the actual priority
        # diagnostics; these are not fabricated Event summaries.
        for cluster in getattr(ctx, "clusters_cap_dropped", None) or []:
            if cluster.key in pending:
                continue
            written_keys.add(cluster.key)
            title = (cluster.members[0].title or cluster.members[0].body or "").strip()
            on_mission = 1 if is_on_mission(cluster, ctx.config) else 0
            raw_prellm = prellm_scores.get(cluster.key)
            prellm_score = f"{raw_prellm:.4f}" if raw_prellm is not None else ""
            corroborating = cluster.corroborating_count(credibility)
            reason = (f"over max_clusters_per_run; no LLM call. "
                      f"on_mission={on_mission} corroborating_count={corroborating}")
            writer.writerow([
                cluster.key, "cap_dropped", reason,
                len(cluster.members), _sources(cluster), "", _best_tier(cluster, credibility),
                "", "", "", cluster.independent_count(credibility), "",
                prellm_score, on_mission, title[:300], "", *lifecycle_columns(ctx, cluster.key),
            ])
        for cluster in getattr(ctx, "clusters_prellm_dropped", None) or []:
            if cluster.key in pending:
                continue
            written_keys.add(cluster.key)
            title = (cluster.members[0].title or cluster.members[0].body or "").strip()
            raw_prellm = prellm_scores.get(cluster.key)
            prellm_score = f"{raw_prellm:.4f}" if raw_prellm is not None else ""
            reason = f"below prellm_drop_threshold; no LLM call. prellm_score={prellm_score}"
            writer.writerow([
                cluster.key, "prellm_dropped", reason,
                len(cluster.members), _sources(cluster), "", _best_tier(cluster, credibility),
                "", "", "", cluster.independent_count(credibility), "",
                prellm_score, 0, title[:300], "", *lifecycle_columns(ctx, cluster.key),
            ])
        # Queue snapshots can outlive their source run's clusters/providers.
        # Unknown provenance remains empty, never guessed from current feeds.
        queue_keys = {e.event_key for attr in ("queue_selected", "queue_deferred", "queue_expired")
                      for e in getattr(ctx, attr, None) or []}
        for key in sorted(queue_keys - written_keys):
            event = events_by_key[key]
            fate, reason = _fate_for(key, events_by_key, ctx)
            writer.writerow([
                key, fate, reason, "", "", "", "", event.category, event.significance,
                event.claim_status, event.independent_count, score_text(ctx, event, None),
                "", "", event.headline, event.summary, *lifecycle_columns(ctx, key),
            ])
    return path
