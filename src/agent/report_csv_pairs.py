"""Writer for pairs_<ts>.csv (session 9s): every same-run pairwise
comparison at or above settings.pipeline.samerun_pair_log_floor, dropped
or not, with the pair's text -- the measurement that settles the
cluster-fragmentation question for the 9t clusterer fix.

Follows the report_csv family rules: no LLM calls, no clock reads (the
record's run_at_utc came from ctx.now at record time), no secrets --
news text only. A write failure must never break a run: run.py wraps the
whole reporting block.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

from agent.pipeline.samerun_dedup import PairRecord

_FIELDS = (
    "run_at_utc", "key_a", "key_b", "similarity", "decision", "threshold",
    "n_members_a", "n_members_b", "independent_count_a",
    "independent_count_b", "headline_a", "headline_b",
)


def write_pairs(path: Path, rows: Sequence[PairRecord]) -> Path:
    """Write the pairs CSV (header + one row per record). Returns the
    path. An empty `rows` still writes the header, like the other
    per-run CSVs -- an artifact with no pairs is a fact (a quiet run),
    not a missing report."""
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_FIELDS)
        for row in rows:
            writer.writerow((
                row.run_at_utc, row.key_a, row.key_b, row.similarity,
                row.decision, row.threshold, row.n_members_a,
                row.n_members_b, row.independent_count_a,
                row.independent_count_b, row.headline_a, row.headline_b,
            ))
    return path
