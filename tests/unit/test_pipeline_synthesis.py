"""Unit tests for pipeline/synthesis.py (Session 25): the one-line "big
picture" synthesis is OPTIONAL + cosmetic -- every failure path omits the
line, never raises, never sinks the digest."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from agent.llm.errors import LlmResult
from agent.memory.event_models import Event
from agent.pipeline.synthesis import build_synthesis

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
TPL = "SYNTH PROMPT\n{events}"


class _Log:
    def info(self, m, *a):
        pass

    def error(self, m, *a):
        pass

    def warning(self, m, *a):
        pass


class _FakeRouter:
    def __init__(self, result: LlmResult) -> None:
        self._result = result

    def complete(self, prompt, stage="synthesis"):
        return self._result


def _events(n: int = 2) -> list[Event]:
    return [
        Event(event_key=f"k{i}" * 8, summary=f"گزارش تازه رویداد {i}", headline=f"تیتر {i}",
              claim_status="confirmed",
              category="military", significance="escalation",
              first_seen_at=NOW, last_updated_at=NOW)
        for i in range(n)
    ]


def _ctx(router):
    return type("Ctx", (), {"router": router, "db": None})()


def test_returns_the_line_when_valid():
    router = _FakeRouter(LlmResult(
        ok=True, status="ok",
        text=json.dumps({"synthesis": "امروز چند جبهه به طور همزمان فعال شد.",
                         "evidence": [{"event_id": e.event_key, "quote": e.summary}
                                      for e in _events()]})))
    line = build_synthesis(_ctx(router), _events(), TPL, _Log())
    assert line == "امروز چند جبهه به طور همزمان فعال شد."


def test_omits_when_fewer_than_two_events():
    router = _FakeRouter(LlmResult(ok=True, status="ok", text='{"synthesis": "x"}'))
    assert build_synthesis(_ctx(router), _events(1), TPL, _Log()) == ""


def test_omits_when_no_router():
    assert build_synthesis(_ctx(None), _events(), TPL, _Log()) == ""


def test_omits_when_llm_call_fails():
    router = _FakeRouter(LlmResult(ok=False, status="status_429", text=""))
    assert build_synthesis(_ctx(router), _events(), TPL, _Log()) == ""


def test_omits_when_unparseable():
    router = _FakeRouter(LlmResult(ok=True, status="ok", text="not json at all"))
    assert build_synthesis(_ctx(router), _events(), TPL, _Log()) == ""


def test_omits_when_too_short():
    # 1 Persian word -- below SYNTHESIS_WORD_BOUNDS lower bound.
    router = _FakeRouter(LlmResult(ok=True, status="ok", text='{"synthesis": "کوتاه"}'))
    assert build_synthesis(_ctx(router), _events(), TPL, _Log()) == ""


def test_omits_when_not_persian_script():
    router = _FakeRouter(LlmResult(
        ok=True, status="ok",
        text='{"synthesis": "Today several fronts were active at once."}'))
    assert build_synthesis(_ctx(router), _events(), TPL, _Log()) == ""
