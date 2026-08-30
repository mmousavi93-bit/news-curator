"""Per-provider HTTP read timeout (2026-08-30 cascade decision).

A dead Gemini cost 60s per attempt at the old DEFAULT_TIMEOUT; at 4
attempts x 2 failing calls the breaker needed ~8 minutes -- most of an
~8-min run. `read_timeout_seconds` (settings.yaml, providers block) now
overrides the read leg per provider: the primary runs 20s, fallbacks keep
the 60s default. Connect stays 10s everywhere.

Covers the whole chain -- settings -> wiring (build_router) -> Router ->
attempt -> transport -- and asserts the REAL value handed to the transport
(MockHttpTransport records it), not an intermediate mapping.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agent.llm.providers import DEFAULT_TIMEOUT, GeminiAdapter, GroqAdapter
from agent.llm.router import Router
from agent.llm.transport import HttpResponse, MockHttpTransport
from agent.llm.wiring import build_router
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"

_GEMINI_OK = HttpResponse(
    200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}
)
_GROQ_OK = HttpResponse(
    200, {"choices": [{"message": {"content": "answer"}}], "usage": {}}
)
_GEMINI_500 = HttpResponse(500, {})

_GEMINI_URL = "https://generativelanguage.googleapis.com"
_GROQ_URL = "https://api.groq.com"


def _settings(read_timeout: int | None) -> Settings:
    raw = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    if read_timeout is not None:
        raw["llm"]["providers"]["gemini"]["read_timeout_seconds"] = read_timeout
    return Settings.from_dict(raw)


def _env() -> dict[str, str]:
    return {"GEMINI_API_KEY": "g" * 16, "GROQ_API_KEY": "r" * 16}


def _timeouts_by_provider(transport: MockHttpTransport) -> dict[str, tuple]:
    # Recorded URLs are the full endpoints; match on the host prefix.
    out: dict[str, tuple] = {}
    for call in transport.calls:
        url = call["url"]
        if url.startswith(_GEMINI_URL):
            out["gemini"] = call["timeout"]
        elif url.startswith(_GROQ_URL):
            out["groq"] = call["timeout"]
    return out


def test_wiring_applies_primary_read_timeout_and_defaults_others():
    transport = MockHttpTransport(responses=[_GEMINI_500, _GROQ_OK, _GEMINI_OK])
    router = build_router(_settings(20).llm, _env(), transport=transport)
    assert router.complete("one").provider == "groq"  # gemini 500 -> rotate
    assert router.complete("two").provider == "gemini"  # recovered
    assert _timeouts_by_provider(transport) == {
        "gemini": (10.0, 20.0),       # override applied
        "groq": DEFAULT_TIMEOUT,      # fallback keeps the default
    }


def test_wiring_without_override_uses_default_everywhere():
    transport = MockHttpTransport(responses=[_GEMINI_OK])
    router = build_router(_settings(None).llm, _env(), transport=transport)
    assert router.complete("x").provider == "gemini"
    assert transport.calls[0]["timeout"] == DEFAULT_TIMEOUT


def test_router_timeout_by_provider_param_reaches_transport():
    transport = MockHttpTransport(responses=[_GEMINI_500, _GROQ_OK])
    router = Router(
        [
            GeminiAdapter("gemini-flash-latest", "k" * 16),
            GroqAdapter("qwen/qwen3.8-27b", "k" * 16),
        ],
        transport=transport,
        timeout_by_provider={"gemini": (10.0, 20.0)},
        clock=lambda: 0.0,
        sleep=lambda s: None,
    )
    result = router.complete("x")
    assert result.provider == "groq"
    assert _timeouts_by_provider(transport) == {
        "gemini": (10.0, 20.0),
        "groq": DEFAULT_TIMEOUT,
    }
