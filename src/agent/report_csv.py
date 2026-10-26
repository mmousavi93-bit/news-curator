"""Offline per-run CSVs: collected inputs, cluster/queue fates, selected
snapshots and run metrics. Queue winners and budget exclusions remain visible.

Legacy `sent`/`sent_followup` fates and run `sent` count describe composition,
NOT delivery. Legacy `deliver` also counts mocked successes (and lead sends).
Use explicit delivery_state, delivered_events and acknowledged_messages for
real receipts. No LLM, clock reads or guessed queue provenance. Reporting
failure must never break a run: run.py wraps the call.
"""

from __future__ import annotations

import csv
from pathlib import Path

from agent.pipeline.rank import event_order_key
from agent.report_csv_chosen import _write_chosen
from agent.report_csv_helpers import _best_tier, _sources, _when_utc
from agent.report_csv_pairs import write_pairs
from agent.report_csv_lifecycle import LIFECYCLE_COLUMNS, lifecycle_columns, pending_keys, receipt_metrics, score_text

_BODY_CAP = 400
_TIMESTAMP_FMT = "%Y%m%dT%H%M%SZ"


def write_run_reports(ctx, out_dir: Path) -> list[Path]:
    """Render the per-run CSVs for one run. Returns the written paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = ctx.now.strftime(_TIMESTAMP_FMT)
    written = [
        _write_read(ctx, out_dir / f"read_{stamp}.csv"),
        _write_chosen(ctx, out_dir / f"chosen_{stamp}.csv"),
        _write_summaries(ctx, out_dir / f"summaries_{stamp}.csv"),
        _write_run(ctx, out_dir / f"run_{stamp}.csv"),
        # Session 9s: same-run pair observations (samerun_dedup.py).
        write_pairs(
            out_dir / f"pairs_{stamp}.csv",
            list(getattr(ctx, "samerun_pairs", None) or []),
        ),
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
    pending = pending_keys(ctx)
    sent_keys = set(getattr(ctx, "compose_kept_keys", None) or [])
    credibility = ctx.config.credibility
    # Production retains the exact selected snapshots, including queue-only
    # winners and budget exclusions. Older callers keep their legacy behavior.
    selected = getattr(ctx, "compose_selected", None)
    if selected is not None:
        events = list(selected)
    else:
        events = [e for e in (getattr(ctx, "events", None) or []) if e.event_key in sent_keys]
        events.sort(key=lambda e: event_order_key(
            e, clusters_by_key, credibility, ctx.config.settings, ctx.now))
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "event_key", "score", "category", "significance",
                         "claim_status", "independent_count", "best_tier", "n_members",
                         "sources", "provider", "latest_utc", "headline", "summary",
                         *LIFECYCLE_COLUMNS])
        providers = getattr(ctx, "event_provider", None) or {}
        for rank, event in enumerate(events):
            cluster = None if event.event_key in pending else clusters_by_key.get(event.event_key)
            writer.writerow([
                rank, event.event_key,
                score_text(ctx, event, cluster),
                event.category, event.significance, event.claim_status,
                event.independent_count,
                _best_tier(cluster, credibility) if cluster else "",
                len(cluster.members) if cluster else "",
                _sources(cluster) if cluster else "",
                "" if event.event_key in pending else providers.get(event.event_key, ""),
                _when_utc(cluster) if cluster else "",
                event.headline, event.summary, *lifecycle_columns(ctx, event.event_key),
            ])
    return path


def _write_run(ctx, path: Path) -> Path:
    counters = getattr(ctx, "counters", None) or {}
    # Per-provider attempt counters (llm/stats.py, owner request
    # 2026-08-30): who carried the run and who failed it, read from the
    # artifact instead of the log. Absent when ctx has no router.
    router = getattr(ctx, "router", None)
    stats = getattr(router, "stats", None)
    # Receipt metrics are separate from the legacy composed/mock counters.
    stat_cols: list[tuple[str, str]] = [(key, str(value)) for key, value in receipt_metrics(ctx)]
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
