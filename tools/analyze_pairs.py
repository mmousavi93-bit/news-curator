"""Analyze a pairs_<ts>.csv into the fragmentation measurement that settles
the same-run dedup tuning decision (D1).

Reads the CSV the pipeline writes (header `run_at_utc,key_a,key_b,similarity,
decision,threshold,n_members_a,n_members_b,independent_count_a,
independent_count_b,headline_a,headline_b,novelty`). Two classifications:

  decision  `dropped_a`/`dropped_b`   sim >= event_repeat_threshold (merged)
            `kept_below_threshold`    floor <= sim < threshold (survived)
  novelty   `fragment`    neither side brings a new number/entity -> same
                          story, should have merged (true fragmentation)
            `development` one side brings a new fact -> legitimately distinct

The D1 signal is the KEPT_BELOW_THRESHOLD + FRAGMENT set: pairs that survived
the gate but tell the same story. Bucketed by similarity, it shows whether a
lower cosine threshold would catch them (fragments clumped near the band top)
or whether cosine alone cannot separate them (fragments scattered -> need the
cross-run two-band/content-novelty logic, not a threshold move).

Usage: python tools/analyze_pairs.py <pairs.csv> [--list-fragments N]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict


def _bucket(sim: float) -> str:
    if sim >= 0.80:
        return "0.80-1.00"
    if sim >= 0.70:
        return "0.70-0.79"
    if sim >= 0.60:
        return "0.60-0.69"
    if sim >= 0.50:
        return "0.50-0.59"
    return "0.40-0.49"


def _headline(row: dict) -> str:
    return (row.get("headline_a") or "").strip()


def analyze(path: str, list_fragments: int) -> None:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        print(f"{path}: empty (no pairs logged this run)")
        return

    has_novelty = "novelty" in rows[0]
    total = len(rows)
    dropped = [r for r in rows if r["decision"] in ("dropped_a", "dropped_b")]
    kept = [r for r in rows if r["decision"] == "kept_below_threshold"]

    print(f"file: {path}")
    print(f"total pairs (sim >= floor): {total}")
    print(f"  dropped  (sim >= threshold, merged): {len(dropped)}")
    print(f"  kept     (below threshold, survived): {len(kept)}")
    if not has_novelty:
        print("WARNING: no 'novelty' column (pre-Session-21 run); "
              "fragment/development split unavailable")
        return

    kept_frag = [r for r in kept if r["novelty"] == "fragment"]
    kept_dev = [r for r in kept if r["novelty"] == "development"]
    print(f"  kept + fragment   (same story, should have merged): {len(kept_frag)}")
    print(f"  kept + development (legitimately distinct):          {len(kept_dev)}")
    print()

    # Bucketed: kept pairs by similarity x novelty.
    bucket = defaultdict(Counter)
    for r in kept:
        bucket[_bucket(float(r["similarity"]))][r["novelty"]] += 1
    print("kept pairs by similarity band (fragment / development):")
    for b in ("0.80-1.00", "0.70-0.79", "0.60-0.69", "0.50-0.59", "0.40-0.49"):
        if b not in bucket:
            continue
        c = bucket[b]
        print(f"  {b}:  fragment={c['fragment']:3}  development={c['development']:3}")

    if kept_frag and list_fragments:
        print(f"\ntop {min(list_fragments, len(kept_frag))} fragment pairs "
              "(same story, survived the gate):")
        for r in sorted(kept_frag, key=lambda r: -float(r["similarity"]))[:list_fragments]:
            print(f"  sim={float(r['similarity']):.3f}  "
                  f"ind={r['independent_count_a']}/{r['independent_count_b']}  "
                  f"a={_headline(r)[:60]!r}")
            hb = (r.get("headline_b") or "").strip()
            print(f"      b={hb[:60]!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--list-fragments", type=int, default=20)
    args = ap.parse_args()
    analyze(args.csv_path, args.list_fragments)
    return 0


if __name__ == "__main__":
    sys.exit(main())
