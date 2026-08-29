"""Unit tests for llm/router.py: selection, failover and the breaker. The
budget/cap/vision/reservation tests live in test_llm_router_budget.py
(split for the ~200-line convention). The transport is always mocked, the
clock is always injected -- the router logic itself is what is under test
(PHASE_5_BRIEF §11: a mock that short-circuits the router tests nothing).
"""

from __future__ import annotations

from agent.llm import errors
from agent.llm.errors import FATAL, UNAVAILABLE, LlmResult
from agent.llm.providers import GeminiAdapter, GroqAdapter
from agent.llm.router import Router
from agent.llm.transport import HttpError, HttpTimeout, HttpResponse, MockHttpTransport

GEMINI_URL_PREFIX = "https://generativelanguage.googleapis.com"
GROQ_URL_PREFIX = "https://api.groq.com"

_GEMINI_OK = HttpResponse(
    200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}
)
_GROQ_OK = HttpResponse(
    200, {"choices": [{"message": {"content": "answer"}}], "usage": {}}
)
_SCHEMA_BAD = HttpResponse(200, {"unexpected": "shape"})


def _router(adapters, transport, **kwargs):
    kwargs.setdefault("clock", lambda: 0.0)
    kwargs.setdefault("sleep", lambda s: None)
    return Router(adapters, transport=transport, **kwargs)


def _gemini(key="k" * 16):
    return GeminiAdapter("gemini-2.5-flash", key)


def _groq():
    return GroqAdapter("llama-3.3-70b-versatile", "k" * 16)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_ok_result_carries_provenance_fields():
    transport = MockHttpTransport(responses=[_GEMINI_OK])
    result = _router([_gemini()], transport).complete("hello")
    assert result.ok is True
    assert result.status == errors.OK
    assert result.text == "answer"
    assert result.provider == "gemini"
    assert result.model == "gemini-2.5-flash"
    assert result.call_index == 1
    assert len(result.prompt_hash) == 16


def test_prompt_hash_is_prompt_fingerprint():
    transport = MockHttpTransport(responses=[_GEMINI_OK, _GEMINI_OK])
    router = _router([_gemini()], transport)
    first = router.complete("same prompt")
    second = router.complete("same prompt")
    third = router.complete("different prompt")
    assert first.prompt_hash == second.prompt_hash
    assert first.prompt_hash != third.prompt_hash


def test_call_index_increments_across_calls():
    transport = MockHttpTransport(responses=[_GEMINI_OK, _GEMINI_OK])
    router = _router([_gemini()], transport)
    assert router.complete("a").call_index == 1
    assert router.complete("b").call_index == 2


# ---------------------------------------------------------------------------
# Failover policy (gate 2)
# ---------------------------------------------------------------------------


def test_429_rotates_to_next_provider():
    transport = MockHttpTransport(responses=[HttpResponse(429, {}), _GROQ_OK])
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is True
    assert result.provider == "groq"
    assert len(transport.calls) == 2
    assert GEMINI_URL_PREFIX in transport.calls[0]["url"]
    assert GROQ_URL_PREFIX in transport.calls[1]["url"]


def test_500_rotates_to_next_provider():
    transport = MockHttpTransport(responses=[HttpResponse(500, {}), _GROQ_OK])
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is True
    assert result.provider == "groq"


def test_timeout_rotates_to_next_provider():
    transport = MockHttpTransport(responses=[HttpTimeout, _GROQ_OK])
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is True
    assert result.provider == "groq"


def test_connection_error_rotates_to_next_provider():
    transport = MockHttpTransport(responses=[HttpError, _GROQ_OK])
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is True
    assert result.provider == "groq"


def test_401_does_not_rotate():
    # A bad key fails identically on every provider: rotating turns one
    # visible fault into three wasted calls (PHASE_5_BRIEF §3).
    transport = MockHttpTransport(responses=[HttpResponse(401, {})])
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is False
    assert result.status == FATAL
    assert len(transport.calls) == 1
    assert GEMINI_URL_PREFIX in transport.calls[0]["url"]  # provider 2 never contacted


def test_schema_error_retries_same_provider_then_rotates():
    transport = MockHttpTransport(responses=[_SCHEMA_BAD, _SCHEMA_BAD, _GROQ_OK])
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is True
    assert result.provider == "groq"
    urls = [c["url"] for c in transport.calls]
    assert GEMINI_URL_PREFIX in urls[0]
    assert GEMINI_URL_PREFIX in urls[1]  # retried once on the same provider
    assert GROQ_URL_PREFIX in urls[2]    # then rotated


# ---------------------------------------------------------------------------
# Total collapse and breaker (gate 1, gate 6)
# ---------------------------------------------------------------------------


def test_every_provider_failed_returns_unavailable_no_exception():
    transport = MockHttpTransport(responses=[HttpResponse(500, {})])
    # Mock repeats the last canned response, so every attempt fails.
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert isinstance(result, LlmResult)  # no exception escaped the package
    assert result.ok is False
    assert result.status == UNAVAILABLE


def test_circuit_breaker_opens_and_skips_for_rest_of_run(caplog):
    import logging

    transport = MockHttpTransport(responses=[HttpResponse(500, {})])
    with caplog.at_level(logging.INFO, logger="agent.llm.router"):
        result = _router(
            [_gemini(), _groq()], transport, breaker_threshold=2, max_retries=10
        ).complete("hello")
    assert result.status == UNAVAILABLE
    # Each provider fails twice (opening its breaker), then is never called
    # again -- the breaker is what bounds the attempts.
    gemini_calls = [c for c in transport.calls if GEMINI_URL_PREFIX in c["url"]]
    groq_calls = [c for c in transport.calls if GROQ_URL_PREFIX in c["url"]]
    assert len(gemini_calls) == 2
    assert len(groq_calls) == 2
    assert "circuit breaker open" in caplog.text


def test_attempts_bounded_by_max_retries():
    transport = MockHttpTransport(responses=[HttpResponse(500, {})])
    # breaker_threshold high so only max_retries bounds the loop: 3 + 1 = 4
    # attempts maximum, then UNAVAILABLE.
    result = _router(
        [_gemini(), _groq()], transport, breaker_threshold=99, max_retries=3
    ).complete("hello")
    assert result.status == UNAVAILABLE
    assert len(transport.calls) == 4


def test_backoff_sleep_between_retryable_failures():
    sleeps: list[float] = []
    transport = MockHttpTransport(
        responses=[HttpResponse(429, {}), HttpResponse(429, {}), _GEMINI_OK]
    )
    result = _router([_gemini(), _groq()], transport, sleep=sleeps.append).complete("hello")
    assert result.ok is True
    assert result.provider == "gemini"  # rotation came back around to gemini
    # base 2.0: attempt 1 -> 2.0s, attempt 2 -> 4.0s; success ends the loop.
    assert sleeps == [2.0, 4.0]


# The call-cap, vision-gating, reservation and provider-spend tests live in
# test_llm_router_budget.py (split for the ~200-line convention).
