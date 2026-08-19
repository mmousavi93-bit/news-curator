from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.collectors import rss
from agent.collectors.base import SourceSpec

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _spec(**overrides) -> SourceSpec:
    defaults = dict(id="sample", name="Sample", url="https://example.test/feed",
                     type="rss", lang="en", enabled=True, max_items=None)
    defaults.update(overrides)
    return SourceSpec(**defaults)


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_collect_parses_rss_items_and_sorts_dated_desc_undated_last():
    result = rss.collect(_spec(), _read("rss_sample.xml"), "application/rss+xml; charset=utf-8", max_items=20)

    assert result.raw_entries == 3
    assert result.parsed == 3
    assert result.kept == 3

    # item two (2026-08-17) is later than item one (2026-08-16); item three is undated.
    assert [it.title for it in result.items] == [
        "Item two, dc:date and content:encoded, namespaced",
        "Item one, RFC-822 date",
        "Item three, no date at all",
    ]
    assert result.items[-1].published_at is None  # never fabricated as "now"


def test_collect_strips_markup_and_unwraps_cdata():
    result = rss.collect(_spec(), _read("rss_sample.xml"), "application/rss+xml", max_items=20)
    by_title = {it.title: it for it in result.items}
    assert by_title["Item one, RFC-822 date"].body == "Body one with & an entity and markup."
    assert by_title["Item two, dc:date and content:encoded, namespaced"].body == (
        "Body two, from a content:encoded block."
    )


def test_collect_extracts_link_from_href_attribute_and_from_text():
    result = rss.collect(_spec(), _read("rss_sample.xml"), "application/rss+xml", max_items=20)
    by_title = {it.title: it for it in result.items}
    assert by_title["Item one, RFC-822 date"].url == "https://example.test/item-one"
    assert by_title["Item two, dc:date and content:encoded, namespaced"].url == "https://example.test/item-two"


def test_collect_falls_back_to_source_url_when_no_link_present():
    result = rss.collect(_spec(), _read("rss_sample.xml"), "application/rss+xml", max_items=20)
    by_title = {it.title: it for it in result.items}
    assert by_title["Item three, no date at all"].url == "https://example.test/feed"


def test_collect_raw_hash_present_and_consistent_across_calls():
    result = rss.collect(_spec(), _read("rss_sample.xml"), "application/rss+xml", max_items=20)
    result2 = rss.collect(_spec(), _read("rss_sample.xml"), "application/rss+xml", max_items=20)
    hashes1 = sorted(it.raw_hash for it in result.items)
    hashes2 = sorted(it.raw_hash for it in result2.items)
    assert hashes1 == hashes2
    assert len(set(hashes1)) == 3  # three distinct entries, three distinct hashes


def test_collect_respects_atom_entries_via_the_same_entry_pattern():
    result = rss.collect(_spec(), _read("atom_sample.xml"), "application/atom+xml", max_items=20)
    assert result.raw_entries == 2
    assert result.parsed == 2
    assert result.kept == 2
    assert [it.title for it in result.items] == ["Atom entry two, later", "Atom entry one"]


def test_collect_zero_parse_is_distinguishable_from_genuinely_empty():
    zero_parse = rss.collect(_spec(), _read("zero_parse_sample.xml"), "application/rss+xml", max_items=20)
    empty = rss.collect(_spec(), _read("empty_sample.xml"), "application/rss+xml", max_items=20)

    assert zero_parse.raw_entries == 3   # entries existed
    assert zero_parse.parsed == 0        # nothing usable was inside them
    assert zero_parse.kept == 0

    assert empty.raw_entries == 0        # no entries existed at all
    assert empty.parsed == 0
    assert empty.kept == 0


def test_collect_truncates_by_max_items():
    result = rss.collect(_spec(), _read("rss_sample.xml"), "application/rss+xml", max_items=1)
    assert result.raw_entries == 3
    assert result.parsed == 3
    assert result.kept == 1
    assert result.items[0].title == "Item two, dc:date and content:encoded, namespaced"


def test_collect_isolates_one_bad_entry_from_the_rest(monkeypatch):
    # Force _parse_entry to raise on exactly one of the three entries.
    # collect() must still return the other two -- one bad entry never
    # aborts the whole source.
    calls = {"n": 0}
    real_parse_entry = rss._parse_entry

    def flaky_parse_entry(spec, entry_bytes, content_type):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("simulated per-entry failure")
        return real_parse_entry(spec, entry_bytes, content_type)

    monkeypatch.setattr(rss, "_parse_entry", flaky_parse_entry)
    result = rss.collect(_spec(), _read("rss_sample.xml"), "application/rss+xml", max_items=20)

    assert result.raw_entries == 3
    assert result.parsed == 2   # one entry raised and was skipped, not counted
    assert result.kept == 2


# ---------------------------------------------------------------------------
# _parse_date -- direct tests of the date-parsing fallback chain.
# ---------------------------------------------------------------------------

def test_parse_date_rfc822():
    dt = rss._parse_date("Tue, 16 Aug 2026 08:00:00 GMT")
    assert dt == datetime(2026, 8, 16, 8, 0, 0, tzinfo=timezone.utc)


def test_parse_date_iso8601():
    dt = rss._parse_date("2026-08-17T09:30:00+00:00")
    assert dt == datetime(2026, 8, 17, 9, 30, 0, tzinfo=timezone.utc)


def test_parse_date_ynet_format_summer_offset():
    # Ynet's undocumented format, no RFC-822/ISO form at all. April-Sept -> UTC+3 (IDT).
    dt = rss._parse_date("8/16/2026 11:00:00 AM")
    assert dt == datetime(2026, 8, 16, 8, 0, 0, tzinfo=timezone.utc)


def test_parse_date_ynet_format_winter_offset():
    # Nov-Feb -> UTC+2 (IST, no DST).
    dt = rss._parse_date("12/16/2026 11:00:00 AM")
    assert dt == datetime(2026, 12, 16, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_date_ynet_format_pm_and_midnight_edge_cases():
    noon = rss._parse_date("1/1/2026 12:00:00 PM")
    assert noon.hour == 12 - 2  # noon IST -> 10:00 UTC (winter offset)
    midnight = rss._parse_date("1/1/2026 12:00:00 AM")
    assert midnight.hour == (0 - 2) % 24  # 12 AM -> hour 0 local -> wraps to prior day


def test_parse_date_empty_or_garbage_returns_none_never_fabricates_now():
    assert rss._parse_date("") is None
    assert rss._parse_date("not a date at all") is None


def test_extract_link_prefers_href_attribute_over_text_form():
    assert rss._extract_link('<link href="https://a.test/x">text-link</link>') == "https://a.test/x"


def test_extract_link_falls_back_to_text_form():
    assert rss._extract_link("<link>https://a.test/y</link>") == "https://a.test/y"


def test_extract_link_returns_none_when_absent():
    assert rss._extract_link("<title>no link here</title>") is None
