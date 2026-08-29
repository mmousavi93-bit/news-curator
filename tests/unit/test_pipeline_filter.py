"""Unit tests for pipeline/filter.py: the topic gate and its config
validation (session-5 decision 1)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent.collectors.base import Item
from agent.config import ConfigError
from agent.pipeline.filter import TopicGateStage, validate_topics

TOPICS = {"en": ("iran", "tehran", "oil"), "fa": ("ایران",)}
GATED = {"gated_en", "gated_fa"}


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


def _item(source_id: str, lang: str, title: str, body: str = "") -> Item:
    return Item(source_id=source_id, url=f"https://x/{source_id}", title=title,
                body=body, published_at=None, lang=lang, raw_hash="h" * 8)


@dataclass
class _Ctx:
    items: list
    counters: dict = field(default_factory=dict)


def _stage(ctx, gated=GATED):
    return TopicGateStage(TOPICS, set(gated), _Log()), ctx


def test_non_gated_source_passes_everything_through():
    stage, ctx = _stage(_Ctx(items=[
        _item("tier1_source", "en", "Kitten rescued from tree"),
        _item("tier1_source", "en", "Iran holds military drills"),
    ]))
    stage.run(ctx)
    assert len(ctx.items) == 2
    assert ctx.counters["filter.kept"] == 2
    assert ctx.counters["filter.dropped"] == 0


def test_gated_source_off_topic_item_dropped():
    stage, ctx = _stage(_Ctx(items=[_item("gated_en", "en", "Celebrity wedding news")]))
    stage.run(ctx)
    assert ctx.items == []
    assert ctx.counters["filter.dropped"] == 1


def test_gated_source_on_topic_item_kept():
    stage, ctx = _stage(_Ctx(items=[_item("gated_en", "en", "Tehran oil talks resume")]))
    stage.run(ctx)
    assert len(ctx.items) == 1
    assert ctx.counters["filter.kept"] == 1


def test_gate_matches_body_not_just_title():
    stage, ctx = _stage(_Ctx(items=[
        _item("gated_en", "en", "Markets update", body="Crude prices and Iranian exports rose")
    ]))
    stage.run(ctx)
    assert len(ctx.items) == 1


def test_gate_is_case_insensitive_substring():
    stage, ctx = _stage(_Ctx(items=[_item("gated_en", "en", "IRANIAN navy exercises")]))
    stage.run(ctx)
    assert len(ctx.items) == 1


def test_unknown_language_fails_open():
    stage, ctx = _stage(_Ctx(items=[_item("gated_fa", "ur", "کچھ بھی نہیں")]))
    stage.run(ctx)
    assert len(ctx.items) == 1  # fail-open: never silently drop


def test_gate_uses_item_language_not_source():
    # The item claims fa, which has a list; the title is English, so it drops.
    stage, ctx = _stage(_Ctx(items=[_item("gated_fa", "fa", "British election results")]))
    stage.run(ctx)
    assert ctx.items == []


def test_empty_items_sets_counters():
    stage, ctx = _stage(_Ctx(items=[]))
    stage.run(ctx)
    assert ctx.counters["filter.kept"] == 0
    assert ctx.counters["filter.dropped"] == 0


def test_validate_topics_accepts_well_formed():
    assert validate_topics({"topics": {"en": ["Iran", " OIL "]}}) == {"en": ("iran", "oil")}


def test_validate_topics_rejects_missing_topics_key():
    with pytest.raises(ConfigError, match="topics"):
        validate_topics({"other": {}})


def test_validate_topics_rejects_non_list_keywords():
    with pytest.raises(ConfigError, match="list of strings"):
        validate_topics({"topics": {"en": "iran"}})


def test_validate_topics_rejects_non_string_keyword():
    with pytest.raises(ConfigError, match="non-string"):
        validate_topics({"topics": {"en": ["iran", 42]}})
