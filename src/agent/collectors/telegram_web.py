"""t.me/s/<channel> preview scraping -- HTML, not RSS/Atom (constraint 6:
no Telethon/MTProto, this endpoint is the only legal path).

Regex-based, like rss.py, for the same reason: a stateful HTML parser has
to track a tag-nesting stack, and an un-self-closed void element (<br>,
<img>) never fires a matching end-tag event, silently corrupting that stack
for every post parsed afterward. The preview page's structure is simple and
repetitive -- one post is one top-level `<div class="tgme_widget_message_wrap
...">` block -- so splitting on the wrapper's start tag and running narrow
regexes inside each slice avoids that whole failure class.

UNVERIFIED against a live fetch: the sandbox has no network egress, so the
exact class names below (tgme_widget_message_wrap/_text/
_forwarded_from_name, and the <time datetime="..."> attribute) are
best-effort from what a t.me/s/ page is documented to render, matching the
`class="tgme_widget_message` prefix tools/check_feeds.py already probes
against. Confirm against real output at the Phase 3 CI gate.

Two facts this collector exists to fix, both load-bearing later:
  - t.me/s/ has no pubDate at all. Post time lives only in the <time>
    element's datetime attribute. A None here quietly disables the
    30-minute near-duplicate window in analysis/LEAD_HANDLING.md.
  - Forward-from attribution is fetch-time-only -- the preview shows
    roughly the last 20 posts, so a post that scrolls off cannot be
    recovered later. Preserved as a "Forwarded from <name>: " prefix on
    `body` (brief 3b), not a new Item field.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from agent.collectors.base import Item, SourceResult, SourceSpec, decode_body, hash_raw, strip_html

_WRAP_START_RE = re.compile(rb'<div class="tgme_widget_message_wrap[^"]*"')
_TIME_RE = re.compile(r'<time[^>]*\bdatetime="([^"]+)"', re.I)
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.I | re.S)
_FORWARD_RE = re.compile(
    r'<span class="tgme_widget_message_forwarded_from_name"[^>]*>(.*?)</span>', re.I | re.S
)
_LINK_RE = re.compile(r'<a class="tgme_widget_message_date"[^>]*href="([^"]+)"', re.I)


def _parse_iso(raw: str) -> datetime | None:
    # Same 'Z'-suffix normalisation as rss.py's _parse_date -- fromisoformat()
    # only accepts a trailing 'Z' from Python 3.11 onward, and this collector
    # must not depend on which interpreter it happens to run under.
    text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_block(spec: SourceSpec, entry_bytes: bytes, content_type: str) -> Item | None:
    # Decode this one post's raw bytes AFTER hashing, not before -- see
    # collect() below. Mirrors rss.py's _parse_entry, which decodes per
    # entry rather than decoding the whole page once and slicing text.
    block = decode_body(content_type, entry_bytes)
    text_m = _TEXT_RE.search(block)
    body = strip_html(html.unescape(text_m.group(1))) if text_m else ""

    forward_m = _FORWARD_RE.search(block)
    if forward_m:
        forwarded_name = strip_html(html.unescape(forward_m.group(1)))
        if forwarded_name:
            prefix = f"Forwarded from {forwarded_name}: "
            body = f"{prefix}{body}" if body else prefix.rstrip()

    if not body:
        return None  # image/video-only post with no text -- nothing to cluster on yet

    time_m = _TIME_RE.search(block)
    published_at = _parse_iso(time_m.group(1)) if time_m else None

    link_m = _LINK_RE.search(block)
    url = html.unescape(link_m.group(1)) if link_m else spec.url

    return Item(
        source_id=spec.id,
        url=url,
        title="",  # telegram posts carry no separate title -- body is everything
        body=body,
        published_at=published_at,
        lang=spec.lang,
        raw_hash=hash_raw(entry_bytes),
    )


def collect(spec: SourceSpec, raw: bytes, content_type: str, max_items: int) -> SourceResult:
    # Locate post-block boundaries in the RAW bytes, not decoded text, so
    # each per-post slice hashed below is byte-identical to what the wire
    # sent -- PHASE_3_BRIEF.md lines 44-46. Decoding happens once per post,
    # inside _parse_block, strictly after the slice is taken.
    content_type = content_type or "text/html; charset=utf-8"
    starts = [m.start() for m in _WRAP_START_RE.finditer(raw)]
    raw_entries = len(starts)
    bounds = starts + [len(raw)]

    parsed_items: list[Item] = []
    for i in range(raw_entries):
        entry_bytes = raw[bounds[i]:bounds[i + 1]]
        try:
            item = _parse_block(spec, entry_bytes, content_type)
        except Exception:  # noqa: BLE001 -- one malformed post must never abort the source
            continue
        if item is not None:
            parsed_items.append(item)

    dated = sorted(
        (it for it in parsed_items if it.published_at is not None),
        key=lambda it: it.published_at,
        reverse=True,
    )
    undated = [it for it in parsed_items if it.published_at is None]
    kept_items = (dated + undated)[:max_items]

    return SourceResult(
        source_id=spec.id,
        raw_entries=raw_entries,
        parsed=len(parsed_items),
        kept=len(kept_items),
        items=kept_items,
    )
