"""Formats a registry.CollectReport for its two consumers: the JSON shape
.github/workflows/collect-test.yml asserts against, and the human count
table agent.run's --collect-only prints. Split out of registry.py to keep
that file under the ~200-line cap (CLAUDE.md constraint #12) -- same shape
as Phase 2's telegram.py -> credentials.py + transport.py split. registry.py
owns loading, dispatch and aggregation; this file only turns the result into
text.
"""

from __future__ import annotations

from typing import Mapping

from agent.collectors.base import SourceSpec
from agent.collectors.registry import CollectReport

# The Phase 3 gate (PHASE_3_BRIEF.md, Gate section) requires these four
# specifically -- ynet_he for the DATE_RE fix, all three telegram ids for
# the no-pubDate fix. A source missing from the results table does not
# pass by absence.
REQUIRED_SOURCE_IDS = ("ynet_he", "tg_militarywave", "tg_padeshah_fxn", "tg_ukmto_mirror")


def build_json_report(report: CollectReport, sources_by_id: Mapping[str, SourceSpec]) -> dict:
    """Machine-checkable shape for collect-test.yml -- the gate parses this,
    it does not grep unstructured log text (send-test.yml's own lesson:
    run_collect_only must never be the thing deciding pass/fail)."""
    per_source = {}
    for source_id, res in sorted(report.results.items()):
        timestamps = [it.published_at.isoformat() for it in res.items if it.published_at is not None]
        per_source[source_id] = {
            "name": sources_by_id[source_id].name if source_id in sources_by_id else source_id,
            "raw_entries": res.raw_entries,
            "parsed": res.parsed,
            "kept": res.kept,
            "error": res.error,
            "published_at": timestamps,
            "all_timestamps_identical": len(timestamps) > 1 and len(set(timestamps)) == 1,
        }
    return {
        "generated_at": report.generated_at.isoformat(),
        "required_source_ids": list(REQUIRED_SOURCE_IDS),
        "sources_enabled": report.sources_enabled,
        "sources_with_items": report.sources_with_items,
        "total_kept": report.total_kept,
        "sources": per_source,
    }


def format_table(report: CollectReport, sources_by_id: Mapping[str, SourceSpec]) -> str:
    """Raw/parsed/kept per source -- a cap doing the work must be
    distinguishable from a feed doing it (Gate section, final line)."""
    lines = [f"{'id':<20}{'raw':>6}{'parsed':>8}{'kept':>6}  status"]
    for source_id in sorted(report.results):
        res = report.results[source_id]
        lines.append(f"{source_id:<20}{res.raw_entries:>6}{res.parsed:>8}{res.kept:>6}  {res.error or 'ok'}")
    lines.append(
        f"\nTOTAL kept={report.total_kept}  "
        f"sources_with_items={report.sources_with_items}/{report.sources_enabled}"
    )
    return "\n".join(lines)
