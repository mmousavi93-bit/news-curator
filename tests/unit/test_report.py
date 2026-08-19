"""build_json_report / format_table -- split out of test_registry.py when
report.py was split out of registry.py (CLAUDE.md constraint #12), same
shape as Phase 2's test_telegram.py / test_telegram_retry.py split.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent.collectors import registry, report
from agent.collectors.base import Item, SourceResult, SourceSpec


def _item(source_id: str, dt) -> Item:
    return Item(source_id=source_id, url=f"https://x/{source_id}", title="t", body="b",
                published_at=dt, lang="en", raw_hash="h" * 8)


def test_build_json_report_flags_identical_timestamps():
    same_time = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = SourceResult(source_id="s", raw_entries=2, parsed=2, kept=2,
                           items=[_item("s", same_time), _item("s", same_time)])
    collect_report = registry.CollectReport(datetime.now(timezone.utc), {"s": result}, 2, 1, 1)
    spec = SourceSpec(id="s", name="S", url="https://x", type="rss", lang="en", enabled=True)

    payload = report.build_json_report(collect_report, {"s": spec})
    assert payload["sources"]["s"]["all_timestamps_identical"] is True
    assert payload["required_source_ids"] == list(report.REQUIRED_SOURCE_IDS)


def test_build_json_report_does_not_flag_a_single_item_as_identical():
    result = SourceResult(source_id="s", raw_entries=1, parsed=1, kept=1,
                           items=[_item("s", datetime(2026, 8, 1, tzinfo=timezone.utc))])
    collect_report = registry.CollectReport(datetime.now(timezone.utc), {"s": result}, 1, 1, 1)
    spec = SourceSpec(id="s", name="S", url="https://x", type="rss", lang="en", enabled=True)

    payload = report.build_json_report(collect_report, {"s": spec})
    assert payload["sources"]["s"]["all_timestamps_identical"] is False


def test_format_table_shows_raw_parsed_kept_and_error_status():
    ok = SourceResult(source_id="ok", raw_entries=5, parsed=4, kept=4, items=[])
    failed = SourceResult(source_id="failed", raw_entries=0, parsed=0, kept=0, items=[], error="HTTP 403")
    collect_report = registry.CollectReport(datetime.now(timezone.utc), {"ok": ok, "failed": failed}, 4, 1, 2)
    specs = {
        "ok": SourceSpec(id="ok", name="OK", url="https://x", type="rss", lang="en", enabled=True),
        "failed": SourceSpec(id="failed", name="Failed", url="https://y", type="rss", lang="en", enabled=True),
    }

    table = report.format_table(collect_report, specs)
    assert "ok" in table and "4" in table
    assert "failed" in table and "HTTP 403" in table
    assert "TOTAL kept=4" in table
