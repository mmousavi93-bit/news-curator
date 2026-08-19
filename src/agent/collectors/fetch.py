"""HTTP fetch layer shared by every collector.

`requests` is imported lazily, inside fetch(), never at module level --
importing this module (and therefore agent.collectors.registry and
agent.run) must never require the package, matching the pattern already
established in delivery/transport.py's RequestsTransport.

Two probe lessons carried over verbatim from tools/check_feeds.py, because a
source that answered 200 there must answer 200 here too:
  - Never send Accept-Encoding: gzip (a truncated 400KB read of a gzip
    stream can't be decompressed), but decode a body the server gzipped
    anyway without being asked.
  - The byte cap is enforced on a STREAMED read, not on the buffered
    `.content` -- that already reads the whole body before any cap applies.

New in this file, not in the probe (requirement 4): a wall-clock deadline
enforced independently of requests' own timeout. requests' timeout is
BETWEEN bytes, not for the whole request -- a server dripping one byte
every 9 seconds under a 10s timeout never trips it, and per-host
serialisation then queues every sibling source behind that one connection
until GitHub's 6-hour job kill.
"""

from __future__ import annotations

import gzip
import time
import zlib
from dataclasses import dataclass

# Matches tools/check_feeds.py exactly -- same probe, same collector, same cap.
MAX_BYTES = 400_000
ACCEPT = ("application/rss+xml, application/atom+xml, application/xml;q=0.9, "
          "text/xml;q=0.9, text/html;q=0.8, */*;q=0.7")
_CHUNK_SIZE = 8192


class FetchError(Exception):
    """A source could not be fetched -- DNS/TLS/timeout/wall-deadline/non-2xx,
    or `requests` not being installed. registry.py catches this per source
    and records it as a failure; it never aborts the run. Per DECISION 2b, a
    403 or 429 in here is a definitive refusal -- the caller must not retry
    with different headers, a different path, or a mirror."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    status_code: int
    body: bytes
    content_type: str


def _maybe_gunzip(body: bytes, content_encoding: str | None) -> tuple[bytes, str]:
    """Pure and network-free on purpose, so it is unit-testable without
    mocking `requests` at all. Returns (possibly-decompressed body, a
    content-type suffix to append -- empty unless decompression failed)."""
    if "gzip" not in (content_encoding or "").lower():
        return body, ""
    try:
        return gzip.decompress(body), ""
    except (OSError, EOFError, zlib.error):
        # Keep the raw bytes rather than raising -- an undecodable partial body
        # is still worth reporting as EMPTY-with-a-reason, not a hard failure.
        #
        # All three arms are load-bearing; catching only OSError shipped as a
        # real defect and the owner's Windows gate caught it on 2026-08-18:
        #   EOFError   -- truncated stream, i.e. we stopped at MAX_BYTES
        #                 mid-frame. THE primary case this handler exists for,
        #                 and EOFError derives from Exception, NOT OSError.
        #   zlib.error -- corrupt deflate payload; also not an OSError.
        #   OSError    -- gzip.BadGzipFile (bad magic/header, CRC mismatch)
        #                 subclasses it, so this arm still earns its place.
        # Escaping here would propagate out of fetch() as an unexpected type
        # and kill a source on the exact path the 400 KB cap makes routine.
        return body, "; gzip-undecodable"


def fetch(url: str, *, user_agent: str, timeout_seconds: float) -> FetchResult:
    """One GET. Raises FetchError on any failure. No robots.txt fetch here
    (DECISION 2b, resolved 2026-08-18 -- see settings.yaml's
    collection.respect_robots_txt for the full reasoning); this module
    implements none, deliberately."""
    try:
        import requests
    except ImportError as exc:
        raise FetchError(
            "the 'requests' package is required to fetch over the network "
            "(pip install requests); mock mode and the test suite never "
            "call fetch()"
        ) from exc

    headers = {
        "User-Agent": user_agent,
        "Accept": ACCEPT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=timeout_seconds, stream=True)
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc

    try:
        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code}")

        deadline = time.monotonic() + timeout_seconds
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                if time.monotonic() > deadline:
                    raise FetchError(f"wall deadline of {timeout_seconds}s exceeded mid-read")
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_BYTES:
                    break
        except requests.exceptions.RequestException as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc

        body = b"".join(chunks)[:MAX_BYTES]
        content_type = response.headers.get("Content-Type", "")
        body, suffix = _maybe_gunzip(body, response.headers.get("Content-Encoding"))
        return FetchResult(status_code=response.status_code, body=body, content_type=content_type + suffix)
    finally:
        response.close()
