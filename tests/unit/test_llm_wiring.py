"""Unit tests for llm/wiring.py's build_router: the session-9s tpm map
and the missing-tpm diagnostic (brief req 3: treat a missing value as
unconstrained but log it once -- so a silently-unpaced provider is
visible in the Actions log). No network: adapters are built but never
called."""

from __future__ import annotations

from pathlib import Path

import yaml

from agent.llm.transport import MockHttpTransport
from agent.llm.wiring import build_router
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def error(self, msg, *args):
        self.messages.append(("error", msg % args if args else msg))

    def warning(self, msg, *args):
        self.messages.append(("warning", msg % args if args else msg))

    def info(self, msg, *args):
        self.messages.append(("info", msg % args if args else msg))


def _llm_settings(**provider_tpms) -> Settings:
    raw = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    for name, tpm in provider_tpms.items():
        raw["llm"]["providers"][name]["tpm"] = tpm
    return Settings.from_dict(raw)


_ENV = {
    "GEMINI_API_KEY": "k", "GROQ_API_KEY": "k", "OPENROUTER_API_KEY": "k",
}


def _warnings(settings):
    log = RecordingLogger()
    build_router(settings.llm, _ENV, transport=MockHttpTransport(), logger=log)
    return [m for m in log.messages if m[0] == "warning" and "no tpm" in m[1]]


def test_missing_tpm_logged_once_per_provider():
    # The fixture's three providers carry no tpm: each gets ONE warning,
    # once per run -- never silently unpaced (brief req 3).
    warnings = _warnings(_llm_settings())
    assert len(warnings) == 3
    assert any("gemini" in m[1] for m in warnings)
    assert any("groq" in m[1] for m in warnings)


def test_no_warning_for_providers_that_declare_tpm():
    warnings = _warnings(_llm_settings(gemini=250000, groq=8000, openrouter=10000))
    assert warnings == []
