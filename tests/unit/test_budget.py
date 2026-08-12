from __future__ import annotations

import pytest

from agent.delivery.budget import (
    Fragment,
    fit_single,
    fit_split,
    utf16_len,
    utf16_truncate,
)

PERSIAN = "ایران و منطقه در وضعیت هشدار هستند"  # all BMP -- 1 UTF-16 unit per char
EMOJI = "🚨"  # astral plane -- 2 UTF-16 units, 1 Python char


def test_utf16_len_matches_python_len_for_bmp_persian_text():
    # The bug this guards against: computing length from UTF-8 byte count
    # instead of UTF-16 code units. Persian is multi-byte in UTF-8 but
    # single-unit in UTF-16, so a UTF-8-byte-based budget would wrongly
    # think this string is much longer than Telegram will count it as.
    assert utf16_len(PERSIAN) == len(PERSIAN)
    assert utf16_len(PERSIAN) != len(PERSIAN.encode("utf-8"))


def test_utf16_len_counts_surrogate_pairs_for_astral_emoji():
    assert len(EMOJI) == 1  # one Python character
    assert utf16_len(EMOJI) == 2  # two UTF-16 code units


def test_utf16_truncate_does_not_split_a_surrogate_pair():
    text = "AB" + EMOJI + "CD"  # emoji occupies units 2-3
    truncated = utf16_truncate(text, 3)  # would land mid-surrogate-pair if naive
    # Must decode cleanly and never contain a lone surrogate.
    truncated.encode("utf-16-le").decode("utf-16-le")
    assert utf16_len(truncated) <= 3
    assert truncated == "AB"  # the incomplete emoji is dropped, not mangled


def test_utf16_truncate_is_noop_when_already_within_budget():
    assert utf16_truncate("hello", 100) == "hello"


def _frag(priority: int, order: int, body: str, headline_only: str | None = None) -> Fragment:
    return Fragment(priority=priority, order=order, body=body, headline_only=headline_only or body)


def test_fit_single_keeps_header_and_drops_lowest_priority_item_first():
    header = "H"
    frags = [
        _frag(priority=0, order=0, body="A" * 30),  # most important
        _frag(priority=5, order=1, body="B" * 30),  # least important
    ]
    # Room for header + one 30-char item + the overflow marker, but not a
    # second 30-char item as well.
    max_units = 50
    result = fit_single(header, frags, "", max_units)
    assert result.startswith("H")
    assert "A" * 30 in result
    assert "B" * 30 not in result
    assert "more" in result  # honest overflow marker present


def test_fit_single_never_exceeds_max_units():
    header = "Header text"
    frags = [_frag(priority=i, order=i, body="X" * 500) for i in range(10)]
    result = fit_single(header, frags, "footer text", 4096)
    assert utf16_len(result) <= 4096


def test_fit_single_drops_detail_before_dropping_the_item_entirely():
    header = "H"
    body_with_detail = "HEADLINE" + "\n" + "D" * 40
    frag = _frag(priority=0, order=0, body=body_with_detail, headline_only="HEADLINE")
    # Budget fits the headline-only form but not headline+detail.
    max_units = 1 + 2 + len("HEADLINE")
    result = fit_single(header, [frag], "", max_units)
    assert "HEADLINE" in result
    assert "D" * 40 not in result


def test_fit_single_appends_no_marker_when_everything_fits():
    header = "H"
    frags = [_frag(priority=0, order=0, body="short")]
    result = fit_single(header, frags, "", 4096)
    assert "more" not in result


def test_fit_single_overflow_marker_never_pushes_past_max_units():
    header = "H"
    frags = [_frag(priority=i, order=i, body="X" * 200) for i in range(50)]
    result = fit_single(header, frags, "", 500)
    assert utf16_len(result) <= 500
    assert "more" in result


def test_fit_split_distributes_items_across_pages_within_max_messages():
    header = "H"
    frags = [_frag(priority=i, order=i, body="X" * 100) for i in range(20)]
    pages = fit_split(header, frags, "", max_units=400, max_messages=3)
    assert len(pages) <= 3
    for page in pages:
        assert utf16_len(page) <= 400
    # Continuation pages are self-contained (repeat the header).
    if len(pages) > 1:
        assert pages[1].startswith("H")


def test_fit_split_caps_at_max_messages_even_with_more_leftover_items():
    header = "H"
    frags = [_frag(priority=i, order=i, body="X" * 100) for i in range(50)]
    pages = fit_split(header, frags, "", max_units=400, max_messages=2)
    assert len(pages) == 2
    assert "more" in pages[-1]


def test_fit_single_overflow_marker_present_when_header_consumes_the_whole_budget():
    """Regression for the defect where a header that alone consumed most or
    all of the budget left no room for the final guard's `body + marker`
    check to pass, so a dropped item vanished with no trace. The marker must
    be present whenever anything was dropped, even here."""
    header = "X" * 20
    frag = _frag(priority=0, order=0, body="Y" * 40)
    result = fit_single(header, [frag], "", 20)
    assert utf16_len(result) <= 20
    assert "more" in result


def _assert_no_lone_surrogate(text: str) -> None:
    assert not any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


@pytest.mark.parametrize("max_units", [0, 1, 5, 8, 11, 12])
def test_fit_single_overflow_marker_never_exceeds_tiny_budgets(max_units):
    """Regression: the overflow marker used to be appended unconditionally
    after the repack, with no check that `max_units` was itself large
    enough to hold it. 10 fragments behind a real header overflow the
    marker alone (12 UTF-16 units) at every budget below 12 -- this must
    now clamp (or vanish entirely at max_units=0) instead of overflowing."""
    header = "Header text"
    frags = [_frag(priority=i, order=i, body=f"ITEM{i}" * 10) for i in range(10)]
    result = fit_single(header, frags, "", max_units)
    assert utf16_len(result) <= max_units
    _assert_no_lone_surrogate(result)
    if max_units == 0:
        assert result == ""


def test_fit_split_footer_only_appended_to_last_page():
    header = "H"
    # max_units=50 forces exactly two pages (30-char item 1 leaves no room
    # for item 2 alongside it), with clear headroom on page 2 for the footer.
    frags = [_frag(priority=0, order=0, body="X" * 30), _frag(priority=1, order=1, body="Y" * 30)]
    pages = fit_split(header, frags, "FOOT", max_units=50, max_messages=3)
    assert len(pages) == 2
    assert "FOOT" not in pages[0]
    assert "FOOT" in pages[1]
