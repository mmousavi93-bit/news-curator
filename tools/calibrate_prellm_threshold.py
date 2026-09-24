"""CLI: turn scored cluster CSVs into a pre-LLM drop threshold recommendation.

    py -3.12 tools/calibrate_prellm_threshold.py artifacts/run_*/chosen_*.csv

Accepts any CSV carrying `fate` + `prellm_score` (+ optional `on_mission`,
`n_members`, `headline`) -- either a real run's chosen.csv once the drop
wiring is enabled, or the joined export tools/score_historical_prellm.py
writes on CI. Pure analysis: no LLM, no network, no embeddings.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.pipeline.prellm_calibration import (  # noqa: E402
    load,
    recommend,
    render_report,
    sweep,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="scored cluster CSVs")
    parser.add_argument("--steps", type=int, default=101,
                        help="threshold grid resolution (default 101)")
    parser.add_argument("--json-out", help="also write the sweep curve here as JSON")
    args = parser.parse_args(argv)

    loaded = load(args.paths)
    if not loaded.rows:
        print("no scored rows found -- nothing to calibrate")
        return 1
    points = sweep(loaded.rows, args.steps)
    print(render_report(loaded, points))
    if args.json_out:
        import json
        Path(args.json_out).write_text(json.dumps({
            "rows": len(loaded.rows),
            "unscored": loaded.unscored,
            "guard_unknown": loaded.guard_unknown,
            "curve": [
                {"threshold": p.threshold, "waste_caught": p.waste_caught,
                 "waste_total": p.waste_total, "keep_lost": p.keep_lost,
                 "keep_total": p.keep_total, "recall": p.recall,
                 "lost_rate": p.lost_rate}
                for p in points
            ],
        }, indent=2), encoding="utf-8")
    best = recommend(points)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
