"""Reconstruct cluster memberships from run-report CSVs — no embeddings needed
for the common case.

`chosen_*.csv` carries `cluster_key` = sha256(sorted member urls)[:16] but NOT
the member urls; `read_*.csv` has the items (url, title, body, published_at,
lang). Recovery strategy (astra session 10H):

1. SINGLETONS (n_members == 1): hash each read url and match the key directly.
   Exact, zero ambiguity — the vast majority of clusters.
2. SMALL clusters (2..MAX_COMBO): bounded combinatorial search constrained by
   the cluster's `sources` list, verifying sha256(sorted(urls))[:16] == key.
3. LARGE clusters (> MAX_COMBO): flag unrecovered — the embedding re-cluster is
   a later CI fallback, never a silent guess.

Outputs a partial dataset.json (`runs` with `clusters` -> recovered item ids) +
a recovery report by n_members, so the coverage gap is explicit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
from collections import defaultdict
from pathlib import Path

MAX_COMBO = 3


def cluster_key(urls: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(urls)).encode("utf-8")).hexdigest()[:16]


def load_items(read_csv: Path) -> dict[str, dict]:
    items: dict[str, dict] = {}
    with open(read_csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            items[row["url"]] = {
                "source_id": row["source_id"], "url": row["url"],
                "title": row.get("title", ""), "body": row.get("body", ""),
                "published_at": row.get("published_at_utc") or None,
                "date_only": row.get("date_only", "") in ("true", "True", "1"),
                "lang": row.get("lang", ""),
            }
    return items


def recover(clusters: list[dict], items: dict[str, dict]) -> dict:
    """Return {cluster_key: [urls]} for recovered clusters, plus unrecovered list."""
    recovered: dict[str, list[str]] = {}
    unrecovered: list[dict] = []
    by_source = defaultdict(list)
    for url, it in items.items():
        by_source[it["source_id"]].append(url)

    for c in clusters:
        key, n, sources = c["cluster_key"], c["n_members"], c["sources"]
        pool = [u for s in sources for u in by_source.get(s, [])]
        # dedupe pool, preserve order
        pool = list(dict.fromkeys(pool))
        if len(pool) < n:
            unrecovered.append({"cluster_key": key, "n": n, "why": "pool too small"})
            continue
        if n == 1:
            hits = [u for u in pool if cluster_key([u]) == key]
            if len(hits) == 1:
                recovered[key] = hits
            else:
                unrecovered.append({"cluster_key": key, "n": n, "why": f"singleton matches {len(hits)}"})
        elif 2 <= n <= MAX_COMBO:
            found = None
            for combo in itertools.combinations(pool, n):
                if cluster_key(list(combo)) == key:
                    found = list(combo)
                    break
            if found is not None:
                recovered[key] = found
            else:
                unrecovered.append({"cluster_key": key, "n": n, "why": "no combo hash match"})
        else:
            unrecovered.append({"cluster_key": key, "n": n, "why": "large cluster, needs CI re-cluster"})
    return recovered, unrecovered


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read", nargs="+", type=Path, required=True)
    parser.add_argument("--chosen", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("analysis/dataset.json"))
    args = parser.parse_args(argv)

    items: dict[str, dict] = {}
    for r in args.read:
        items.update(load_items(r))
    clusters: list[dict] = []
    for c in args.chosen:
        with open(c, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                clusters.append({
                    "cluster_key": row["cluster_key"],
                    "n_members": int(row["n_members"]),
                    "sources": [s for s in (row.get("sources") or "").split("|") if s],
                    "fate": row.get("fate", ""),
                })

    recovered, unrecovered = recover(clusters, items)
    by_n = defaultdict(lambda: [0, 0])
    for c in clusters:
        k = "1" if c["n_members"] == 1 else ("2-3" if c["n_members"] <= MAX_COMBO else f"{c['n_members']}")
        by_n[k][0] += 1
        by_n[k][1] += 1 if c["cluster_key"] in recovered else 0

    report = {
        "items": len(items), "clusters": len(clusters),
        "recovered": len(recovered), "unrecovered": len(unrecovered),
        "recovery_by_n": {k: f"{v[1]}/{v[0]}" for k, v in sorted(by_n.items())},
        "unrecovered": unrecovered,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    out = {"runs": [], "recovered_memberships": recovered, "report": report}
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
