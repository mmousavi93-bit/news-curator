"""Unit + stage tests for pipeline/langretry.py: the retry-once forced-
Persian recovery for provider language drift (2026-08-30 clean-run
finding: a tier-2 Al Jazeera Arabic cluster answered in Arabic, scored
9.14, and was lost at compose's lang gate).

Deterministic, offline: a stub router returns canned responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from agent.collectors.base import Item
from agent.llm.errors import LlmResult, UNAVAILABLE
from agent.pipeline.cluster import cluster_items
from agent.pipeline.langretry import (
    FORCE_PERSIAN_LINE,
    drifts_from_persian,
    recovery_payload,
    retry_persian,
)
from agent.pipeline.understand import UnderstandStage

T0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
_TEMPLATE = "Articles:\n{items}"


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


class _StubRouter:
    """Canned responses in order; the last repeats. Records prompts."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt, *, stage="understand", use_reservation=None):
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self._responses) - 1)
        return self._responses[index]


def _ok(text: str) -> LlmResult:
    return LlmResult(ok=True, status="ok", text=text, provider="gemini",
                     model="gemini-flash-latest", prompt_hash="p" * 16,
                     call_index=1)


def _payload(headline: str, summary: str, **overrides) -> LlmResult:
    body = {"headline": headline, "summary": summary,
            "entities": ["Iran"], "clickbait": False, "irrelevant": False}
    body.update(overrides)
    return _ok(json.dumps(body))


_ARABIC = _payload("هجوم على سفينة تجارية في البحر الأحمر",
                   "تقارير أولية تشير إلى الحادثة")
_PERSIAN = _payload("حمله به یک کشتی تجاری در دریای سرخ",
                    "گزارش‌های اولیه به وقوع این حادثه اشاره دارند")


def _item(url: str) -> Item:
    return Item(source_id="src", url=url, title="title", body="body text",
                published_at=T0, lang="ar", raw_hash="h" * 8)


def _cluster(items) -> object:
    return cluster_items(items, [(1.0, 0.0, 0.0)] * len(items), 0.62)[0]


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    events: list = field(default_factory=list)
    router: object = None
    db: object = None
    counters: dict = field(default_factory=dict)
    now: datetime = T0


def test_persian_text_is_not_drift():
    assert drifts_from_persian("حمله به یک کشتی در تنگه هرمز", "جزئیات حادثه") is False


@pytest.mark.parametrize("marker", ["ة", "ى", "ي", "ك", "إ"])
def test_arabic_only_markers_are_drift(marker):
    assert drifts_from_persian(f"خبر مهم با {marker}", "خلاصه") is True


def test_hebrew_block_is_drift():
    assert drifts_from_persian("חדשות", "summary") is True


def test_persian_productive_alef_is_not_drift():
    # أ is deliberately NOT a marker (تأیید، مأموریت) -- review finding
    # 2026-08-30: dropping on it would lose exactly the stories the
    # digest exists for.
    assert drifts_from_persian("تأیید خبر مهم", "جزئیات") is False


def test_retry_appends_force_line_and_uses_understand_stage():
    router = _StubRouter([_ok("{}")])
    retry_persian(router, "Articles:\n- item")
    assert len(router.prompts) == 1
    assert router.prompts[0].startswith("Articles:")
    assert router.prompts[0].endswith(FORCE_PERSIAN_LINE)


def test_recovery_returns_none_on_provider_failure():
    router = _StubRouter([LlmResult(ok=False, status=UNAVAILABLE)])
    payload, status = recovery_payload(router, "p")
    assert payload is None
    assert status == UNAVAILABLE


def test_recovery_returns_none_on_unparseable():
    router = _StubRouter([_ok("not json")])
    payload, status = recovery_payload(router, "p")
    assert payload is None and status == "unparseable"


def test_recovery_returns_none_on_oversized():
    big = json.dumps({"headline": "حمله به یک کشتی",
                      "summary": "بله. " * 200,
                      "clickbait": False, "irrelevant": False})
    router = _StubRouter([_ok(big)])
    payload, status = recovery_payload(router, "p")
    assert payload is None and status == "oversized"


def test_recovery_returns_none_on_filtered():
    router = _StubRouter([_payload("خبر مهم نظامی", "جزئیات خبر",
                                   irrelevant=True)])
    payload, status = recovery_payload(router, "p")
    assert payload is None and status == "filtered"


def test_recovery_returns_none_when_retry_still_non_persian():
    router = _StubRouter([_ARABIC])
    payload, status = recovery_payload(router, "p")
    assert payload is None and status == "still_non_persian"


def test_recovery_returns_payload_when_retry_is_persian():
    router = _StubRouter([_PERSIAN])
    payload, status = recovery_payload(router, "p")
    assert status == "ok"
    assert payload["headline"] == "حمله به یک کشتی تجاری در دریای سرخ"


def test_stage_retries_arabic_answer_and_uses_persian_replacement():
    log = _Log()
    router = _StubRouter([_ARABIC, _PERSIAN])
    ctx = _Ctx(clusters=[_cluster([_item("https://x/1")])], router=router)
    UnderstandStage(_TEMPLATE, 600, log).run(ctx)
    assert len(ctx.events) == 1
    assert ctx.events[0].headline == "حمله به یک کشتی تجاری در دریای سرخ"
    assert len(router.prompts) == 2
    assert FORCE_PERSIAN_LINE in router.prompts[1]
    assert any("Persian recovered" in m for m in log.messages)


def test_stage_keeps_original_when_retry_fails():
    log = _Log()
    router = _StubRouter([_ARABIC, LlmResult(ok=False, status=UNAVAILABLE)])
    ctx = _Ctx(clusters=[_cluster([_item("https://x/1")])], router=router)
    UnderstandStage(_TEMPLATE, 600, log).run(ctx)
    assert len(ctx.events) == 1  # original kept -- compose gate drops it
    assert ctx.events[0].headline == "هجوم على سفينة تجارية في البحر الأحمر"
    assert len(router.prompts) == 2
    assert any("retry failed" in m for m in log.messages)


def test_stage_does_not_retry_persian_answer():
    router = _StubRouter([_PERSIAN])
    ctx = _Ctx(clusters=[_cluster([_item("https://x/1")])], router=router)
    UnderstandStage(_TEMPLATE, 600, _Log()).run(ctx)
    assert len(ctx.events) == 1
    assert len(router.prompts) == 1  # no extra call on compliant output
