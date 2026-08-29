"""Unit tests for pipeline/understand.py: one router call per cluster, JSON
parsing, the clickbait/irrelevance filter, the budget-stop contract and the
events write. The router is a stub; the prompt template is the real
config/prompts/understand.txt loaded from the repo (no fixture drift)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.collectors.base import Item
from agent.llm.errors import LlmResult, REFUSED_CAP, UNAVAILABLE
from agent.memory import db as memory_db
from agent.memory.event_models import read_events
from agent.pipeline.cluster import cluster_items
from agent.pipeline.understand import UnderstandStage, _extract_json, render_prompt

_REPO_ROOT = Path(__file__).parent.parent.parent
_TEMPLATE = (_REPO_ROOT / "config" / "prompts" / "understand.txt").read_text(encoding="utf-8")

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


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
                     model="gemini-2.5-flash", prompt_hash="p" * 16, call_index=1)


def _json_result(**overrides) -> LlmResult:
    payload = {"headline": "Headline", "summary": "Summary text.",
               "entities": ["Iran"], "clickbait": False, "irrelevant": False}
    payload.update(overrides)
    return _ok(json.dumps(payload))


def _item(url: str, published_at=None, date_only: bool = False, body: str = "body text") -> Item:
    return Item(source_id="src", url=url, title="title", body=body,
                published_at=published_at, lang="en", raw_hash="h" * 8, date_only=date_only)


def _cluster(members, vector=(1.0, 0.0, 0.0)):
    return cluster_items(members, [vector] * len(members), 0.62)[0]


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    events: list = field(default_factory=list)
    router: object = None
    db: object = None
    counters: dict = field(default_factory=dict)
    now: datetime = T0


def _stage() -> tuple[UnderstandStage, _Log]:
    log = _Log()
    return UnderstandStage(_TEMPLATE, 600, log), log


def test_summarises_each_cluster_into_an_event():
    stage, log = _stage()
    cluster = _cluster([_item(f"https://x/e/{i}", T0) for i in range(3)])
    ctx = _Ctx(clusters=[cluster], router=_StubRouter([_json_result()]))
    stage.run(ctx)
    assert len(ctx.events) == 1
    event = ctx.events[0]
    assert event.event_key == cluster.key
    assert event.summary == "Summary text."
    assert event.entities == ("Iran",)
    assert event.source_count == 3
    assert event.first_seen_at == T0


def test_irrelevant_cluster_is_dropped():
    stage, log = _stage()
    ctx = _Ctx(clusters=[_cluster([_item("https://x/1", T0)])],
               router=_StubRouter([_json_result(irrelevant=True)]))
    stage.run(ctx)
    assert ctx.events == []
    assert any("content filter" in m for m in log.messages)


def test_clickbait_cluster_is_dropped():
    stage, _ = _stage()
    ctx = _Ctx(clusters=[_cluster([_item("https://x/1", T0)])],
               router=_StubRouter([_json_result(clickbait=True)]))
    stage.run(ctx)
    assert ctx.events == []


def test_unparseable_response_skips_cluster_and_continues():
    stage, log = _stage()
    clusters = [
        _cluster([_item("https://x/bad/0", T0)]),
        _cluster([_item("https://x/good/0", T0)], vector=(0.0, 1.0, 0.0)),
    ]
    ctx = _Ctx(clusters=clusters,
               router=_StubRouter([_ok("not json at all"), _json_result()]))
    stage.run(ctx)
    assert len(ctx.events) == 1
    assert any("unparseable" in m for m in log.messages)


def test_refused_cap_stops_the_loop_and_logs_once():
    stage, log = _stage()
    clusters = [
        _cluster([_item("https://x/c0/0", T0)]),
        _cluster([_item("https://x/c1/0", T0)], vector=(0.0, 1.0, 0.0)),
        _cluster([_item("https://x/c2/0", T0)], vector=(0.0, 0.0, 1.0)),
    ]
    router = _StubRouter([LlmResult(ok=False, status=REFUSED_CAP)])
    ctx = _Ctx(clusters=clusters, router=router)
    stage.run(ctx)
    assert ctx.events == []
    assert len(router.prompts) == 1  # stopped, did not keep calling
    caps = [m for m in log.messages if "call cap" in m]
    assert len(caps) == 1


def test_unavailable_skips_without_raising():
    stage, log = _stage()
    ctx = _Ctx(clusters=[_cluster([_item("https://x/1", T0)])],
               router=_StubRouter([LlmResult(ok=False, status=UNAVAILABLE)]))
    stage.run(ctx)  # must not raise
    assert ctx.events == []
    assert any("skipped" in m for m in log.messages)
    # The honesty flag: clusters existed, no LLM call succeeded.
    assert ctx.llm_failed is True


def test_events_persisted_when_db_present(tmp_path):
    stage, _ = _stage()
    cluster = _cluster([_item(f"https://x/e/{i}", T0) for i in range(2)])
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    try:
        ctx = _Ctx(clusters=[cluster], router=_StubRouter([_json_result()]), db=conn)
        stage.run(ctx)
    finally:
        conn.close()
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    try:
        events = read_events(conn)
    finally:
        conn.close()
    assert len(events) == 1
    assert events[0].event_key == cluster.key
    assert events[0].claim_status == "unconfirmed"


def test_no_db_no_persistence_no_crash():
    stage, _ = _stage()
    ctx = _Ctx(clusters=[_cluster([_item("https://x/1", T0)])],
               router=_StubRouter([_json_result()]))
    stage.run(ctx)
    assert len(ctx.events) == 1  # in memory only


def test_extract_json_strips_markdown_fences():
    text = '```json\n{"headline": "H", "summary": "S"}\n```'
    assert _extract_json(text) == {"headline": "H", "summary": "S"}


def test_extract_json_rejects_non_object():
    with pytest.raises(ValueError):
        _extract_json('["a", "b"]')


def test_undated_members_get_observation_time_and_persist(tmp_path):
    """Regression: events.first_seen_at is NOT NULL; an event whose members
    all lack dates was written with NULL and INSERT OR IGNORE silently
    dropped the row. Now the observation time (ctx.now) is used -- a fact,
    not an invention -- and the row persists."""
    cluster = _cluster([_item("https://x/nodate/0", published_at=None)])
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    ctx = _Ctx(clusters=[cluster], router=_StubRouter([_json_result()]), db=conn)
    stage, _ = _stage()
    try:
        stage.run(ctx)
    finally:
        conn.close()
    assert ctx.events[0].first_seen_at == T0
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    try:
        assert len(read_events(conn)) == 1  # not silently dropped
    finally:
        conn.close()


def test_render_prompt_truncates_body_and_marks_date_only():
    long_body = "x" * 1000
    item = _item("https://x/1", T0, date_only=True, body=long_body)
    cluster = _cluster([item])
    prompt = render_prompt(_TEMPLATE, cluster, body_chars=100)
    assert "date only, time not stated" in prompt
    assert "xxx" in prompt
    assert len(long_body) > 100 and "x" * 150 not in prompt
    assert item.source_id in prompt
