"""The injectable HTTP transport for TelegramClient: the real `requests`
transport, and a mock that records calls and returns canned responses.
Split out of telegram.py to keep that file under the ~200-line limit
(CLAUDE.md constraint #12) -- this is I/O plumbing only, no retry policy.

`requests` is imported lazily, inside RequestsTransport, not at module
level. Mock mode and the whole test suite must run with no third-party
packages installed at all -- importing this module (which telegram.py does
unconditionally) must never require requests to be present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


class TransportError(Exception):
    """Raised by a Transport when a send could not reach the server at all
    -- connection failure, DNS failure, timeout. Owned by the delivery
    layer, not by whichever HTTP library a Transport wraps: TelegramClient's
    retry logic catches this one type and backs off, and must never import
    (or know about) requests, aiohttp, urllib3, or anything else underneath."""


class TransportTimeout(TransportError):
    """A TransportError specifically caused by exceeding the connect/read
    timeout. Kept as its own subclass in case retry policy ever needs to
    treat a timeout differently from a hard connection failure -- it does
    not today, so catching TransportError alone still catches this."""


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    body: Mapping[str, object]


class Transport(Protocol):
    def post(
        self, url: str, json: Mapping[str, object], timeout: tuple[float, float]
    ) -> TransportResponse: ...


class RequestsTransport:
    """The real network transport. Only exercised when a token is present.
    Imports `requests` at construction time, not at module import time, so
    merely importing this module -- which every test and --dry-run do --
    never requires the package. Constructing this class without it
    installed fails immediately with an actionable message instead of a
    bare ModuleNotFoundError surfacing later out of .post()."""

    def __init__(self) -> None:
        try:
            import requests
        except ImportError as exc:
            raise ImportError(
                "RequestsTransport needs the 'requests' package for a real "
                "Telegram send (pip install requests); mock mode does not "
                "need it, so this only matters when actually sending."
            ) from exc
        self._requests = requests

    def post(
        self, url: str, json: Mapping[str, object], timeout: tuple[float, float]
    ) -> TransportResponse:
        try:
            response = self._requests.post(url, json=json, timeout=timeout)
        except self._requests.exceptions.Timeout as exc:
            raise TransportTimeout(str(exc)) from exc
        except self._requests.exceptions.ConnectionError as exc:
            raise TransportError(str(exc)) from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        # Valid JSON that isn't an object (a list, null, string, number) is
        # not a dict -- callers assume Mapping-like .get() access downstream.
        if not isinstance(body, dict):
            body = {}
        return TransportResponse(status_code=response.status_code, body=body)


@dataclass
class MockTransport:
    """Records every call and returns canned responses in order (the last
    canned entry repeats once exhausted). A canned entry that is an
    Exception subclass is raised instead of returned -- how tests simulate
    a TransportTimeout or TransportError without any real socket, and
    without requests needing to be installed."""

    responses: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def post(
        self, url: str, json: Mapping[str, object], timeout: tuple[float, float]
    ) -> TransportResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        canned = self.responses[index]
        if isinstance(canned, type) and issubclass(canned, Exception):
            # Embed the real request URL (which contains the bot token) in
            # the exception text, the same way RequestsTransport's own
            # translation preserves requests/urllib3's URL-bearing message
            # -- otherwise a redaction test on this path would pass
            # trivially without proving anything.
            raise canned(f"mock transport: simulated network failure calling {url}")
        return canned
