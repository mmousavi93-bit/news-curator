"""fetch.py tests. `requests` is imported lazily inside fetch() -- these
tests inject a fake `requests`-shaped module into sys.modules rather than
requiring the real package or any network, matching the offline discipline
tests/conftest.py's _network_guard enforces for the whole suite.
"""

from __future__ import annotations

import gzip
import sys

import pytest

from agent.collectors import fetch
from agent.collectors.fetch import FetchError, _maybe_gunzip


# ---------------------------------------------------------------------------
# _maybe_gunzip -- pure, no requests involved at all.
# ---------------------------------------------------------------------------

def test_maybe_gunzip_no_content_encoding_returns_body_unchanged():
    body = b"plain bytes"
    out, suffix = _maybe_gunzip(body, None)
    assert out == body
    assert suffix == ""


def test_maybe_gunzip_decodes_a_gzip_body():
    original = b"the quick brown fox"
    compressed = gzip.compress(original)
    out, suffix = _maybe_gunzip(compressed, "gzip")
    assert out == original
    assert suffix == ""


def test_maybe_gunzip_truncated_stream_keeps_raw_bytes_with_reason():
    # Simulates stopping mid-frame at MAX_BYTES: gzip.decompress raises OSError
    # on a truncated stream. Must not raise out of fetch() -- reported as
    # EMPTY-with-a-reason instead of a hard failure.
    truncated = gzip.compress(b"some data" * 100)[:10]
    out, suffix = _maybe_gunzip(truncated, "gzip")
    assert out == truncated
    assert suffix == "; gzip-undecodable"


# ---------------------------------------------------------------------------
# fetch() -- requires a fake `requests` module in sys.modules.
# ---------------------------------------------------------------------------

class _FakeRequestException(Exception):
    pass


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks or [])
        self.closed = False

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        self.closed = True


class _FakeRequestsModule:
    """Minimal stand-in shaped like the real `requests` package: a `.get`
    function and an `.exceptions.RequestException` the real fetch() catches
    by name via `requests.exceptions.RequestException`."""

    def __init__(self, get_fn):
        self.get = get_fn
        self.exceptions = type("exceptions", (), {"RequestException": _FakeRequestException})


@pytest.fixture
def fake_requests(monkeypatch):
    """Installs a fake `requests` module and returns a setter for its `.get`.
    Restores sys.modules afterward so this cannot leak into other tests."""
    had_real = "requests" in sys.modules
    saved = sys.modules.get("requests")

    holder = {}

    def _get(url, headers=None, timeout=None, stream=None):
        return holder["fn"](url, headers=headers, timeout=timeout, stream=stream)

    module = _FakeRequestsModule(_get)
    sys.modules["requests"] = module

    def _set(fn):
        holder["fn"] = fn

    yield _set

    if had_real:
        sys.modules["requests"] = saved
    else:
        sys.modules.pop("requests", None)


def test_fetch_raises_actionable_fetcherror_without_requests_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)
    with pytest.raises(FetchError, match="requests"):
        fetch.fetch("https://example.test/feed", user_agent="ua", timeout_seconds=5.0)


def test_fetch_raises_on_http_error_status(fake_requests):
    fake_requests(lambda *a, **k: _FakeResponse(status_code=404))
    with pytest.raises(FetchError, match="404"):
        fetch.fetch("https://example.test/feed", user_agent="ua", timeout_seconds=5.0)


def test_fetch_raises_on_request_exception(fake_requests):
    def _raise(*a, **k):
        raise _FakeRequestException("connection refused")

    fake_requests(_raise)
    with pytest.raises(FetchError, match="connection refused"):
        fetch.fetch("https://example.test/feed", user_agent="ua", timeout_seconds=5.0)


def test_fetch_streams_and_caps_at_max_bytes(fake_requests, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_BYTES", 20)
    chunks = [b"0123456789"] * 5  # 50 bytes offered, cap is 20
    response = _FakeResponse(status_code=200, headers={"Content-Type": "application/rss+xml"}, chunks=chunks)
    fake_requests(lambda *a, **k: response)

    result = fetch.fetch("https://example.test/feed", user_agent="ua", timeout_seconds=5.0)

    assert len(result.body) <= 20
    assert result.body == b"0123456789" * 2
    assert response.closed  # always closed, even on the capped-early path


def test_fetch_closes_response_even_on_error(fake_requests):
    response = _FakeResponse(status_code=500)
    fake_requests(lambda *a, **k: response)
    with pytest.raises(FetchError):
        fetch.fetch("https://example.test/feed", user_agent="ua", timeout_seconds=5.0)
    assert response.closed


def test_fetch_enforces_wall_deadline_independent_of_between_byte_gaps(fake_requests, monkeypatch):
    # requests' own timeout is BETWEEN bytes -- a server dripping data never
    # trips it. fetch() must enforce its own wall-clock deadline across the
    # whole streamed read. Simulate "time never stops advancing" by making
    # monotonic() jump far past the deadline on the very first check.
    calls = {"n": 0}

    def fake_monotonic():
        calls["n"] += 1
        # First call establishes the deadline; every call after is "later".
        return 0.0 if calls["n"] == 1 else 1_000_000.0

    monkeypatch.setattr(fetch.time, "monotonic", fake_monotonic)
    response = _FakeResponse(status_code=200, chunks=[b"a" * 10, b"b" * 10])
    fake_requests(lambda *a, **k: response)

    with pytest.raises(FetchError, match="wall deadline"):
        fetch.fetch("https://example.test/feed", user_agent="ua", timeout_seconds=5.0)


def test_fetch_decodes_gzip_body_transparently(fake_requests):
    original = b"<rss>gzip round trip</rss>"
    response = _FakeResponse(
        status_code=200,
        headers={"Content-Type": "application/rss+xml", "Content-Encoding": "gzip"},
        chunks=[gzip.compress(original)],
    )
    fake_requests(lambda *a, **k: response)

    result = fetch.fetch("https://example.test/feed", user_agent="ua", timeout_seconds=5.0)
    assert result.body == original


def test_fetch_never_sends_accept_encoding_gzip(fake_requests):
    # A truncated 400KB partial read of a gzip stream cannot be decompressed
    # -- ACCEPT must not advertise gzip support via Accept-Encoding.
    seen = {}

    def _get(url, headers=None, timeout=None, stream=None):
        seen["headers"] = headers
        return _FakeResponse(status_code=200, chunks=[b"ok"])

    fake_requests(_get)
    fetch.fetch("https://example.test/feed", user_agent="ua", timeout_seconds=5.0)
    assert "Accept-Encoding" not in seen["headers"]
