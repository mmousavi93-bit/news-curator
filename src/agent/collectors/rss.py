"""RSS/Atom parsing via regex over raw bytes -- not xml.etree, deliberately.

Isolating one <item>/<entry> fragment (needed to hash a byte-precise
per-entry slice for raw_hash) with ElementTree raises ParseError on any
namespace prefix (dc:, content:, media:) declared only on the document
root, and several feeds in this project's source set use exactly that.
tools/check_feeds.py already proves the regex approach survives against
these same 51 URLs, so this mirrors it rather than reinventing parsing.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from agent.collectors.base import Item, SourceResult, SourceSpec, decode_body, hash_raw, strip_html

_ENTRY_RE = re.compile(rb"<(item|entry)[\s>].*?</\1\s*>", re.I | re.S)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DESC_RE = re.compile(
    r"<(description|summary|content:encoded|content)[^>]*>(.*?)</\1>", re.I | re.S
)
_DATE_RE = re.compile(r"<(pubDate|published|updated|dc:date)[^>]*>(.*?)</\1>", re.I | re.S)
_LINK_HREF_RE = re.compile(r"<link[^>]+href=[\"']([^\"']+)[\"']", re.I)
_LINK_TEXT_RE = re.compile(r"<link[^>]*>([^<]*)</link>", re.I)

# Ynet's date has no RFC-822/ISO form the probe's DATE_RE (or
# email.utils.parsedate_to_datetime) matches at all -- both ynet and
# ynet_he return items with an empty parsed date today. Best-effort format,
# UNVERIFIED against a live fetch (sandbox has no network egress): a
# no-timezone "M/D/YYYY H:MM:SS AM/PM" reading as Israel local time. The
# month-based +2/+3 split below is a deliberate approximation, not a real
# DST calendar -- zoneinfo/tzdata is not an approved dependency (not
# guaranteed present on the owner's clean Windows Python), so this only
# risks mis-bucketing items published within a few days of the actual
# IST/IDT transition, not silently fabricating a date.
_YNET_DATE_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?", re.I
)


def _israel_utc_offset_hours(month: int) -> int:
    return 3 if 4 <= month <= 9 else 2


def _parse_date(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        # A trailing 'Z' is the canonical Atom-spec example
        # (<updated>2003-12-13T18:30:02Z</updated>) and is common on RSS
        # dc:date too. datetime.fromisoformat() only accepts it from
        # Python 3.11 -- normalise it explicitly rather than depending on
        # the interpreter version the collector happens to run under.
        iso_text = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        dt = datetime.fromisoformat(iso_text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    match = _YNET_DATE_RE.match(text)
    if match:
        month, day, year, hour, minute, second, ampm = match.groups()
        hour_i = int(hour)
        if ampm:
            if ampm.upper() == "PM" and hour_i != 12:
                hour_i += 12
            elif ampm.upper() == "AM" and hour_i == 12:
                hour_i = 0
        try:
            naive = datetime(int(year), int(month), int(day), hour_i, int(minute), int(second))
        except ValueError:
            return None
        offset = _israel_utc_offset_hours(naive.month)
        return (naive - timedelta(hours=offset)).replace(tzinfo=timezone.utc)
    return None


def _extract_link(text: str) -> str | None:
    match = _LINK_HREF_RE.search(text)
    if match:
        return match.group(1).strip()
    match = _LINK_TEXT_RE.search(text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return None


def _parse_entry(spec: SourceSpec, entry_bytes: bytes, content_type: str) -> Item | None:
    text = decode_body(content_type, entry_bytes)

    title_m = _TITLE_RE.search(text)
    title = strip_html(title_m.group(1)) if title_m else ""

    desc_m = _DESC_RE.search(text)
    body = strip_html(desc_m.group(2)) if desc_m else ""

    if not title and not body:
        return None  # nothing usable parsed out of this entry -- counted, not raised

    date_m = _DATE_RE.search(text)
    published_at = _parse_date(date_m.group(2)) if date_m else None

    url = _extract_link(text) or spec.url

    return Item(
        source_id=spec.id,
        url=url,
        title=title,
        body=body,
        published_at=published_at,
        lang=spec.lang,
        raw_hash=hash_raw(entry_bytes),
    )


def collect(spec: SourceSpec, raw: bytes, content_type: str, max_items: int) -> SourceResult:
    matches = list(_ENTRY_RE.finditer(raw))
    raw_entries = len(matches)

    parsed_items: list[Item] = []
    for match in matches:
        entry_bytes = match.group(0)
        try:
            item = _parse_entry(spec, entry_bytes, content_type)
        except Exception:  # noqa: BLE001 -- one bad entry must never abort the source
            continue
        if item is not None:
            parsed_items.append(item)

    # Truncate by published_at desc where dates exist -- feeds in this set
    # are not reliably date-sorted (the IranWire DATE_RE lesson: "first N of
    # 87" can drop today's item and keep a stale one). Undated items sort
    # last, in original feed order.
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
