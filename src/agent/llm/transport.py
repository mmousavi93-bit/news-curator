"""The injectable HTTP layer for LLM providers.

Deliberately NOT agent.delivery.transport (PHASE_5_BRIEF §1): coupling
Telegram retry plumbing to LLM calls means a change to one breaks the other.
~40 lines are duplicated on purpose; extracting a shared util/http module is
a separate owner decision, not this phase.

`requests` is imported lazily, inside RequestsHttpTransport, exactly like
delivery/transport.py and collectors/fetch.py: mock mode and the entire test
suite must run with no third-party packages installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


class HttpError(Exception):
    """A request could not complete at all (connection, DNS).

    Messages are deliberately scrubbed: never the URL, never headers, never a
    body. requests' own exception strings embed the URL, which may carry a
    credential (PHASE_5_BRIEF §7), so nothing below re-raises those strings
    verbatim -- this repo's logs are public (CLAUDE.md constraint #9)."""


class HttpTimeout(HttpError):
    """Connect/read timeout. Subclassed so policy could distinguish it from a
    hard connection failure; it does not today -- both are retryable."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: Mapping[str, object]


class HttpTransport(Protocol):
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: tuple[float, float],
    ) -> HttpResponse: ...


class RequestsHttpTransport:
    """The real network. Imports `requests` at construction, not at import
    time, so merely importing this module never requires the package.
    Constructing this class without it installed fails immediately with an
    actionable message (same pattern as delivery/transport.py)."""

    def __init__(self) -> None:
        try:
            import requests
        except ImportError as exc:
            raise ImportError(
                "RequestsHttpTransport needs the 'requests' package for a real "
                "LLM call (pip install requests); mock mode does not need it, "
                "so this only matters when actually calling a provider."
            ) from exc
        self._requests = requests

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: tuple[float, float],
    ) -> HttpResponse:
        try:
            response = self._requests.post(
                url, headers=dict(headers), json=dict(payload), timeout=timeout
            )
        except self._requests.exceptions.Timeout as exc:
            raise HttpTimeout(f"timeout after {timeout[1]}s") from exc
        except self._requests.exceptions.ConnectionError as exc:
            raise HttpError("connection failed") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return HttpResponse(status_code=response.status_code, body=body)


@dataclass
class MockHttpTransport:
    """Records every call; returns canned responses in order (the last canned
    entry repeats once exhausted). A canned entry that is an Exception
    subclass is raised instead, with a message that embeds the request URL --
    the same way real transports leak URLs into exception text, so a
    redaction test on this path proves the router scrubs rather than passing
    trivially (delivery/transport.py's MockTransport sets the same trap)."""

    responses: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: tuple[float, float],
    ) -> HttpResponse:
        self.calls.append({"url": url, "headers": dict(headers), "payload": dict(payload)})
        if not self.responses:
            return HttpResponse(status_code=200, body={})
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        canned = self.responses[index]
        if isinstance(canned, type) and issubclass(canned, Exception):
            raise canned(f"mock transport: simulated network failure calling {url}")
        return canned
