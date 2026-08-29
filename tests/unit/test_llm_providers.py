"""Unit tests for llm/providers.py: request shapes, response parsing and
adapter construction. No HTTP anywhere -- every assertion is on the
(url, headers, payload) triple or on a canned body dict.
"""

from __future__ import annotations

import pytest

from agent.llm.errors import SchemaError
from agent.llm.providers import (
    API_KEY_ENV,
    GeminiAdapter,
    GroqAdapter,
    ImageInput,
    OpenRouterAdapter,
)
from agent.llm.wiring import build_adapters
from agent.settings_llm import ProviderSettings

_GEMINI_OK = {
    "candidates": [{"content": {"parts": [{"text": "hello from gemini"}]}}],
    "usageMetadata": {
        "promptTokenCount": 7,
        "candidatesTokenCount": 3,
        "totalTokenCount": 10,
    },
}
_OPENAI_OK = {
    "choices": [{"message": {"content": "hello from groq"}}],
    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
}


# ---------------------------------------------------------------------------
# GeminiAdapter
# ---------------------------------------------------------------------------


def test_gemini_key_travels_in_header_never_query_string():
    adapter = GeminiAdapter("gemini-2.5-flash", "SUPERSECRETKEY123456")
    url, headers, _payload = adapter.build_request("prompt", [])
    assert "?key=" not in url
    assert "SUPERSECRETKEY123456" not in url
    assert headers["x-goog-api-key"] == "SUPERSECRETKEY123456"


def test_gemini_temperature_is_zero():
    _, _, payload = GeminiAdapter("gemini-2.5-flash", "k" * 16).build_request("p", [])
    assert payload["generationConfig"]["temperature"] == 0.0


def test_gemini_prompt_is_the_only_text_part():
    _, _, payload = GeminiAdapter("gemini-2.5-flash", "k" * 16).build_request("p", [])
    parts = payload["contents"][0]["parts"]
    assert parts == [{"text": "p"}]


def test_gemini_images_become_inline_data_parts():
    adapter = GeminiAdapter("gemini-2.5-flash", "k" * 16)
    image = ImageInput(mime_type="image/png", data_b64="aGVsbG8=")
    _, _, payload = adapter.build_request("describe", [image])
    parts = payload["contents"][0]["parts"]
    assert parts[1] == {"inline_data": {"mime_type": "image/png", "data": "aGVsbG8="}}


def test_gemini_parse_ok():
    text, usage = GeminiAdapter("gemini-2.5-flash", "k" * 16).parse(_GEMINI_OK)
    assert text == "hello from gemini"
    assert usage == {"in": 7, "out": 3, "total": 10}


def test_gemini_parse_missing_candidates_raises_schema_error():
    with pytest.raises(SchemaError):
        GeminiAdapter("gemini-2.5-flash", "k" * 16).parse({"candidates": []})


# ---------------------------------------------------------------------------
# Groq / OpenRouter (shared OpenAI-chat shape)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter,host",
    [
        (GroqAdapter("llama-3.3-70b-versatile", "k" * 16), "api.groq.com"),
        (OpenRouterAdapter("meta-llama/llama-3.1-8b-instruct:free", "k" * 16),
         "openrouter.ai"),
    ],
)
def test_openai_chat_request_shape(adapter, host):
    url, headers, payload = adapter.build_request("prompt", [])
    assert host in url
    assert headers["Authorization"] == f"Bearer {'k' * 16}"
    assert payload == {
        "model": adapter.model,
        "messages": [{"role": "user", "content": "prompt"}],
        "temperature": 0.0,
    }


@pytest.mark.parametrize(
    "adapter",
    [
        GroqAdapter("llama-3.3-70b-versatile", "k" * 16),
        OpenRouterAdapter("m/f", "k" * 16),
    ],
)
def test_openai_chat_parse_ok(adapter):
    text, usage = adapter.parse(_OPENAI_OK)
    assert text == "hello from groq"
    assert usage == {"in": 4, "out": 2, "total": 6}


@pytest.mark.parametrize(
    "adapter",
    [
        GroqAdapter("llama-3.3-70b-versatile", "k" * 16),
        OpenRouterAdapter("m/f", "k" * 16),
    ],
)
def test_openai_chat_parse_missing_choices_raises_schema_error(adapter):
    with pytest.raises(SchemaError):
        adapter.parse({"choices": []})


def test_text_only_adapter_refuses_images_loudly():
    # Unreachable through the router (see() filters on supports_vision);
    # explicit so a future caller fails loudly instead of inventing a
    # caption (PHASE_5_BRIEF §4, constraints #10/#11).
    image = ImageInput(mime_type="image/png", data_b64="aGk=")
    with pytest.raises(ValueError, match="cannot process images"):
        GroqAdapter("llama-3.3-70b-versatile", "k" * 16).build_request("p", [image])


def test_capability_flags_are_correct():
    assert GeminiAdapter("m", "k" * 16).supports_vision is True
    assert GroqAdapter("m", "k" * 16).supports_vision is False
    assert OpenRouterAdapter("m", "k" * 16).supports_vision is False


# ---------------------------------------------------------------------------
# build_adapters
# ---------------------------------------------------------------------------


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)


def _cfg(**overrides) -> ProviderSettings:
    kwargs = dict(
        model="gemini-2.5-flash", rpm=10, rpd=1500, supports_vision=True
    )
    kwargs.update(overrides)
    return ProviderSettings(**kwargs)


def test_build_adapters_respects_order_and_env():
    env = {"GEMINI_API_KEY": "g" * 16, "GROQ_API_KEY": "r" * 16}
    cfgs = {
        "gemini": _cfg(),
        "groq": _cfg(model="llama-3.3-70b-versatile", supports_vision=False),
    }
    adapters = build_adapters(["groq", "gemini"], cfgs, env, _Log())
    assert [a.name for a in adapters] == ["groq", "gemini"]


def test_build_adapters_skips_disabled_provider_with_one_log():
    log = _Log()
    env = {"GEMINI_API_KEY": "g" * 16}
    cfgs = {"gemini": _cfg(enabled=False)}
    adapters = build_adapters(["gemini"], cfgs, env, log)
    assert adapters == []
    assert len(log.messages) == 1
    assert "disabled" in log.messages[0]


def test_build_adapters_skips_missing_key():
    adapters = build_adapters(["gemini"], {"gemini": _cfg()}, {}, _Log())
    assert adapters == []


def test_build_adapters_skips_missing_model():
    adapters = build_adapters(
        ["gemini"], {"gemini": _cfg(model=None)}, {"GEMINI_API_KEY": "g" * 16}, _Log()
    )
    assert adapters == []


def test_build_adapters_skips_unimplemented_provider():
    adapters = build_adapters(["anthropic"], {}, {}, _Log())
    assert adapters == []


def test_api_key_env_names():
    assert API_KEY_ENV["gemini"] == "GEMINI_API_KEY"
    assert API_KEY_ENV["groq"] == "GROQ_API_KEY"
    assert API_KEY_ENV["openrouter"] == "OPENROUTER_API_KEY"
