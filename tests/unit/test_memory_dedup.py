"""Gate 2 (the same story twice is sent once) and gate 3 (the layer-2 trap).

Gate 3 is the one worth reading. `reuters_gnews` and `ap_gnews` reach Reuters
and AP through a Google News `site:` proxy, so every item from those feeds
shares a host, a path prefix and a parameter set -- the article's identity lives
in one encoded component. A naive "strip the query string" normaliser turns the
whole feed into one URL, and the resulting failure is INVISIBLE: an engine that
discards too much looks exactly like a quiet news day.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.collectors.base import Item
from agent.memory import db as memory_db
from agent.memory import dedup, models

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

# Two genuinely different Reuters articles as Google News proxies them: same
# host, same path prefix, same parameters, different encoded target.
GNEWS_A = (
    "https://news.google.com/rss/articles/"
    "CBMiK2h0dHBzOi8vd3d3LnJldXRlcnMuY29tL3dvcmxkL2lyYW4tb25l?oc=5&hl=en-US&gl=US&ceid=US:en"
)
GNEWS_B = (
    "https://news.google.com/rss/articles/"
    "CBMiK2h0dHBzOi8vd3d3LnJldXRlcnMuY29tL3dvcmxkL2lyYW4tdHdv?oc=5&hl=en-US&gl=US&ceid=US:en"
)


@pytest.fixture
def conn(tmp_path: Path):
    connection = memory_db.initialize(tmp_path / "state.db")
    yield connection
    connection.close()


def item(url: str, title: str = "Iran strike reported", source: str = "reuters_gnews") -> Item:
    return Item(
        source_id=source, url=url, title=title, body="body",
        published_at=NOW, lang="en", raw_hash="h",
    )


# --- layer 2 normalisation -------------------------------------------------

def test_tracking_params_are_stripped() -> None:
    plain = dedup.normalise_url("https://reuters.com/world/iran-one")
    for suffix in (
        "?utm_source=twitter", "?utm_campaign=x&utm_medium=social", "?fbclid=abc",
        "?gclid=abc", "?ref=hp", "?ref_src=twsrc", "?at_medium=RSS", "?at_campaign=64",
    ):
        assert dedup.normalise_url(f"https://reuters.com/world/iran-one{suffix}") == plain


def test_non_tracking_params_are_preserved_verbatim() -> None:
    """The allow-list is an allow-list. Everything not on it survives, values
    included -- that is the whole defence against the Google News collapse."""
    url = "https://news.google.com/rss/articles/ABC?oc=5&hl=en-US&gl=US&ceid=US:en"
    assert dedup.normalise_url(url) == url


def test_two_google_news_targets_stay_distinct() -> None:
    """Gate 3."""
    assert dedup.normalise_url(GNEWS_A) != dedup.normalise_url(GNEWS_B)


def test_google_news_item_still_dedupes_against_its_tracked_twin() -> None:
    tracked = GNEWS_A + "&utm_source=newsletter"
    assert dedup.normalise_url(tracked) == dedup.normalise_url(GNEWS_A)


def test_host_case_fragment_and_trailing_slash_are_normalised() -> None:
    variants = [
        "https://Example.COM/world/iran/",
        "https://example.com/world/iran",
        "https://example.com/world/iran#top",
        "https://example.com/world/iran/#top",
    ]
    assert len({dedup.normalise_url(v) for v in variants}) == 1


def test_port_and_path_case_are_not_discarded() -> None:
    assert dedup.normalise_url("https://example.com:8443/a") != dedup.normalise_url(
        "https://example.com/a"
    )
    assert dedup.normalise_url("https://example.com/A") != dedup.normalise_url(
        "https://example.com/a"
    )


def test_scheme_is_not_collapsed() -> None:
    """http and https are different transports, and `mee` was plaintext through
    four probe rounds. Folding them would hide that class of change."""
    assert dedup.normalise_url("http://example.com/a") != dedup.normalise_url(
        "https://example.com/a"
    )


# --- layer 3 titles --------------------------------------------------------

def test_title_normalisation_is_punctuation_and_case_insensitive() -> None:
    assert dedup.normalise_title("Iran: 'Strike' reported!") == dedup.normalise_title(
        "  iran   strike  reported  "
    )


def test_non_ascii_punctuation_is_stripped() -> None:
    """Arabic comma and Persian question mark. `string.punctuation` would not
    have touched either, and three of four source languages are non-ASCII."""
    assert dedup.normalise_title("تهران، ایران؟") == \
        dedup.normalise_title("تهران ایران")


def test_empty_title_does_not_hash() -> None:
    """Otherwise every untitled item -- a bare photo post, a feed entry whose
    title is '-' -- collides with every other untitled item."""
    assert dedup.title_hash("") is None
    assert dedup.title_hash("   ...   ") is None
    assert dedup.title_hash("Iran") is not None


def test_untitled_items_are_not_folded_together(conn) -> None:
    items = [item("https://a.example/1", title="-"), item("https://a.example/2", title="")]
    assert len(dedup.store_new(conn, items, NOW).new) == 2


# --- the three layers end to end ------------------------------------------

def test_same_batch_twice_yields_nothing_new(conn) -> None:
    """Gate 2, first half."""
    batch = [item(GNEWS_A, "Story one"), item(GNEWS_B, "Story two")]
    assert len(dedup.store_new(conn, batch, NOW).new) == 2
    second = dedup.store_new(conn, batch, NOW)
    assert second.new == ()
    assert models.count_items(conn) == 2


def test_tracking_suffix_still_yields_nothing_new(conn) -> None:
    """Gate 2, second half: the same batch with tracking parameters appended."""
    batch = [item(GNEWS_A, "Story one"), item(GNEWS_B, "Story two")]
    dedup.store_new(conn, batch, NOW)
    tracked = [
        item(GNEWS_A + "&utm_source=nl", "Story one"),
        item(GNEWS_B + "&fbclid=xyz", "Story two"),
    ]
    result = dedup.store_new(conn, tracked, NOW)
    assert result.new == ()
    assert result.by_norm_url == 2


def test_two_google_news_targets_both_survive_storage(conn) -> None:
    """Gate 3 at the storage layer, not just the string layer -- the collapse
    would happen here, and titles differ so layer 3 cannot mask it."""
    result = dedup.store_new(conn, [item(GNEWS_A, "One"), item(GNEWS_B, "Two")], NOW)
    assert len(result.new) == 2
    assert result.by_norm_url == 0


def test_wire_copy_from_two_sources_is_one_story(conn) -> None:
    """Layer 3's reason to exist: Reuters and AP publishing identical copy under
    different URLs is the single largest duplicate class in this feed set."""
    result = dedup.store_new(
        conn,
        [
            item("https://a.example/1", "Iran closes airspace", source="reuters_gnews"),
            item("https://b.example/2", "IRAN CLOSES AIRSPACE.", source="ap_gnews"),
        ],
        NOW,
    )
    assert len(result.new) == 1
    assert result.by_title == 1


def test_duplicates_within_one_batch_are_caught(conn) -> None:
    """Without the intra-batch pass a fresh database emits four copies of every
    wire story on its first run and is only correct from run two."""
    batch = [item(GNEWS_A, "Same headline") for _ in range(4)]
    result = dedup.store_new(conn, batch, NOW)
    assert len(result.new) == 1
    assert result.by_url == 3


def test_seen_rows_and_items_commit_together(conn) -> None:
    dedup.store_new(conn, [item(GNEWS_A, "One")], NOW)
    assert conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0] == 1
    assert models.count_items(conn) == 1


def test_layer_counts_are_reported_separately(conn) -> None:
    dedup.store_new(conn, [item(GNEWS_A, "One")], NOW)
    result = dedup.store_new(
        conn,
        [
            item(GNEWS_A, "One"),                       # layer 1
            item(GNEWS_A + "&utm_source=x", "One"),     # layer 2
            item("https://other.example/z", "One"),     # layer 3
        ],
        NOW,
    )
    assert (result.by_url, result.by_norm_url, result.by_title) == (1, 1, 1)
    assert result.duplicates == 3
