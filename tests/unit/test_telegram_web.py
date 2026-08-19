from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent.collectors import telegram_web
from agent.collectors.base import SourceSpec

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _spec(**overrides) -> SourceSpec:
    defaults = dict(id="tg_militarywave", name="Military Wave", url="https://t.me/s/militarywave",
                     type="telegram", lang="en", enabled=True, max_items=None)
    defaults.update(overrides)
    return SourceSpec(**defaults)


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_collect_counts_raw_entries_including_the_image_only_post():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html; charset=utf-8", max_items=20)
    assert result.raw_entries == 4   # A, B, C (image-only), D


def test_collect_skips_image_only_post_with_no_text():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=20)
    assert result.parsed == 3        # C dropped -- nothing to cluster on
    assert result.kept == 3
    bodies = [it.body for it in result.items]
    assert not any("103" in b for b in bodies)  # post C never produced an Item


def test_collect_sorts_dated_desc_undated_last():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=20)
    # B (11:00, forwarded -- body carries the "Forwarded from ..." prefix)
    # is later than A (10:00); D has no <time> at all -> undated, sorts last.
    assert [it.body for it in result.items] == [
        "Forwarded from Original Channel: Post B, forwarded, later than A.",
        "Post A, plain text, no forward.",
        "Post D, no time element at all.",
    ]
    assert result.items[-1].published_at is None


def test_collect_preserves_forward_from_as_body_prefix():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=20)
    forwarded = next(it for it in result.items if it.body.startswith("Forwarded from"))
    assert forwarded.body == "Forwarded from Original Channel: Post B, forwarded, later than A."


def test_collect_parses_time_datetime_attribute_to_utc():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=20)
    post_a = next(it for it in result.items if it.body.startswith("Post A"))
    assert post_a.published_at == datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)


def test_collect_title_is_always_empty_telegram_has_no_separate_title():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=20)
    assert all(it.title == "" for it in result.items)


def test_collect_extracts_permalink_from_message_date_anchor():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=20)
    post_a = next(it for it in result.items if it.body.startswith("Post A"))
    assert post_a.url == "https://t.me/militarywave/101"


def test_collect_falls_back_to_source_url_when_no_permalink():
    spec = _spec()
    result = telegram_web.collect(spec, b'<div class="tgme_widget_message_wrap"><div class="tgme_widget_message_text">no link here</div></div>', "text/html", max_items=20)
    assert result.items[0].url == spec.url


def test_collect_truncates_by_max_items():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=1)
    assert result.kept == 1
    assert result.items[0].body.startswith("Forwarded from Original Channel: Post B")  # latest dated post


def test_collect_raw_hash_distinct_per_post():
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=20)
    hashes = [it.raw_hash for it in result.items]
    assert len(set(hashes)) == len(hashes)


def test_collect_isolates_one_malformed_post(monkeypatch):
    calls = {"n": 0}
    real_parse_block = telegram_web._parse_block

    def flaky_parse_block(spec, entry_bytes, content_type):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("simulated per-post failure")
        return real_parse_block(spec, entry_bytes, content_type)

    monkeypatch.setattr(telegram_web, "_parse_block", flaky_parse_block)
    result = telegram_web.collect(_spec(), _read("telegram_sample.html"), "text/html", max_items=20)

    assert result.raw_entries == 4
    assert result.parsed == 2   # one post raised (skipped), one was already image-only (None)


def test_collect_raw_hash_is_over_raw_bytes_not_decoded_reencoded_text():
    # Regression for the decode -> slice -> re-encode round trip
    # (PHASE_3_BRIEF.md lines 44-46): the old code decoded the WHOLE page
    # first, sliced the DECODED text per post, then re-encoded each slice as
    # utf-8 before hashing -- so raw_hash was a function of charset
    # resolution, not the bytes that actually arrived on the wire. A
    # windows-1256 post re-encodes to different utf-8 bytes than its own
    # windows-1256 bytes, so this fixture fails under the old behaviour
    # (hash would be over the re-encoded utf-8 bytes) and passes only if the
    # hash is taken over the exact raw byte slice, pre-decode.
    text = "مرحبا"  # "مرحبا"
    raw = (
        '<div class="tgme_widget_message_wrap">'
        '<div class="tgme_widget_message_text">' + text + "</div>"
        '<a class="tgme_widget_message_date" href="https://t.me/x/1">'
        '<time datetime="2026-08-18T10:00:00+00:00">10:00</time></a>'
        "</div>"
    ).encode("windows-1256")

    result = telegram_web.collect(_spec(), raw, "text/html; charset=windows-1256", max_items=20)

    from agent.collectors.base import hash_raw

    assert len(result.items) == 1
    # Single post in this fixture -> the whole raw buffer is that post's slice.
    assert result.items[0].raw_hash == hash_raw(raw)
