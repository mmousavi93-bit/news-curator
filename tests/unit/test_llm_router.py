"""Unit tests for llm/router.py: selection, failover and the breaker. The
budget/cap/vision/reservation tests live in test_llm_router_budget.py
(split for the ~200-line convention). The transport is always mocked, the
clock is always injected -- the router logic itself is what is under test
(PHASE_5_BRIEF §11: a mock that short-circuits the router tests nothing).
"""

from __future__ import annotations

from agent.llm import errors
from agent.llm.errors import FATAL, UNAVAILABLE, LlmResult
from agent.llm.providers import BaiAdapter, GeminiAdapter, GroqAdapter
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


def _bai():
    return BaiAdapter("qwen3.8-flash", "k" * 16)


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


def test_stats_count_attempts_per_provider():
    # 2026-08-30: run.csv gains calls_<provider>/fails_<provider> so the
    # owner watches bai's ceiling and Gemini's degradation from artifacts.
    transport = MockHttpTransport(responses=[HttpResponse(429, {}), _GROQ_OK])
    router = _router([_gemini(), _groq()], transport)
    result = router.complete("hello")
    assert result.ok is True
    assert router.stats.as_dict() == {
        "gemini": {"calls": 1, "failed": 1},   # the 429 attempt
        "groq": {"calls": 1, "failed": 0},     # the carrying attempt
    }


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


def test_provider_fatal_rotates_to_next_provider():
    # REVISED 2026-09-05. The old rule ("a 4xx fails identically on every
    # provider, so stop") was falsified in production: bai_deepseek 400'd
    # and openrouter 403'd on prompts groq and gemini answered seconds
    # later, and the early return destroyed three clusters -- one of them
    # the run's most-corroborated story (7 members, 5 independent sources).
    # A 4xx is a PROVIDER fact. Rotate; the breaker bounds the waste.
    transport = MockHttpTransport(responses=[HttpResponse(400, {}), _GROQ_OK])
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is True
    assert result.provider == "groq"
    assert len(transport.calls) == 2


def test_all_providers_fatal_surfaces_fatal_status():
    # When every candidate is fatal the caller must still learn WHY, so the
    # fate column reads "fatal" rather than a generic "unavailable".
    transport = MockHttpTransport(
        responses=[HttpResponse(400, {}), HttpResponse(403, {})]
    )
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is False
    assert result.status == FATAL


def test_fatal_provider_is_not_retried_within_the_same_call():
    # Rotating must not mean re-queueing: a malformed request does not
    # become well-formed on a retry. Gemini gets exactly one shot.
    transport = MockHttpTransport(
        responses=[HttpResponse(400, {}), HttpResponse(500, {}), _GROQ_OK]
    )
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is True
    gemini_calls = [c for c in transport.calls if GEMINI_URL_PREFIX in c["url"]]
    assert len(gemini_calls) == 1


def test_404_rotates_to_next_provider():
    # "This provider does not have this model" is provider-SPECIFIC -- each
    # provider has its own model id. Learned 2026-08-29: a discontinued
    # gemini model 404'd every call and the old fatal treatment cost a full
    # run while Groq sat unused.
    transport = MockHttpTransport(responses=[HttpResponse(404, {}), _GROQ_OK])
    result = _router([_gemini(), _groq()], transport).complete("hello")
    assert result.ok is True
    assert result.provider == "groq"
    assert len(transport.calls) == 2


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


def test_fatal_responses_open_the_breaker_and_stop_reburning():
    # 2026-08-30: a 403-blocked OpenRouter was called once per remaining
    # cluster (34 identical fatal calls). The fatal RESULT still stops the
    # loop per call; the breaker must stop the PROVIDER after two.
    transport = MockHttpTransport(responses=[HttpResponse(403, {})])
    router = _router([_gemini()], transport, breaker_threshold=2, max_retries=10)
    first = router.complete("a")
    second = router.complete("b")
    third = router.complete("c")
    assert first.status == FATAL  # the first call surfaces the diagnosis
    assert second.status == FATAL  # breaker opens on this one
    assert third.status == UNAVAILABLE  # skipped, not re-burned
    assert len(transport.calls) == 2


def test_breaker_skip_logged_once_per_provider_per_run(caplog):
    import logging

    transport = MockHttpTransport(responses=[HttpResponse(500, {})])
    router = _router(
        [_gemini(), _groq()], transport, breaker_threshold=2, max_retries=10
    )
    with caplog.at_level(logging.INFO, logger="agent.llm.router"):
        router.complete("first")   # both breakers open here
        router.complete("second")  # both skipped again -- silently this time
    # One line per provider for the whole run, not one line per cluster
    # (2026-08-30: 27 clusters produced 54 identical lines).
    assert caplog.text.count("circuit breaker open") == 2


def test_429_cools_provider_and_rotates_to_ready_one():
    # 2026-08-31: retrying into a per-minute token wall burned 16 calls in
    # one run. A 429'd provider rests; others serve meanwhile.
    transport = MockHttpTransport(responses=[HttpResponse(429, {}), _GROQ_OK, _GROQ_OK])
    clock = {"t": 0.0}
    router = _router([_gemini(), _groq()], transport, clock=lambda: clock["t"])
    assert router.complete("a").provider == "groq"  # gemini 429 -> cooled
    assert router.complete("b").provider == "groq"  # gemini still cooling
    gemini_calls = [c for c in transport.calls if GEMINI_URL_PREFIX in c["url"]]
    assert len(gemini_calls) == 1  # not re-burned while cooling


def test_all_providers_cooling_proceeds_anyway():
    # Spinning forever is worse than hitting the wall once: when the ONLY
    # provider is cooling, the loop retries it anyway after backoff -- a
    # run with just groq alive must not give up on one 429.
    transport = MockHttpTransport(responses=[HttpResponse(429, {}), _GEMINI_OK])
    clock = {"t": 0.0}
    router = _router([_gemini()], transport, clock=lambda: clock["t"])
    result = router.complete("a")
    assert result.ok is True
    assert result.provider == "gemini"
    assert len(transport.calls) == 2  # 429, then the retry


def test_cooldown_expires_with_clock():
    transport = MockHttpTransport(responses=[HttpResponse(429, {}), _GROQ_OK, _GEMINI_OK])
    clock = {"t": 0.0}
    router = _router([_gemini(), _groq()], transport, clock=lambda: clock["t"])
    assert router.complete("a").provider == "groq"   # gemini 429 -> cooled
    clock["t"] = 31.0
    assert router.complete("b").provider == "gemini"  # cooled off -> serves
    gemini_calls = [c for c in transport.calls if GEMINI_URL_PREFIX in c["url"]]
    assert len(gemini_calls) == 2  # once at t=0, once after expiry


def test_stage_unavailable_logged_once_per_run(caplog):
    import logging

    transport = MockHttpTransport(responses=[HttpResponse(500, {})])
    router = _router(
        [_gemini(), _groq()], transport, breaker_threshold=2, max_retries=10
    )
    with caplog.at_level(logging.INFO, logger="agent.llm.router"):
        router.complete("first")   # both breakers open; final line logged
        router.complete("second")  # identical outcome; line suppressed
    # 2026-08-30: 14 identical "unavailable after 0 attempt(s)" lines.
    assert caplog.text.count("unavailable after") == 1


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
    # 429 is special-cased (cooldown sleep, same-provider retry) since
    # 2026-09-05; 500 is the retryable failure that exercises backoff.
    transport = MockHttpTransport(
        responses=[HttpResponse(500, {}), HttpResponse(500, {}), _GEMINI_OK]
    )
    result = _router([_gemini(), _groq()], transport, sleep=sleeps.append).complete("hello")
    assert result.ok is True
    assert result.provider == "gemini"  # rotation came back around to gemini
    # base 2.0: attempt 1 -> 2.0s, attempt 2 -> 4.0s; success ends the loop.
    assert sleeps == [2.0, 4.0]


def test_consecutive_429s_do_not_open_the_breaker():
    # 2026-08-31 09:36 run: Groq's breaker opened over TWO pacing 429s
    # and the cascade collapsed into 24 skipped clusters. A 429 means
    # "slow down", not "broken" -- the breaker must not judge on it.
    # With one provider: two 429s in a row would open a breaker that
    # counts them, and the third attempt would return UNAVAILABLE.
    transport = MockHttpTransport(
        responses=[HttpResponse(429, {}), HttpResponse(429, {}), _GEMINI_OK]
    )
    result = _router([_gemini()], transport, breaker_threshold=2).complete("hello")
    assert result.ok is True
    assert result.provider == "gemini"


def test_429_retries_same_provider_after_cooldown():
    # 2026-09-05: a 429 means "slow down, I'll be back", not "hand the
    # cluster to the next provider". The 2026-09-05 run wasted its cascade
    # on this -- Groq (the only healthy provider) walled, and each 429
    # rotated into bai/gemini/openrouter, which 502/503/403'd, dropping the
    # cluster while Groq sat cooling. Now a 429 retries the SAME provider
    # after one cooldown wait, before any rotation.
    sleeps: list[float] = []
    clock = {"t": 0.0}

    def advance(s: float) -> None:
        sleeps.append(s)
        clock["t"] += s

    transport = MockHttpTransport(responses=[HttpResponse(429, {}), _GEMINI_OK])
    router = _router([_gemini()], transport, clock=lambda: clock["t"], sleep=advance)
    result = router.complete("hello")
    assert result.ok is True
    assert result.provider == "gemini"  # the SAME provider, not a rotation
    assert len(transport.calls) == 2
    assert all(GEMINI_URL_PREFIX in c["url"] for c in transport.calls)
    assert sleeps == [30.0]  # one cooldown wait, then the same-provider retry


def test_429_does_not_starve_ready_downstream_provider():
    # 2026-09-05 review regression: the always-retry-same-provider form of
    # the 429 fix burned the whole attempt budget on a walled mid-cascade
    # provider (Groq) while a HEALTHY one (bai) sat idle. A 429 retries the
    # same provider ONLY when no ready alternative is waiting; here bai is
    # ready, so the walled groq rotates and bai serves.
    transport = MockHttpTransport(responses=[
        HttpResponse(503, {}),   # gemini overload -> rotate
        HttpResponse(429, {}),   # groq token wall -> rotate (bai ready)
        _GROQ_OK,                # bai serves (OpenAI shape)
    ])
    result = _router([_gemini(), _groq(), _bai()], transport).complete("hello")
    assert result.ok is True
    assert result.provider == "bai"
    assert len(transport.calls) == 3
    # bai's own URL, not another groq retry.
    assert "api.b.ai" in transport.calls[2]["url"]


# The call-cap, vision-gating, reservation and provider-spend tests live in
# test_llm_router_budget.py (split for the ~200-line convention).
