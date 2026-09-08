"""Tiny per-cluster CSV field helpers shared by report_csv.py and
report_csv_chosen.py -- split out 2026-09-06 (round-2 review, fix 5) so
the two writer modules can share them without an import cycle (report_csv
owns write_run_reports/_write_read/_write_summaries/_write_run;
report_csv_chosen owns the larger _fate_for/_write_chosen pair). No LLM
calls, no clock reads, no secrets -- same contract as the parent modules.
"""

from __future__ import annotations


def _sources(cluster) -> str:
    return "|".join(sorted({m.source_id for m in cluster.members}))


def _best_tier(cluster, credibility) -> int:
    from agent.pipeline.rank import best_tier
    return best_tier(cluster, credibility)


def _when_utc(cluster) -> str:
    stamps = [m.published_at for m in cluster.members if m.published_at is not None]
    return max(stamps).isoformat() if stamps else ""
