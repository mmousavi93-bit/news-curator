"""Unit tests for pipeline/compose.py: events -> one budgeted message (or
the honest one-liner), priority ordering, date_only handling, digest marker
and the 4,096-char ceiling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.pipeline.compose import ComposeStage
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


def _config() -> Config:
    settings = Settings.from_dict(yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")))
    return Config(settings=settings, credibility={})


@dataclass
class _Ctx:
    config: Config
    events: list = field(default_factory=list)
    clusters: list = field(default_factory=list)
    now: datetime = NOW
    daily_digest: bool = False
    counters: dict = field(default_factory=dict)


def _cluster(key: str, members: list[Item]) -> Cluster:
    cluster = Cluster(key=key)
    for item in members:
        cluster.add(item, [1.0])  # dummy unit vector; centroid irrelevant here
    return cluster


def _event(key: str, summary: str = "First sentence. More detail.") -> Event:
    return Event(event_key=key, summary=summary, entities=("Iran",), source_count=2)


def test_no_events_produces_honest_one_liner():
    ctx = _Ctx(config=_config())
    ComposeStage(_Log()).run(ctx)
    assert ctx.message == "Nothing new since the last run."
    assert ctx.counters["compose"] == 0


def test_events_become_items_in_priority_order():
    events = [
        _event("a" * 16, summary="Alpha event. Alpha detail."),
        _event("b" * 16, summary="Beta event. Beta detail."),
    ]
    ctx = _Ctx(config=_config(), events=events)
    ComposeStage(_Log()).run(ctx)
    assert ctx.counters["compose"] == 2
    assert "Alpha event" in ctx.message  # headline + detail rendered
    assert "Beta event" in ctx.message
    # Priority order (event index order) is preserved in the message.
    assert ctx.message.index("Alpha") < ctx.message.index("Beta")


def test_message_stays_within_telegram_char_cap():
    ctx = _Ctx(config=_config(), events=[
        _event(f"{i:016x}", summary=f"Event {i}. " + "detail " * 200) for i in range(30)
    ])
    ComposeStage(_Log()).run(ctx)
    assert len(ctx.message.encode("utf-16-le")) // 2 <= 4096


def test_date_only_cluster_says_time_not_stated():
    member = Item(source_id="s", url="https://x/1", title="t", body="b",
                  published_at=NOW, lang="en", raw_hash="h" * 8, date_only=True)
    cluster = _cluster("", [member])  # Cluster.add() computes the real key
    ctx = _Ctx(config=_config(), events=[_event(cluster.key)], clusters=[cluster])
    ComposeStage(_Log()).run(ctx)
    assert "time not stated" in ctx.message
    assert "03:30" not in ctx.message  # midnight placeholder never rendered


def test_normal_cluster_shows_tehran_clock_time():
    member = Item(source_id="s", url="https://x/1", title="t", body="b",
                  published_at=NOW, lang="en", raw_hash="h" * 8)
    cluster = _cluster("", [member])
    ctx = _Ctx(config=_config(), events=[_event(cluster.key)], clusters=[cluster])
    ComposeStage(_Log()).run(ctx)
    assert "Tehran" in ctx.message


def test_digest_marker_in_header_only_when_flagged():
    ctx = _Ctx(config=_config(), events=[_event("d" * 16)], daily_digest=True)
    ComposeStage(_Log()).run(ctx)
    assert "daily digest" in ctx.message
    ctx2 = _Ctx(config=_config(), events=[_event("d" * 16)], daily_digest=False)
    ComposeStage(_Log()).run(ctx2)
    assert "daily digest" not in ctx2.message
