"""Dedup layers 1-3: exact URL, normalised URL, normalised title.

Layer 4 (semantic cosine over embeddings) is Phase 6 and is deliberately absent.
Do not add it here and do not import NumPy in this file.

**Layer 2 is the dangerous one, and the danger is live in this project's source
list.** `reuters_gnews` and `ap_gnews` reach Reuters and AP through a Google News
`site:` proxy, so the identity of the article lives inside the URL's own
structure rather than in a clean canonical link. Strip query parameters by
wildcard and every Google News item normalises to the same string: the entire
feed collapses to one story and the failure is invisible, because a dedup engine
that discards too much looks exactly like a quiet news day. Strip nothing and the
same article under two tracking suffixes is counted twice.

So: an explicit allow-list of tracking keys is removed, and **every other
parameter is preserved verbatim**, values included. Never strip by wildcard.
"""

from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

from agent.collectors.base import Item
from agent.memory import models

# Exact keys only. `utm_` is the one prefix match, because the utm_* family is
# open-ended by specification (utm_source, utm_content, utm_id, utm_term, and
# whatever a marketing team invents next Tuesday) -- but it is a NAMED prefix,
# not a wildcard over all parameters.
_TRACKING_KEYS = frozenset(
    {"fbclid", "gclid", "ref", "ref_src", "at_medium", "at_campaign"}
)
_TRACKING_PREFIXES = ("utm_",)


def _is_tracking(pair: str) -> bool:
    key = pair.split("=", 1)[0].lower()
    return key in _TRACKING_KEYS or key.startswith(_TRACKING_PREFIXES)


def normalise_url(url: str) -> str:
    """Lowercase the host, drop the fragment, drop one trailing slash, remove
    allow-listed tracking parameters, preserve everything else in its original
    order and encoding.

    Order is preserved, not sorted. Sorting catches one more duplicate shape but
    rewrites the query string of every proxied URL too, and the cost of being
    wrong on layer 2 is collapsing a whole feed. Not worth the marginal catch."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    query = "&".join(p for p in parts.query.split("&") if p and not _is_tracking(p))
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def normalise_title(title: str) -> str:
    """Casefold, strip Unicode punctuation, collapse whitespace.

    `unicodedata.category`, not `string.punctuation`: three of the four source
    languages are not ASCII. Arabic comma (U+060C), Persian question mark
    (U+061F) and Hebrew maqaf (U+05BE) are all `P*` and none are in ASCII."""
    stripped = "".join(
        " " if unicodedata.category(ch).startswith("P") else ch for ch in title
    )
    return " ".join(stripped.casefold().split())


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def title_hash(title: str) -> str | None:
    """None when the title normalises to nothing.

    An empty string has a perfectly good sha256, and using it would make every
    untitled item -- a Telegram post that is a bare photo, a feed entry whose
    title field is `-` -- collide with every other untitled item and be dropped
    as a duplicate of something it has no relationship to.
    """
    normalised = normalise_title(title)
    return sha256_hex(normalised) if normalised else None


@dataclass(frozen=True, slots=True)
class Hashes:
    url: str
    norm_url: str
    title: str | None


def hashes_for(item: Item) -> Hashes:
    return Hashes(
        url=sha256_hex(item.url.strip()),
        norm_url=sha256_hex(normalise_url(item.url)),
        title=title_hash(item.title),
    )


@dataclass(frozen=True, slots=True)
class DedupResult:
    """Per-layer counts, because a jump in `by_norm_url` against a flat
    `by_url` is what the Google News collapse looks like from outside."""

    new: tuple[Item, ...]
    by_url: int
    by_norm_url: int
    by_title: int

    @property
    def duplicates(self) -> int:
        return self.by_url + self.by_norm_url + self.by_title


def _exists(conn: sqlite3.Connection, sql: str, value: str) -> bool:
    return conn.execute(sql, (value,)).fetchone() is not None


def find_new(conn: sqlite3.Connection, items: Sequence[Item]) -> DedupResult:
    """Filter `items` against stored hashes AND against each other.

    The intra-batch pass matters as much as the stored one: a single run collects
    the same wire story from Reuters, AP and two aggregators simultaneously, so
    without it the first run of a fresh database would emit four copies of
    everything and only be correct from run two onwards.
    """
    new: list[Item] = []
    seen_url: set[str] = set()
    seen_norm: set[str] = set()
    seen_title: set[str] = set()
    by_url = by_norm = by_title = 0

    for item in items:
        h = hashes_for(item)
        if h.url in seen_url or _exists(
            conn, "SELECT 1 FROM seen_urls WHERE url_hash = ?", h.url
        ):
            by_url += 1
            continue
        if h.norm_url in seen_norm or _exists(
            conn, "SELECT 1 FROM seen_urls WHERE norm_url_hash = ? LIMIT 1", h.norm_url
        ):
            by_norm += 1
            continue
        if h.title is not None and (
            h.title in seen_title
            or _exists(conn, "SELECT 1 FROM seen_urls WHERE title_hash = ? LIMIT 1", h.title)
        ):
            by_title += 1
            continue
        seen_url.add(h.url)
        seen_norm.add(h.norm_url)
        if h.title is not None:
            seen_title.add(h.title)
        new.append(item)

    return DedupResult(tuple(new), by_url, by_norm, by_title)


def record_seen(conn: sqlite3.Connection, items: Iterable[Item], now: datetime) -> int:
    rows = []
    for item in items:
        h = hashes_for(item)
        rows.append((h.url, h.norm_url, h.title, item.source_id, models.to_utc_iso(now)))
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR IGNORE INTO seen_urls "
        "(url_hash, norm_url_hash, title_hash, source_id, first_seen_at) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def store_new(conn: sqlite3.Connection, items: Sequence[Item], now: datetime) -> DedupResult:
    """Filter, then persist items and hashes in ONE transaction.

    Not two. If the items write committed and the seen_urls write did not, the
    stories would be stored and then re-sent on the next run -- the precise
    failure this phase exists to prevent, arrived at by a partial success rather
    than an error.
    """
    result = find_new(conn, items)
    conn.execute("BEGIN")
    try:
        models.insert_items(conn, result.new, now)
        record_seen(conn, result.new, now)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return result
