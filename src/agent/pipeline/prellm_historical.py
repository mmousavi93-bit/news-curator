"""Rebuild a past run's clusters from its read.csv and re-score them.

The calibration loop's faithfulness argument, in one place: a run's
`read_*.csv` carries every post-dedup item (title + truncated body + url +
published_at), and `chosen_*.csv` carries every cluster's deterministic key
(sha256 of sorted member urls) plus the fate the pipeline recorded for it.
Clustering is deterministic on (items, embeddings, threshold) -- see
cluster.py -- so re-embedding the read.csv items and re-clustering them
reproduces the SAME cluster keys as the original run. Joining the rebuilt
cluster to its historical fate gives a `prellm_score` for every cluster the
original run judged, which the analyzer (prellm_calibration.py) turns into a
threshold.

Known limits, reported rather than hidden:
  * read.csv truncates `body` to 400 chars, so rebuilt embeddings differ
    slightly from the originals and a few clusters split/merge. Those lose
    their key match and show up as `fate == ""` (unmatched) in the export.
  * `on_mission` is NOT re-derived here -- it is injected by the caller (the
    CLI passes the real `is_on_mission` over a fully-loaded Config), so this
    module stays free of config/embedding dependencies and is testable with
    the FakeEmbedder.

No LLM, no network, no torch in this module. `embedder` is a parameter.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Callable

from agent.collectors.base import Item, hash_raw
from agent.pipeline.cluster import cluster_items
from agent.pipeline.prerelevance import DEFAULT_ANCHOR_TEXTS, cosine, relevance_score


def _parse_dt(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def items_from_read(path: Path) -> list[Item]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    items = []
    for record in records:
        url = (record.get("url") or "").strip()
        items.append(Item(
            source_id=(record.get("source_id") or "").strip(),
            url=url,
            title=(record.get("title") or "").strip(),
            body=(record.get("body") or "").strip(),
            published_at=_parse_dt(record.get("published_at_utc") or ""),
            lang=(record.get("lang") or "").strip(),
            raw_hash=hash_raw(url.encode("utf-8")),
            date_only=bool(int((record.get("date_only") or "0").strip() or 0)),
        ))
    return items


def fates_from_chosen(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    fates = {}
    for record in records:
        key = (record.get("cluster_key") or "").strip()
        if key:
            fates[key] = (record.get("fate") or "").strip()
    return fates


def score_run(
    reports_dir: str | Path,
    embedder,
    *,
    threshold: float,
    on_mission: Callable[[object], bool],
) -> tuple[list[dict], dict]:
    """(rows, stats). Rows carry the analyzer's expected columns plus
    cluster_key for the fate join. `on_mission` is injected by the caller."""
    root = Path(reports_dir)
    read_paths = sorted(root.rglob("read_*.csv"))
    chosen_paths = sorted(root.rglob("chosen_*.csv"))
    if not read_paths or not chosen_paths:
        return [], {"run": str(root), "error": f"read={len(read_paths)} chosen={len(chosen_paths)}"}

    items = items_from_read(read_paths[0])
    fates = fates_from_chosen(chosen_paths[0])
    vectors = embedder.embed([f"{item.title}\n{item.body}" for item in items])
    clusters = cluster_items(items, vectors, threshold)
    anchors = [v for v in embedder.embed(list(DEFAULT_ANCHOR_TEXTS)) if v]

    rows = []
    for cluster in clusters:
        score = relevance_score(cluster, anchors, cosine)
        rows.append({
            "cluster_key": cluster.key,
            "fate": fates.get(cluster.key, ""),
            "prellm_score": f"{score:.4f}" if score is not None else "",
            "on_mission": 1 if on_mission(cluster) else 0,
            "n_members": len(cluster.members),
            "headline": (cluster.members[0].title or cluster.members[0].body or "").strip()[:300],
        })

    matched = sum(1 for row in rows if row["fate"])
    stats = {
        "run": read_paths[0].name,
        "items": len(items),
        "clusters": len(clusters),
        "matched": matched,
        "unmatched": len(rows) - matched,
        "chosen_rows": len(fates),
    }
    return rows, stats
