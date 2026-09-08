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

chosen.csv's row-building (_fate_for, _write_chosen) lives in
report_csv_chosen.py -- split out 2026-09-06 (round-2 review, fix 5) when
this file crossed the ~200-line cap; the three tiny per-cluster field
helpers both modules need live in report_csv_helpers.py to avoid an import
cycle between the two writer modules.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from agent.pipeline.rank import event_order_key, score_event
from agent.report_csv_chosen import _write_chosen
from agent.report_csv_helpers import _best_tier, _sources, _when_utc

_BODY_CAP = 400
_TIMESTAMP_FMT = "%Y%m%dT%H%M%SZ"


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


def _write_summaries(ctx, path: Path) -> Path:
    clusters_by_key = {c.key: c for c in getattr(ctx, "clusters", None) or []}
    sent_keys = set(getattr(ctx, "compose_kept_keys", None) or [])
    credibility = ctx.config.credibility
    events = [e for e in (getattr(ctx, "events", None) or []) if e.event_key in sent_keys]
    # Same order the reader sees: the digest's sort key, so rank 0 is the
    # first item of the message, not the first event created.
    events.sort(key=lambda e: event_order_key(
        e, clusters_by_key, credibility, ctx.config.settings, ctx.now))
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "event_key", "score", "category", "claim_status",
                         "independent_count", "best_tier", "n_members", "sources",
                         "provider", "latest_utc", "headline", "summary"])
        providers = getattr(ctx, "event_provider", None) or {}
        for rank, event in enumerate(events):
            cluster = clusters_by_key.get(event.event_key)
            writer.writerow([
                rank, event.event_key,
                f"{score_event(event, cluster, credibility, ctx.config.settings, ctx.now):.3f}"
                if cluster else "",
                event.category, event.claim_status, event.independent_count,
                _best_tier(cluster, credibility) if cluster else "",
                len(cluster.members) if cluster else "",
                _sources(cluster) if cluster else "",
                providers.get(event.event_key, ""),
                _when_utc(cluster) if cluster else "",
                event.headline, event.summary,
            ])
    return path


def _write_run(ctx, path: Path) -> Path:
    counters = getattr(ctx, "counters", None) or {}
    # Per-provider attempt counters (llm/stats.py, owner request
    # 2026-08-30): who carried the run and who failed it, read from the
    # artifact instead of the log. Absent when ctx has no router.
    router = getattr(ctx, "router", None)
    stats = getattr(router, "stats", None)
    stat_cols: list[tuple[str, str]] = []
    if stats is not None:
        for name, entry in sorted(stats.as_dict().items()):
            stat_cols.append((f"calls_{name}", str(entry["calls"])))
            stat_cols.append((f"fails_{name}", str(entry["failed"])))
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["run_at_utc", "daily_digest", "items", "clusters",
                         "events", "sent", "lang_drops", "rank_dropped",
                         "repeat_dropped", "lead_events", "llm_failed",
                         "messages", "deliver",
                         *[h for h, _ in stat_cols]])
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
            *[v for _, v in stat_cols],
        ])
    return path
