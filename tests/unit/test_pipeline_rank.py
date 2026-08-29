"""Unit tests for pipeline/rank.py: the deterministic digest importance
score (owner decision 2026-08-29). NOT the Phase-11 risk engine -- this is
display order from category, corroboration, tier, recency and volume."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.pipeline.rank import best_tier, rank_events, score_event
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


def _settings() -> Settings:
    return Settings.from_dict(yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")))


def _config(**credibility) -> Config:
    return Config(settings=_settings(), credibility=credibility)


def _cluster(key: str, members: list[Item]) -> Cluster:
    cluster = Cluster(key="")
    for m in members:
        cluster.add(m, [1.0])
    return cluster


def _item(source_id: str, hours_ago: float = 1.0) -> Item:
    return Item(source_id=source_id, url=f"https://x/{source_id}/{hours_ago}",
                title="t", body="b", published_at=NOW - timedelta(hours=hours_ago),
                lang="en", raw_hash="h" * 8)


def _event(key: str, category: str, independent: int = 1,
           claim_status: str = "unconfirmed") -> Event:
    return Event(event_key=key, summary=f"s {key}", category=category,
                 independent_count=independent, claim_status=claim_status,
                 source_count=1, first_seen_at=NOW, last_updated_at=NOW)


CRED = {
    "t1": SourceCredibility(tier=1, group=None),
    "t2": SourceCredibility(tier=2, group=None),
    "t3": SourceCredibility(tier=3, group=None),
    "lead": SourceCredibility(tier="lead", group=None),
}


def test_military_outranks_politics_at_equal_corroboration():
    settings = _settings()
    credibility = CRED
    military = _event("m" * 16, "military", independent=2)
    politics = _event("p" * 16, "politics", independent=2)
    cluster_m = _cluster("", [_item("t1"), _item("t2")])
    cluster_p = _cluster("", [_item("t1"), _item("t2")])
    s_m = score_event(military, cluster_m, credibility, settings, NOW)
    s_p = score_event(politics, cluster_p, credibility, settings, NOW)
    assert s_m > s_p


def test_strong_corroboration_can_beat_weak_category():
    settings = _settings()
    # Economy confirmed by 2 groups beats a military rumour from one
    # tier-3 channel: 2+4+3 vs 5+0+0 (+recency equal).
    economy = _event("e" * 16, "economy", independent=2)
    military = _event("m" * 16, "military", independent=0, claim_status="rumour")
    cluster_e = _cluster("", [_item("t1"), _item("t2")])
    cluster_m = _cluster("", [_item("t3")])
    s_e = score_event(economy, cluster_e, CRED, settings, NOW)
    s_m = score_event(military, cluster_m, CRED, settings, NOW)
    assert s_e > s_m


def test_tier_bonus_prefers_tier1():
    settings = _settings()
    tier1 = _event("a" * 16, "security", independent=1)
    tier2 = _event("b" * 16, "security", independent=1)
    s1 = score_event(tier1, _cluster("", [_item("t1")]), CRED, settings, NOW)
    s2 = score_event(tier2, _cluster("", [_item("t2")]), CRED, settings, NOW)
    assert s1 > s2


def test_recency_bonus_prefers_newer():
    settings = _settings()
    fresh = _event("a" * 16, "military", independent=1)
    stale = _event("b" * 16, "military", independent=1)
    s_fresh = score_event(fresh, _cluster("", [_item("t1", hours_ago=1.0)]), CRED, settings, NOW)
    s_stale = score_event(stale, _cluster("", [_item("t1", hours_ago=20.0)]), CRED, settings, NOW)
    assert s_fresh > s_stale


def test_min_score_splits_kept_from_dropped():
    settings = _settings()
    log = _Log()
    military = _event("m" * 16, "military", independent=1)   # 5+2+2+3 = 12
    other = _event("o" * 16, "other", independent=1)         # 0+2+2+3 = 7
    kept, dropped = rank_events(
        [other, military],
        {"m" * 16: _cluster("", [_item("t2")]), "o" * 16: _cluster("", [_item("t2")])},
        CRED, settings, NOW, log,
    )
    assert [e.event_key for e in kept] == ["m" * 16]
    assert [e.event_key for e in dropped] == ["o" * 16]
    assert any("below min_score" in m for m in log.messages)


def test_ranking_is_deterministic():
    settings = _settings()
    log = _Log()
    events = [
        _event("a" * 16, "politics", independent=1),
        _event("b" * 16, "military", independent=2),
        _event("c" * 16, "economy", independent=1),
    ]
    clusters = {e.event_key: _cluster("", [_item("t2")]) for e in events}
    first = [e.event_key for e in rank_events(events, clusters, CRED, settings, NOW, log)[0]]
    second = [e.event_key for e in rank_events(events, clusters, CRED, settings, NOW, log)[0]]
    assert first == second
    assert first[0] == "b" * 16  # military + corroborated on top


def test_best_tier_ignores_lead_members():
    cluster = _cluster("", [_item("lead"), _item("t2")])
    assert best_tier(cluster, CRED) == 2
    assert best_tier(_cluster("", [_item("lead")]), CRED) == 3
