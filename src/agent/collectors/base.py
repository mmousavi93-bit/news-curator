"""Shared shapes and normalisation for every collector: the `Item` a
collector produces, the `SourceSpec` it consumes (one row of
config/sources.yaml), the `SourceResult` it reports back, and the
charset/strip/hash helpers rss.py and telegram_web.py both need.

`Item` fields are deliberately minimal -- source_id, url, title, body,
published_at, lang, raw_hash. Nothing Phase 4 might want yet; adding fields
here now is exactly the scope creep PHASE_3_BRIEF.md warns against.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_WS_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")

_CHARSET_RE = re.compile(r"charset=([\w.-]+)", re.I)
_XML_ENCODING_RE = re.compile(rb'<\?xml[^>]*encoding=["\']([\w.-]+)["\']', re.I)


def strip_html(raw: str) -> str:
    """HTML -> plain text. Scripts/styles/comments dropped whole, remaining
    tags removed, entities unescaped, whitespace collapsed. Requirement 4's
    one carved-out exception -- the Telegram forward-from prefix -- is added
    to `body` by the caller AFTER this runs, not inside it, so it survives
    as plain text rather than being stripped as a tag attribute.

    CDATA sections are unwrapped (content kept, `<![CDATA[`/`]]>` markers
    dropped) BEFORE tag-stripping, not after. `<description>`/
    `content:encoded` wrapped in CDATA is the common case, not an edge
    case -- WordPress and most wire syndication emit it by default. Without
    this, `_TAG_RE` treats the literal "<![CDATA[<p>" run as one tag (its
    own '<' plus the first real tag's '>' close it early) and the trailing
    "]]>" is left dangling in the output with no error raised anywhere.

    Entities are unescaped BEFORE tags are stripped, not after -- the other
    equally common RSS shape is a `<description>` whose HTML is entity-escaped
    rather than CDATA-wrapped (`&lt;p&gt;Body&lt;/p&gt;`). Unescaping after
    tag-removal (the original order) never encounters those tags as real '<'
    '>' characters at all, so they survive stripping and land in `body` as
    literal `<p>`/`</p>` text -- which then reaches the LLM prompt as noise
    and, worse, reaches Telegram's `parse_mode=HTML` sender as attacker- or
    publisher-controlled markup instead of the escaped text it was supposed
    to render as.

    A tag stripped from directly before punctuation (`<b>word</b>.`, the
    common case of markup wrapping the last word of a sentence) leaves a
    dangling space -- "word ." -- once the tag is replaced by a space and
    the true text has no space of its own before the period. Cosmetic, but
    it reads as sloppy in a message the tone contract calls "a calm,
    knowledgeable friend," so it is collapsed as a final pass rather than
    left for Phase 6's composer to notice per-cluster."""
    text = _CDATA_RE.sub(lambda m: m.group(1), raw)
    text = html.unescape(text)
    text = _COMMENT_RE.sub(" ", text)
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)


def resolve_charset(content_type: str, body: bytes) -> str:
    """Precedence, explicit per requirement 4: HTTP Content-Type charset
    param, then the XML prolog `encoding=` attribute, then utf-8. Regional
    CMSes disagree with themselves routinely -- windows-1256 is a live case
    for Arabic sources in this project's feed set -- and a wrong decode is
    silent mojibake with no error raised anywhere, corrupting `body` and
    destabilising anything hashed from the decoded text."""
    match = _CHARSET_RE.search(content_type or "")
    if match:
        return match.group(1)
    match = _XML_ENCODING_RE.search(body[:200])
    if match:
        return match.group(1).decode("ascii", "replace")
    return "utf-8"


def decode_body(content_type: str, body: bytes) -> str:
    charset = resolve_charset(content_type, body)
    try:
        return body.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return body.decode("utf-8", errors="replace")


def hash_raw(entry_bytes: bytes) -> str:
    """Over the raw entry bytes, PRE-normalisation -- not the stripped text.
    Hashing post-strip would mean every future change to the strip rules
    silently changes every stored hash, breaking Phase 4's dedup against
    already-stored rows."""
    return hashlib.sha256(entry_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class Item:
    source_id: str
    url: str
    title: str
    body: str
    published_at: datetime | None
    lang: str
    raw_hash: str
    # True when the source gave a DAY and no time, so published_at is midnight
    # UTC as a placeholder rather than an observation. state_dept_travel is the
    # live case: 95 of 95 items emit 'Wed, 19 Aug 2026' with no time (g1,
    # 2026-08-19). Carried from collection because it CANNOT be recovered
    # later -- 00:00:00Z is indistinguishable from a real midnight publication
    # once the raw string is gone.
    #
    # Two consumers must respect it:
    #  - the composer, which renders Tehran time. 00:00Z is 03:30 IRST, so
    #    printing it as a clock time invents a publication moment that never
    #    happened (hard constraints 10 and 11). Print the date, say the time
    #    was not stated.
    #  - Phase 6's 30-minute near-duplicate window, which would otherwise treat
    #    every same-day advisory from such a feed as simultaneous.
    #
    # Defaulted so existing construction sites and tests stay valid.
    date_only: bool = False


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """One row of config/sources.yaml. registry.py validates the
    credibility join against the FULL list (enabled and disabled) before
    any of these are dispatched -- see registry.validate_join."""

    id: str
    name: str
    url: str
    type: str
    lang: str
    enabled: bool
    max_items: int | None = None
    # Phase 6: general-interest feeds that flood the funnel are keyword-gated
    # by pipeline/filter.py using config/topics.yaml. Reversible reduction,
    # never a deletion (session-5 decision 1).
    topic_gate: bool = False
    # Phase 8: which catalog signals this source can witness (session-5
    # decision 6). Empty for lead sources -- they cannot corroborate.
    signals_covered: tuple[str, ...] = ()


@dataclass
class SourceResult:
    """Per-source outcome. raw_entries/parsed/kept are reported separately
    (requirement 4) so a 200-with-zero-parseable-items feed is
    distinguishable, in the count table, from a genuinely empty one --
    `collection.degraded_after_empty_runs` needs that distinction, and the
    Phase 3 gate needs to tell a cap doing the work apart from a feed doing
    it. `error` is set instead of raising: one bad source must never abort
    the run."""

    source_id: str
    raw_entries: int
    parsed: int
    kept: int
    items: list[Item]
    error: str | None = None


class Collector(Protocol):
    """Structural shape of rss.collect and telegram_web.collect. registry.py
    dispatches to whichever module matches `spec.type` -- see _DISPATCH."""

    def __call__(
        self, spec: SourceSpec, raw: bytes, content_type: str, max_items: int
    ) -> SourceResult: ...
