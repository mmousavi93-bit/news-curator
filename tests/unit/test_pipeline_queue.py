"""Unit tests for pipeline/queue.py: the 24h digest queue (Session 25).

A ranked-but-not-delivered event is DEFERRED, never dropped -- it stays a
candidate for up to queue_window_hours so a #6 this morning can rise to #2
this afternoon. No LLM, no network: pure sqlite + score arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.memory import db as memory_db
from agent.memory.event_json import event_from_json, event_to_json
from agent.memory.event_models import Event, mark_delivered
from agent.pipeline.cluster import Cluster
from agent.pipeline.queue import load_pending, select_digest, store_pending
from agent.settings import Settings

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, m, *a):
        self.messages.append(m % a if a else m)

    def error(self, m, *a):
        self.messages.append(m % a if a else m)

    def warning(self, m, *a):
        self.messages.append(m % a if a else m)


def _settings() -> Settings:
    return Settings.from_dict(yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")))


def _config() -> Config:
    return Config(settings=_settings(), credibility={
        "t1": SourceCredibility(tier=1, group="g1"),
        "t2": SourceCredibility(tier=2, group="g2"),
        "t3": SourceCredibility(tier=3, group="g3"),
    })


@dataclass
class _Ctx:
    config: Config
    db: object = None
    clusters: list = field(default_factory=list)
    now: datetime = NOW


def _mk(seed: str, summary: str, *, category: str = "military",
        significance: str = "escalation", independent: int = 2,
        source_id: str = "t1") -> tuple[Event, Cluster]:
    """Build a cluster + a matching-key event (event_key == cluster.key)."""
    cluster = Cluster(key="")
    cluster.add(Item(source_id=source_id, url=f"https://x/{seed}", title="t",
                     body="b", published_at=NOW, lang="fa", raw_hash="h" * 8),
                [1.0])
    event = Event(
        event_key=cluster.key, summary=summary, headline=f"تیتر {summary}",
        entities=("اسرائیل",), category=category, significance=significance,
        why_matters="", claim_status="likely",
        independent_count=independent, source_count=2,
        first_seen_at=NOW, last_updated_at=NOW,
    )
    return event, cluster


def _fresh(ctx: _Ctx, seeds: list[str], **kw) -> list[Event]:
    events = []
    for seed in seeds:
        event, cluster = _mk(seed, f"خلاصه {seed}", **kw)
        ctx.clusters.append(cluster)
        events.append(event)
    return events


def test_event_json_roundtrip_preserves_inmemory_fields():
    event, _ = _mk("a", "خلاصه")
    event = Event(
        event_key=event.event_key, summary=event.summary, headline=event.headline,
        entities=event.entities, category=event.category,
        significance=event.significance, why_matters="چرا مهم",
        claim_status=event.claim_status, source_count=event.source_count,
        independent_count=event.independent_count,
        follow_up=True, follow_up_high=True, prior_context="خلاصه قبل",
        first_seen_at=event.first_seen_at, last_updated_at=event.last_updated_at,
    )
    back = event_from_json(event_to_json(event))
    assert back.event_key == event.event_key
    assert back.headline == event.headline
    assert back.significance == "escalation"
    assert back.why_matters == "چرا مهم"
    assert back.follow_up is True
    assert back.follow_up_high is True
    assert back.prior_context == "خلاصه قبل"
    assert back.entities == event.entities
    assert back.independent_count == 2


def test_select_digest_caps_to_max_items(tmp_path):
    db = memory_db.open_db(tmp_path / "s.db", create_if_absent=True)
    ctx = _Ctx(config=_config(), db=db)
    fresh = _fresh(ctx, [f"s{i}" for i in range(8)])
    top = select_digest(ctx, fresh, ctx.config.settings, _Log())
    assert len(top) == 5  # settings_minimal max_items
    top_keys = {e.event_key for e in top}
    assert top_keys <= {e.event_key for e in fresh}


def test_deferred_fresh_events_persist_to_pending(tmp_path):
    db = memory_db.open_db(tmp_path / "s.db", create_if_absent=True)
    ctx = _Ctx(config=_config(), db=db)
    fresh = _fresh(ctx, [f"s{i}" for i in range(8)])
    top = select_digest(ctx, fresh, ctx.config.settings, _Log())
    top_keys = {e.event_key for e in top}
    pending_keys = {e.event_key for e, _ in load_pending(ctx, NOW)}
    assert pending_keys == {e.event_key for e in fresh} - top_keys
    assert len(pending_keys) == 3  # 8 fresh - 5 delivered


def test_deferred_event_rises_when_fresh_is_weaker(tmp_path):
    db = memory_db.open_db(tmp_path / "s.db", create_if_absent=True)
    ctx = _Ctx(config=_config(), db=db)
    # Run 1: 8 strong escalation events -> top 5 delivered, 3 deferred.
    fresh1 = _fresh(ctx, [f"a{i}" for i in range(8)])
    top1 = select_digest(ctx, fresh1, ctx.config.settings, _Log())
    top1_keys = {e.event_key for e in top1}
    mark_delivered(db, top1_keys, NOW)
    deferred1 = {e.event_key for e in fresh1} - top1_keys
    assert len(deferred1) == 3
    # Run 2: 2 weak economy events -> the 3 deferred strong events rise.
    fresh2 = _fresh(ctx, [f"b{i}" for i in range(2)], category="economy",
                    significance="economy", source_id="t2")
    top2 = select_digest(ctx, fresh2, ctx.config.settings, _Log())
    top2_keys = {e.event_key for e in top2}
    assert len(top2_keys) == 5
    assert deferred1 <= top2_keys  # every deferred story made the cut


def test_expired_pending_is_not_loaded(tmp_path):
    db = memory_db.open_db(tmp_path / "s.db", create_if_absent=True)
    ctx = _Ctx(config=_config(), db=db)
    event, _ = _mk("old", "قدیمی")
    store_pending(ctx, [(event, 20.0)], NOW - timedelta(hours=25))
    assert load_pending(ctx, NOW) == []


def test_delivered_pending_is_not_loaded(tmp_path):
    db = memory_db.open_db(tmp_path / "s.db", create_if_absent=True)
    ctx = _Ctx(config=_config(), db=db)
    event, _ = _mk("delivered", "تحویل‌شده")
    store_pending(ctx, [(event, 20.0)], NOW)
    mark_delivered(db, [event.event_key], NOW)
    assert load_pending(ctx, NOW) == []


def test_no_db_selects_fresh_only(tmp_path):
    # Dry-run/mock: no db -> queue functions no-op, select returns fresh top-5.
    ctx = _Ctx(config=_config(), db=None)
    fresh = _fresh(ctx, [f"s{i}" for i in range(8)])
    top = select_digest(ctx, fresh, ctx.config.settings, _Log())
    assert len(top) == 5
