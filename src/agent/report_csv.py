"""Per-run observability reports: four CSVs the owner downloads from the
Actions artifacts and reviews (owner request 2026-08-30 -- the measurement
loop that makes digest quality tunable).

  read_<ts>.csv      every item that entered the pipeline (post-dedup):
                     source, url, title, body (truncated), publish time
  chosen_<ts>.csv    one row per CLUSTER with its FATE -- sent, or dropped,
                     and the deterministic reason why (the analysis payload)
  summaries_<ts>.csv every SENT event with its rank, score and the exact
                     headline/summary the LLM produced (reading quality)
  run_<ts>.csv       one row: run metadata + stage counters

The fate capture happens in the stages themselves (understand/validate/
compose record what they dropped and why); this module only renders. No
LLM calls, no clock reads (ctx.now), no secrets -- news text only.
Reporting failure must never break a run: run.py wraps the call.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from agent.pipeline.rank import score_event

_BODY_CAP = 400
_TIMESTAMP_FMT = "%Y%m%dT%H%M%SZ"


def _sources(cluster) -> str:
    return "|".join(sorted({m.source_id for m in cluster.members}))


def _best_tier(cluster, credibility) -> int:
    from agent.pipeline.rank import best_tier
    return best_tier(cluster, credibility)


def _when_utc(cluster) -> str:
    stamps = [m.published_at for m in cluster.members if m.published_at is not None]
    return max(stamps).isoformat() if stamps else ""


def write_run_reports(ctx, out_dir: Path) -> list[Path]:
    """Render the four CSVs for one run. Returns the written paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = ctx.now.strftime(_TIMESTAMP_FMT)
    written = [
        _write_read(ctx, out_dir / f"read_{stamp}.csv"),
        _write_chosen(ctx, out_dir / f"chosen_{stamp}.csv"),
        _write_summaries(ctx, out_dir / f"summaries_{stamp}.csv"),
        _write_run(ctx, out_dir / f"run_{stamp}.csv"),
    ]
    return written


def _write_read(ctx, path: Path) -> Path:
    items = list(getattr(ctx, "items", None) or [])
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_id", "url", "title", "body", "published_at_utc",
                         "date_only", "lang"])
        for item in items:
            body = item.body or ""
            if len(body) > _BODY_CAP:
                body = body[:_BODY_CAP] + "..."
            writer.writerow([
                item.source_id, item.url, item.title, body,
                item.published_at.isoformat() if item.published_at else "",
                int(bool(item.date_only)), item.lang,
            ])
    return path


def _fate_for(cluster_key: str, events_by_key: dict, ctx) -> tuple[str, str]:
    """(fate, reason) for one cluster -- the deterministic story of what
    happened to it this run. Precedence follows the pipeline order."""
    fates = dict(getattr(ctx, "cluster_fates", None) or [])
    if cluster_key in fates:
        return fates[cluster_key], ""
    sent_keys = set(getattr(ctx, "compose_kept_keys", None) or [])
    if cluster_key in sent_keys:
        return "sent", ""
    for fate_attr, fate in (
        ("lang_dropped", "lang_dropped"),
        ("rank_dropped", "rank_dropped"),
        ("repeat_dropped", "repeat_dropped"),
        ("lead_events", "lead_only"),
    ):
        dropped = getattr(ctx, fate_attr, None) or []
        if cluster_key in {e.event_key for e in dropped}:
            return fate, ""
    if cluster_key in events_by_key:
        return "event_unresolved", "event exists but no fate recorded -- anomaly"
    return "no_event", "cluster produced no event and no recorded drop"


def _write_chosen(ctx, path: Path) -> Path:
    clusters = list(getattr(ctx, "clusters", None) or [])
    events_by_key = {e.event_key: e for e in getattr(ctx, "events", None) or []}
    credibility = ctx.config.credibility
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["cluster_key", "fate", "reason", "n_members", "sources",
                         "best_tier", "category", "claim_status",
                         "independent_count", "score"])
        for cluster in clusters:
            event = events_by_key.get(cluster.key)
            fate, reason = _fate_for(cluster.key, events_by_key, ctx)
            score = ""
            if event is not None:
                score = f"{score_event(event, cluster, credibility, ctx.config.settings, ctx.now):.3f}"
            writer.writerow([
                cluster.key, fate, reason, len(cluster.members),
                _sources(cluster), _best_tier(cluster, credibility),
                getattr(event, "category", "") if event else "",
                getattr(event, "claim_status", "") if event else "",
                getattr(event, "independent_count", "") if event else "",
                score,
            ])
    return path


def _write_summaries(ctx, path: Path) -> Path:
    clusters_by_key = {c.key: c for c in getattr(ctx, "clusters", None) or []}
    events = list(getattr(ctx, "events", None) or [])
    sent_keys = set(getattr(ctx, "compose_kept_keys", None) or [])
    credibility = ctx.config.credibility
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "event_key", "score", "category", "claim_status",
                         "independent_count", "best_tier", "n_members", "sources",
                         "latest_utc", "headline", "summary"])
        rank = 0
        for event in events:
            if event.event_key not in sent_keys:
                continue
            cluster = clusters_by_key.get(event.event_key)
            writer.writerow([
                rank, event.event_key,
                f"{score_event(event, cluster, credibility, ctx.config.settings, ctx.now):.3f}"
                if cluster else "",
                event.category, event.claim_status, event.independent_count,
                _best_tier(cluster, credibility) if cluster else "",
                len(cluster.members) if cluster else "",
                _sources(cluster) if cluster else "",
                _when_utc(cluster) if cluster else "",
                event.headline, event.summary,
            ])
            rank += 1
    return path


def _write_run(ctx, path: Path) -> Path:
    counters = getattr(ctx, "counters", None) or {}
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["run_at_utc", "daily_digest", "items", "clusters",
                         "events", "sent", "lang_drops", "rank_dropped",
                         "repeat_dropped", "lead_events", "llm_failed",
                         "messages", "deliver"])
        writer.writerow([
            ctx.now.isoformat(),
            int(bool(getattr(ctx, "daily_digest", False))),
            counters.get("collect", 0),
            counters.get("cluster", 0),
            len(getattr(ctx, "events", None) or []),
            len(getattr(ctx, "compose_kept_keys", None) or []),
            counters.get("compose_lang_drops", 0),
            len(getattr(ctx, "rank_dropped", None) or []),
            len(getattr(ctx, "repeat_dropped", None) or []),
            len(getattr(ctx, "lead_events", None) or []),
            int(bool(getattr(ctx, "llm_failed", False))),
            len(getattr(ctx, "messages", None) or []),
            counters.get("deliver", 0),
        ])
    return path
