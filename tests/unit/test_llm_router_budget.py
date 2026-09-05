"""Router budget tests: the call cap (gate 3), vision gating (gate 5),
reservations (priority-ordered spending) and the provider spend guard
(constraint #15, gate 7). Split out of test_llm_router.py, which holds
selection/failover/breaker. Transport mocked, clock injected throughout.
"""

from __future__ import annotations

import logging

from agent.llm.errors import REFUSED_CAP, UNAVAILABLE
from agent.llm.limits import ProviderBudget
from agent.llm.providers import GeminiAdapter, GroqAdapter, ImageInput
from agent.llm.router import Router
from agent.llm.transport import HttpResponse, MockHttpTransport

GEMINI_URL_PREFIX = "https://generativelanguage.googleapis.com"

_GEMINI_OK = HttpResponse(
    200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}
)
_GROQ_OK = HttpResponse(
    200, {"choices": [{"message": {"content": "answer"}}], "usage": {}}
)


def _router(adapters, transport, **kwargs):
    kwargs.setdefault("clock", lambda: 0.0)
    kwargs.setdefault("sleep", lambda s: None)
    return Router(adapters, transport=transport, **kwargs)


def _gemini(key="k" * 16):
    return GeminiAdapter("gemini-2.5-flash", key)


def _groq():
    return GroqAdapter("llama-3.3-70b-versatile", "k" * 16)


# ---------------------------------------------------------------------------
# Call cap (gate 3)
# ---------------------------------------------------------------------------


def test_cap_refuses_call_n_plus_1_before_request_is_built():
    transport = MockHttpTransport(responses=[_GEMINI_OK])
    router = _router([_gemini()], transport, max_calls=2)
    assert router.complete("a").ok is True
    assert router.complete("b").ok is True
    refused = router.complete("c")
    assert refused.ok is False
    assert refused.status == REFUSED_CAP
    assert len(transport.calls) == 2  # refused BEFORE a request was built
    # The run continues: a fourth call is refused again, not raised.
    assert router.complete("d").status == REFUSED_CAP


def test_cap_refusal_logged_once(caplog):
    transport = MockHttpTransport(responses=[_GEMINI_OK])
    router = _router([_gemini()], transport, max_calls=1)
    with caplog.at_level(logging.ERROR, logger="agent.llm.router"):
        router.complete("a")
        router.complete("b")
        router.complete("c")
    refusals = [r for r in caplog.records if "call refused" in r.getMessage()]
    assert len(refusals) == 1


# ---------------------------------------------------------------------------
# Vision gating (gate 5)
# ---------------------------------------------------------------------------


def test_see_refuses_text_only_providers_structurally():
    transport = MockHttpTransport(responses=[_GROQ_OK])
    image = ImageInput(mime_type="image/png", data_b64="aGk=")
    result = _router([_groq()], transport).see("what is this", [image])
    assert result.ok is False
    assert result.status == UNAVAILABLE
    assert transport.calls == []  # nothing was ever attempted


def test_see_filters_to_vision_capable_provider():
    transport = MockHttpTransport(responses=[_GEMINI_OK])
    image = ImageInput(mime_type="image/png", data_b64="aGk=")
    result = _router([_groq(), _gemini()], transport).see("what is this", [image])
    assert result.ok is True
    assert result.provider == "gemini"
    assert len(transport.calls) == 1
    assert GEMINI_URL_PREFIX in transport.calls[0]["url"]
    # The image actually reached the payload.
    parts = transport.calls[0]["payload"]["contents"][0]["parts"]
    assert any("inline_data" in p for p in parts)


def test_see_without_images_makes_no_call():
    transport = MockHttpTransport()
    result = _router([_gemini()], transport).see("nothing", [])
    assert result.status == UNAVAILABLE
    assert transport.calls == []


# ---------------------------------------------------------------------------
# Reservations (priority-ordered spending)
# ---------------------------------------------------------------------------


def test_reservation_lets_compose_run_after_budget_exhausted():
    transport = MockHttpTransport(responses=[_GEMINI_OK])
    router = _router([_gemini()], transport, max_calls=3)
    assert router.reserve(1, "compose") is True
    assert router.complete("cluster 1", stage="understand").ok is True
    assert router.complete("cluster 2", stage="understand").ok is True
    # The shared pot is empty but the reserved slot exists.
    composed = router.complete("digest", stage="compose", use_reservation="compose")
    assert composed.ok is True
    assert len(transport.calls) == 3


def test_reserved_slot_used_twice_is_refused():
    transport = MockHttpTransport(responses=[_GEMINI_OK])
    router = _router([_gemini()], transport, max_calls=2)
    router.reserve(1, "compose")
    first = router.complete("x", stage="compose", use_reservation="compose")
    second = router.complete("y", stage="compose", use_reservation="compose")
    assert first.ok is True
    assert second.status == REFUSED_CAP


# ---------------------------------------------------------------------------
# Provider spend guard (constraint #15, gate 7)
# ---------------------------------------------------------------------------


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


def test_daily_exhausted_provider_skipped_to_next():
    # gemini already spent its 20/day; the router must skip it (without a
    # request) and serve from groq instead of returning UNAVAILABLE.
    log = _Log()
    transport = MockHttpTransport(responses=[_GROQ_OK])
    limits = {
        "gemini": ProviderBudget(
            name="gemini",
            max_calls_per_run=None,
            max_spend_usd=None,
            halt_on_exceeded=False,
            input_usd_per_mtok=0.0,
            output_usd_per_mtok=0.0,
            max_calls_per_day=20,
            daily_calls_used=20,  # today's quota fully spent
            logger=log,
        ),
    }
    router = _router([_gemini(), _groq()], transport, provider_limits=limits)
    result = router.complete("a")
    assert result.ok is True
    assert result.provider == "groq"
    assert len(transport.calls) == 1  # gemini was never attempted
    assert any("daily quota (20) reached" in m for m in log.messages)


def test_metered_provider_halt_stops_further_calls_to_it():
    log = _Log()
    transport = MockHttpTransport(responses=[
        HttpResponse(200, {
            "candidates": [{"content": {"parts": [{"text": "answer"}]}}],
            "usageMetadata": {"promptTokenCount": 1_000_000,
                              "candidatesTokenCount": 1_000_000,
                              "totalTokenCount": 2_000_000},
        }),
    ])
    limits = {
        "gemini": ProviderBudget(
            name="gemini",
            max_calls_per_run=None,
            max_spend_usd=5.0,  # 1M/1M tokens at $1/$5 = $6.00 -> over
            halt_on_exceeded=True,
            input_usd_per_mtok=1.0,
            output_usd_per_mtok=5.0,
            logger=log,
        ),
    }
    router = _router([_gemini()], transport, provider_limits=limits)
    first = router.complete("a")
    assert first.ok is True
    assert any("budget halt" in m for m in log.messages)
    second = router.complete("b")
    assert second.status == UNAVAILABLE
    assert len(transport.calls) == 1  # the halt stopped the second request
