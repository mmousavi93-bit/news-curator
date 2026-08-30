"""One adapter per provider: request shape and response parsing.

No I/O, no policy. (prompt, images) -> (url, headers, payload); JSON body
-> (text, usage). Classification and failover live in router.py, HTTP in
transport.py. No prompt text is constructed in this package (PHASE_5_BRIEF
"Out of scope"); temperature is fixed at 0 (constraint #3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from agent.llm.errors import SchemaError

# Env var per provider. Values registered with the redaction filter by
# run.py at startup; never log them (PHASE_5_BRIEF §7).
API_KEY_ENV: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "bai": "BAI_API_KEY",
}

# (connect, read) timeout in seconds. Read is generous: a free-tier
# provider can take tens of seconds on a slow night.
DEFAULT_TIMEOUT = (10.0, 60.0)


@dataclass(frozen=True, slots=True)
class ImageInput:
    mime_type: str
    data_b64: str


class ProviderAdapter(Protocol):
    name: str
    supports_vision: bool
    model: str

    def build_request(
        self, prompt: str, images: Sequence[ImageInput]
    ) -> tuple[str, dict[str, str], dict]: ...

    def parse(self, body: Mapping) -> tuple[str, dict[str, int]]: ...


class GeminiAdapter:
    """gemini-2.5-flash via generateContent. The key travels in an
    x-goog-api-key header, NEVER the query string: requests exception
    messages contain the URL, and a ?key=<secret> in any exception string
    would be one public log line away from leaking (PHASE_5_BRIEF §7)."""

    name = "gemini"
    supports_vision = True

    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        self._api_key = api_key

    def build_request(
        self, prompt: str, images: Sequence[ImageInput]
    ) -> tuple[str, dict[str, str], dict]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        headers = {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}
        parts: list[dict] = [{"text": prompt}]
        for image in images:
            parts.append(
                {"inline_data": {"mime_type": image.mime_type, "data": image.data_b64}}
            )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.0},
        }
        return url, headers, payload

    def parse(self, body: Mapping) -> tuple[str, dict[str, int]]:
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SchemaError(
                "gemini response missing candidates/content/parts/text", provider=self.name
            ) from exc
        meta = body.get("usageMetadata") or {}
        usage = {
            "in": int(meta.get("promptTokenCount", 0)),
            "out": int(meta.get("candidatesTokenCount", 0)),
            "total": int(meta.get("totalTokenCount", 0)),
        }
        return text, usage


class _OpenAiChatAdapter:
    """Shared OpenAI chat-completions shape: Groq and OpenRouter speak it."""

    supports_vision = False

    def __init__(self, name: str, url: str, model: str, api_key: str) -> None:
        self.name = name
        self.model = model
        self._url = url
        self._api_key = api_key

    def build_request(
        self, prompt: str, images: Sequence[ImageInput]
    ) -> tuple[str, dict[str, str], dict]:
        if images:
            # Unreachable via the router (see() filters on supports_vision,
            # PHASE_5_BRIEF §4); explicit so a future caller fails loudly
            # instead of inventing a caption (constraints #10, #11).
            raise ValueError(f"{self.name} cannot process images")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            # Hard output cap (2026-08-30 live evidence): without it,
            # nemotron-3.5-lightning generated ~16.5K tokens on a task that
            # needs ~400 -- an 8-minute ramble whose continuous bytes kept
            # the read timeout fed, so it never tripped. The chat adapters'
            # only consumer today is the understand stage (~400-token strict
            # JSON); 700 is headroom, and a truncated JSON fails loudly
            # (schema error -> retry) rather than silently.
            "max_tokens": 700,
        }
        return self._url, headers, payload

    def parse(self, body: Mapping) -> tuple[str, dict[str, int]]:
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SchemaError(
                f"{self.name} response missing choices/message/content", provider=self.name
            ) from exc
        meta = body.get("usage") or {}
        usage = {
            "in": int(meta.get("prompt_tokens", 0)),
            "out": int(meta.get("completion_tokens", 0)),
            "total": int(meta.get("total_tokens", 0)),
        }
        return text, usage


class GroqAdapter(_OpenAiChatAdapter):
    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(
            "groq", "https://api.groq.com/openai/v1/chat/completions", model, api_key
        )


class OpenRouterAdapter(_OpenAiChatAdapter):
    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(
            "openrouter", "https://openrouter.ai/api/v1/chat/completions", model, api_key
        )


class BaiAdapter(_OpenAiChatAdapter):
    """The owner's reseller gateway (2026-08-30). OpenAI-compatible; the
    free roster there is a gift, not capacity to depend on -- it sits in
    the cascade BEHIND groq. Text-only (vision stays on Gemini)."""

    def __init__(self, model: str, api_key: str) -> None:
        super().__init__(
            "bai", "https://api.b.ai/v1/chat/completions", model, api_key
        )
