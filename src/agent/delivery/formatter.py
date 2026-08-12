"""Message object -> Telegram-ready HTML string(s).

Parse mode is HTML, not MarkdownV2 (brief decision: MarkdownV2 needs 18
escaped characters and news headlines are full of them -- one missed escape
is a 400 at 3am on an unattended run). Only the four Telegram-supported tags
are ever emitted: <b>, <i>, <a href="">, <code>. Every interpolated string
that did not originate in this module is escaped for &, <, > (and, inside an
attribute, ").

The header is deliberately left as plain escaped text with no tag wrapping.
That is what lets budget.py truncate it as a last resort without ever
risking a split HTML tag -- see budget.py's module docstring.
"""

from __future__ import annotations

from agent.delivery.budget import (
    DEFAULT_MAX_MESSAGES,
    DEFAULT_MAX_UNITS,
    Fragment,
    fit_single,
    fit_split,
)
from agent.delivery.message import Item, Message


def escape_html(text: str) -> str:
    """Escape &, <, > -- the only characters that are structurally
    significant to Telegram's HTML parse mode outside of attributes."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(text: str) -> str:
    """escape_html plus " -- needed for text embedded inside href="..."."""
    return escape_html(text).replace('"', "&quot;")


def _render_headline(item: Item) -> str:
    text = escape_html(item.headline)
    if item.url:
        return f'<a href="{escape_attr(item.url)}">{text}</a>'
    return f"<b>{text}</b>"


def _render_fragment(item: Item, order: int) -> Fragment:
    headline_html = _render_headline(item)
    if item.detail:
        body = f"{headline_html}\n<i>{escape_html(item.detail)}</i>"
    else:
        body = headline_html
    return Fragment(priority=item.priority, order=order, body=body, headline_only=headline_html)


def _prepare(message: Message) -> tuple[str, list[Fragment], str]:
    header_text = escape_html(message.header)
    fragments = [_render_fragment(item, i) for i, item in enumerate(message.items)]
    footer_text = escape_html(message.footer) if message.footer else ""
    return header_text, fragments, footer_text


def format_single(message: Message, max_units: int = DEFAULT_MAX_UNITS) -> str:
    """Render `message` as one Telegram-ready HTML string, truncating by
    priority (never mid-tag) if it would otherwise exceed `max_units`."""
    header_text, fragments, footer_text = _prepare(message)
    return fit_single(header_text, fragments, footer_text, max_units)


def format_split(
    message: Message,
    max_units: int = DEFAULT_MAX_UNITS,
    max_messages: int = DEFAULT_MAX_MESSAGES,
) -> list[str]:
    """Render `message` as up to `max_messages` Telegram-ready HTML strings,
    each <= max_units, splitting by priority instead of truncating."""
    header_text, fragments, footer_text = _prepare(message)
    return fit_split(header_text, fragments, footer_text, max_units, max_messages)
