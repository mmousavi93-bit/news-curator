"""Redaction gate for the LLM layer (PHASE_5_BRIEF gate 6): no key in any
log line or exception, no prompt, no response body -- even when a simulated
provider error echoes the request URL back verbatim. The repo is public;
the value-based redaction filter in util/logging.py is the net, these tests
are the proof the net is threaded through the router.
"""

from __future__ import annotations

import logging

from agent.llm.router import Router
from agent.llm.transport import HttpError, HttpResponse, MockHttpTransport
from agent.util.logging import PROCESS_FILTER, get_logger

# 40 chars: well above the filter's _MIN_SECRET_LEN floor of 16.
SECRET_KEY = "LLM-SECRET-KEY-0123456789abcdef-0123456789"
DISTINCT_PROMPT = "PROMPT-CANARY-9f8e7d6c5b4a39281726"
DISTINCT_BODY = "BODY-CANARY-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class _LeakyQueryAdapter:
    """A provider adapter that puts the key in the query string -- the exact
    forbidden pattern (PHASE_5_BRIEF §7). Exists only so a test can prove
    the redaction net catches a regression that reintroduces ?key=."""

    name = "leaky"
    supports_vision = True
    model = "leaky-1"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def build_request(self, prompt, images):
        url = f"https://example.com/v1/chat?key={self._api_key}"
        return url, {}, {"prompt": prompt}

    def parse(self, body):
        return body.get("text", ""), {}


def _capture(caplog, level=logging.DEBUG):
    caplog.at_level(level, logger="agent.llm.router")
    return caplog


def test_key_in_exception_message_is_redacted(caplog):
    PROCESS_FILTER.register(SECRET_KEY)
    transport = MockHttpTransport(responses=[HttpError])  # message embeds the URL
    router = Router([_LeakyQueryAdapter(SECRET_KEY)], transport=transport,
                    clock=lambda: 0.0, sleep=lambda s: None)
    with _capture(caplog):
        result = router.complete("hello")
    assert result.ok is False
    assert SECRET_KEY not in caplog.text
    assert "REDACTED" in caplog.text  # the filter fired, it did not just miss


def test_key_never_logged_on_success_path(caplog):
    PROCESS_FILTER.register(SECRET_KEY)
    transport = MockHttpTransport(responses=[
        HttpResponse(200, {"candidates": [{"content": {"parts": [{"text": DISTINCT_BODY}]}}]})
    ])
    router = Router([_LeakyQueryAdapter(SECRET_KEY)], transport=transport,
                    clock=lambda: 0.0, sleep=lambda s: None)
    with _capture(caplog):
        router.complete(DISTINCT_PROMPT)
    assert SECRET_KEY not in caplog.text


def test_prompt_never_logged(caplog):
    transport = MockHttpTransport(responses=[
        HttpResponse(200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]})
    ])
    router = Router([_LeakyQueryAdapter(SECRET_KEY)], transport=transport,
                    clock=lambda: 0.0, sleep=lambda s: None)
    with _capture(caplog):
        router.complete(DISTINCT_PROMPT)
    assert DISTINCT_PROMPT not in caplog.text


def test_response_body_never_logged(caplog):
    transport = MockHttpTransport(responses=[
        HttpResponse(200, {"candidates": [{"content": {"parts": [{"text": DISTINCT_BODY}]}}]})
    ])
    router = Router([_LeakyQueryAdapter(SECRET_KEY)], transport=transport,
                    clock=lambda: 0.0, sleep=lambda s: None)
    with _capture(caplog):
        result = router.complete("hello")
    assert result.ok is True
    assert DISTINCT_BODY not in caplog.text


def test_fatal_error_path_never_logs_body(caplog):
    body = {"error": {"message": DISTINCT_BODY}}
    transport = MockHttpTransport(responses=[HttpResponse(401, body)])
    router = Router([_LeakyQueryAdapter(SECRET_KEY)], transport=transport,
                    clock=lambda: 0.0, sleep=lambda s: None)
    with _capture(caplog):
        result = router.complete("hello")
    assert result.ok is False
    assert DISTINCT_BODY not in caplog.text


def test_structured_log_line_has_no_header_names(caplog):
    # Headers carry the credential; the structured log format must never
    # mention them at all (PHASE_5_BRIEF §8: bodiless logging).
    transport = MockHttpTransport(responses=[
        HttpResponse(200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]})
    ])
    router = Router([_LeakyQueryAdapter(SECRET_KEY)], transport=transport,
                    clock=lambda: 0.0, sleep=lambda s: None)
    with _capture(caplog):
        router.complete("hello")
    assert "Authorization" not in caplog.text
    assert "x-goog-api-key" not in caplog.text


def test_router_logger_has_redaction_filter_attached():
    # The one thing every other test here leans on: the router's logger is
    # obtained via get_logger(), which attaches PROCESS_FILTER.
    logger = get_logger("agent.llm.router")
    assert any(isinstance(f, type(PROCESS_FILTER)) for f in logger.filters)
