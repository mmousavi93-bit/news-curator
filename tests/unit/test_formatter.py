from __future__ import annotations

from agent.delivery.budget import DEFAULT_MAX_MESSAGES, utf16_len
from agent.delivery.formatter import escape_attr, escape_html, format_single, format_split
from agent.delivery.message import Item, Message


def test_escape_html_handles_script_amp_and_bare_angle_bracket():
    text = "Reuters <script>alert(1)</script> & <b>bold</b>"
    escaped = escape_html(text)
    assert "<script>" not in escaped
    assert "<b>" not in escaped
    assert "&amp;" in escaped
    assert "&lt;script&gt;" in escaped


def test_escape_attr_also_escapes_double_quote():
    escaped = escape_attr('https://example.com/?q="x"&y=<1')
    assert '"' not in escaped
    assert "&quot;" in escaped
    assert "&amp;" in escaped
    assert "&lt;" in escaped


def test_format_single_only_emits_the_four_allowed_tags():
    import re

    message = Message(
        header="Daily digest",
        items=(
            Item(headline="Headline one", priority=0, detail="Some detail", url="https://example.com/a"),
            Item(headline="Headline two", priority=1),
        ),
    )
    rendered = format_single(message)
    tags = set(re.findall(r"</?([a-z]+)[ >]", rendered))
    assert tags <= {"b", "i", "a", "code"}


def test_format_single_escapes_hostile_headline_content():
    message = Message(
        header="H",
        items=(Item(headline="Reuters <script>bad()</script> & Co", priority=0),),
    )
    rendered = format_single(message)
    assert "<script>" not in rendered
    assert "&amp;" in rendered


def test_format_single_never_exceeds_4096_utf16_units_and_keeps_tags_whole():
    # A message built to be well over the 4,096-char cap (brief gate #4).
    items = tuple(
        Item(headline=f"Headline number {i} " + "x" * 60, priority=i, detail="detail " * 10)
        for i in range(60)
    )
    message = Message(header="STRAT digest -- overflow test", items=items, footer="run-marker")
    rendered = format_single(message)
    assert utf16_len(rendered) <= 4096
    assert "more" in rendered  # honest overflow marker
    _assert_tags_balanced(rendered)


def test_format_split_never_exceeds_max_units_per_message_and_keeps_tags_whole():
    items = tuple(
        Item(headline=f"Headline number {i} " + "x" * 60, priority=i, detail="detail " * 10)
        for i in range(60)
    )
    message = Message(header="STRAT digest -- split test", items=items, footer="run-marker")
    pages = format_split(message)
    assert 1 <= len(pages) <= DEFAULT_MAX_MESSAGES
    for page in pages:
        assert utf16_len(page) <= 4096
        _assert_tags_balanced(page)


def test_format_single_with_persian_content_respects_utf16_budget():
    message = Message(
        header="گزارش امروز",
        items=(Item(headline="ایران و منطقه در وضعیت هشدار هستند", priority=0, detail="جزئیات بیشتر"),),
    )
    rendered = format_single(message, max_units=4096)
    assert utf16_len(rendered) <= 4096
    assert "گزارش امروز" in rendered


def _assert_tags_balanced(html: str) -> None:
    """Every opening <b>/<i>/<a ...> has a matching close, and no tag is
    truncated mid-way (e.g. '<b' with no closing '>')."""
    import re

    for tag in ("b", "i"):
        assert html.count(f"<{tag}>") == html.count(f"</{tag}>")
    assert html.count("<a ") == html.count("</a>")
    # No dangling '<' that never reaches a '>' before the string ends or the
    # next '<' begins -- a truncated tag would look like this.
    for match in re.finditer(r"<[^>]*$", html):
        raise AssertionError(f"truncated/unbalanced tag at end of string: {match.group()!r}")
