"""CLI: re-score every run under a runs dir and emit one combined CSV.

    python tools/score_historical_prellm.py calibration/runs --out calibration/calibration_rows.csv

Walks each subdir (one per downloaded run-reports artifact), rebuilds the
clusters from read_*.csv, scores them with the REAL MiniLM embedder against
the mission anchors, joins the historical fates by cluster key, and writes
one CSV the analyzer (tools/calibrate_prellm_threshold.py) reads. CI-only in
practice (MiniLM needs torch), but the scoring core is offline-testable.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.config import load_all  # noqa: E402
from agent.pipeline.embed import MiniLmEmbedder  # noqa: E402
from agent.pipeline.prellm_historical import score_run  # noqa: E402
from agent.pipeline.priority import is_on_mission  # noqa: E402

_COLUMNS = ["run_at_utc", "cluster_key", "fate", "prellm_score",
            "on_mission", "n_members", "headline"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs_dir", help="dir of per-run subdirs (each with reports/)")
    parser.add_argument("--out", default="calibration_rows.csv")
    parser.add_argument("--model", default=None, help="override embed_model")
    parser.add_argument("--threshold", type=float, default=None,
                        help="override cluster_similarity_threshold")
    args = parser.parse_args(argv)

    config = load_all()
    pipeline = config.settings.pipeline
    embedder = MiniLmEmbedder(args.model or pipeline.embed_model)
    threshold = args.threshold if args.threshold is not None else pipeline.cluster_similarity_threshold

    runs_dir = Path(args.runs_dir)
    run_dirs = sorted(d for d in runs_dir.iterdir() if d.is_dir()) if runs_dir.is_dir() else [runs_dir]

    all_rows: list[dict] = []
    for run_dir in run_dirs:
        rows, stats = score_run(
            run_dir, embedder, threshold=threshold,
            on_mission=lambda c: is_on_mission(c, config),
        )
        if stats.get("error"):
            print(f"SKIP {stats['run']}: {stats['error']}", file=sys.stderr)
            continue
        for row in rows:
            row["run_at_utc"] = stats["run"]
        all_rows.extend(rows)
        print(f"{stats['run']}: {stats['items']} items -> {stats['clusters']} clusters; "
              f"{stats['matched']}/{stats['chosen_rows']} fates matched "
              f"({stats['unmatched']} unmatched)", file=sys.stderr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: row.get(k, "") for k in _COLUMNS})
    print(f"wrote {len(all_rows)} rows -> {out}")
    return 0 if all_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
