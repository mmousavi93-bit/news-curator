"""Unit tests for pipeline/cluster.py: deterministic greedy clustering, the
priority rank and the enforced cap (session-5 decision 1). Vectors are
controlled stubs -- the gate question "ten articles about one event become
one event" is about ASSIGNMENT logic, which is exactly what a fake model
can test (PHASE_6_BRIEF §2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from pathlib import Path

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.pipeline.cluster import ClusterStage, cluster_items, rank_and_truncate
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _settings(**overrides) -> Settings:
    raw = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        raw["pipeline"][key] = value
    return Settings.from_dict(raw)


def _config(credibility: dict, **overrides) -> Config:
    return Config(settings=_settings(**overrides), credibility=credibility)


def _item(source_id: str, url: str, published_at=None) -> Item:
    return Item(source_id=source_id, url=url, title=f"t {url}", body="b",
                published_at=published_at, lang="en", raw_hash="h" * 8)


def _unit(*values: float) -> list[float]:
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values] if norm else list(values)


class _VecEmbedder:
    """Returns the vectors given at construction, by index."""

    def __init__(self, vectors):
        self._vectors = vectors

    def embed(self, texts):
        return self._vectors


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


@dataclass
class _Ctx:
    config: Config
    items: list = field(default_factory=list)
    embeddings: list = field(default_factory=list)
    clusters: list = field(default_factory=list)
    counters: dict = field(default_factory=dict)


def _event_cluster_fixture():
    """12 items: 10 near-identical (one event), 2 about something else."""
    items = []
    vectors = []
    for i in range(10):
        items.append(_item("src_a", f"https://x/event/{i}", T0 + timedelta(minutes=i)))
        vectors.append(_unit(1.0, 0.01 * (i + 1), 0.0))
    for i in range(2):
        items.append(_item("src_b", f"https://x/other/{i}", T0))
        vectors.append(_unit(0.0, 0.0, 1.0))
    return items, vectors


def test_ten_articles_about_one_event_become_one_cluster():
    items, vectors = _event_cluster_fixture()
    clusters = cluster_items(items, vectors, threshold=0.62)
    assert len(clusters) == 2
    sizes = sorted(len(c.members) for c in clusters)
    assert sizes == [2, 10]


def test_clustering_is_deterministic():
    items, vectors = _event_cluster_fixture()
    first = [c.key for c in cluster_items(items, vectors, 0.62)]
    second = [c.key for c in cluster_items(items, vectors, 0.62)]
    assert first == second


def test_identical_vectors_merge_orthogonal_split():
    items = [_item("s", f"https://x/a/{i}") for i in range(2)]
    clusters = cluster_items(items, [_unit(1, 0), _unit(1, 0)], 0.62)
    assert len(clusters) == 1
    clusters = cluster_items(items, [_unit(1, 0), _unit(0, 1)], 0.62)
    assert len(clusters) == 2


def test_cluster_key_is_hash_of_sorted_member_urls():
    items = [_item("s", "https://x/b"), _item("s", "https://x/a")]
    clusters = cluster_items(items, [_unit(1, 0), _unit(1, 0)], 0.62)
    assert len(clusters) == 1
    assert len(clusters[0].key) == 16


def test_undated_items_cluster_without_crashing():
    items = [_item("s", f"https://x/u/{i}", None) for i in range(3)]
    clusters = cluster_items(items, [_unit(1, 0)] * 3, 0.62)
    assert len(clusters) == 1


def test_cap_truncates_and_orders_tier_before_recency():
    # Five distinct clusters. The tier-1 cluster is OLDEST; it must still
    # rank first and survive the cap. The two newest tier-3 clusters are
    # dropped (session-5 decision 1: priority, not chronology).
    credibility = {
        "t1": SourceCredibility(tier=1, group=None),
        "t3_a": SourceCredibility(tier=3, group=None),
        "t3_b": SourceCredibility(tier=3, group=None),
        "t3_c": SourceCredibility(tier=3, group=None),
        "t3_d": SourceCredibility(tier=3, group=None),
    }
    config = _config(credibility, max_clusters_per_run=3)
    items = [
        _item("t1", "https://x/1", T0 - timedelta(days=5)),
        _item("t3_a", "https://x/2", T0 - timedelta(days=4)),
        _item("t3_b", "https://x/3", T0 - timedelta(days=3)),
        _item("t3_c", "https://x/4", T0 - timedelta(days=2)),
        _item("t3_d", "https://x/5", T0 - timedelta(days=1)),
    ]
    # Five pairwise-sub-threshold vectors (all cosines < 0.62) in 3D.
    vectors = [
        _unit(1, 0, 0), _unit(0, 1, 0), _unit(0, 0, 1),
        _unit(1, 1, 1), _unit(1, -1, 1),
    ]
    clusters = cluster_items(items, vectors, 0.62)
    assert len(clusters) == 5

    log = _Log()
    kept = rank_and_truncate(clusters, 3, config, log)
    assert len(kept) == 3
    assert kept[0].members[0].source_id == "t1"  # tier 1 wins despite age
    # Tier ties break on recency: the two newest tier-3 clusters survive.
    kept_sources = {c.members[0].source_id for c in kept}
    assert kept_sources == {"t1", "t3_d", "t3_c"}
    assert any("dropping 2 keys" in m for m in log.messages)


def test_under_cap_logs_nothing_and_keeps_priority_order():
    credibility = {
        "t3": SourceCredibility(tier=3, group=None),
        "t1": SourceCredibility(tier=1, group=None),
    }
    config = _config(credibility)
    items = [_item("t3", "https://x/1"), _item("t1", "https://x/2")]
    clusters = cluster_items(items, [_unit(1, 0), _unit(0, 1)], 0.62)
    log = _Log()
    kept = rank_and_truncate(clusters, 5, config, log)
    assert len(kept) == 2
    assert kept[0].members[0].source_id == "t1"
    assert log.messages == []


def test_cluster_stage_end_to_end():
    credibility = {"src_a": SourceCredibility(tier=1, group=None),
                   "src_b": SourceCredibility(tier=3, group=None)}
    config = _config(credibility)
    items, vectors = _event_cluster_fixture()
    ctx = _Ctx(config=config, items=items, embeddings=vectors)
    ClusterStage(config, _Log()).run(ctx)
    assert len(ctx.clusters) == 2
    assert ctx.counters["cluster"] == 2


def test_cluster_stage_empty_items():
    config = _config({})
    ctx = _Ctx(config=config, items=[], embeddings=[])
    ClusterStage(config, _Log()).run(ctx)
    assert ctx.clusters == []
    assert ctx.counters["cluster"] == 0


def test_length_mismatch_raises_loudly():
    config = _config({})
    ctx = _Ctx(config=config, items=[_item("s", "https://x/1")], embeddings=[])
    with pytest.raises(ValueError, match="embeddings"):
        ClusterStage(config, _Log()).run(ctx)
