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
from agent.pipeline.cluster import (
    ClusterStage, cluster_items, rank_and_truncate, split_at_cap,
)
from agent.pipeline.relevance import validate_relevance
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

# Minimal relevance config for fix-2 tests -- "hormuz"/"هرمز" is on-mission,
# anything else scores 0. Not the real config/relevance.yaml: these tests
# are about the on_mission SORT MECHANISM, not real keyword coverage.
_RELEVANCE = validate_relevance({
    "weights": {"iran_direct": 8},
    "keywords": {"iran_direct": ["hormuz", "هرمز"]},
})


def _settings(**overrides) -> Settings:
    raw = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        raw["pipeline"][key] = value
    return Settings.from_dict(raw)


def _config(credibility: dict, relevance=None, **overrides) -> Config:
    return Config(settings=_settings(**overrides), credibility=credibility,
                  relevance=relevance)


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
    assert any("dropping 2 " in m for m in log.messages)


def test_cap_keeps_corroborated_cluster_over_fresher_single_source():
    # The 2026-09-05 16:18 regression, in miniature. The old priority key
    # was (tier, recency, size): size was the LAST tiebreak and, because
    # timestamps are effectively always distinct, it never fired -- inside a
    # tier the ranking was pure recency. That run cut 36 of 76 clusters and
    # left 23 single-member clusters standing simply because they were
    # fresher. Independence now sits ahead of recency: a three-outlet story
    # must not lose a cap slot to a newer one-outlet post.
    credibility = {
        "wire_a": SourceCredibility(tier=2, group="a"),
        "wire_b": SourceCredibility(tier=2, group="b"),
        "wire_c": SourceCredibility(tier=2, group="c"),
        "solo": SourceCredibility(tier=2, group="d"),
    }
    config = _config(credibility, max_clusters_per_run=1)
    items = [
        _item("wire_a", "https://x/1", T0 - timedelta(hours=6)),
        _item("wire_b", "https://x/2", T0 - timedelta(hours=5)),
        _item("wire_c", "https://x/3", T0 - timedelta(hours=4)),
        _item("solo", "https://x/4", T0 - timedelta(hours=1)),  # freshest
    ]
    same, other = _unit(1, 0), _unit(0, 1)
    clusters = cluster_items(items, [same, same, same, other], 0.62)
    assert sorted(len(c.members) for c in clusters) == [1, 3]

    kept, dropped = split_at_cap(clusters, 1, config, _Log())
    assert len(kept) == 1
    assert len(kept[0].members) == 3, "the 3-source cluster must win the slot"
    assert kept[0].independent_count(credibility) == 3
    # The overflow is returned, not discarded, so it can be audited in
    # chosen.csv instead of vanishing into a line of hex keys.
    assert [c.members[0].source_id for c in dropped] == ["solo"]


def test_independent_count_collapses_shared_groups_and_ignores_leads():
    # Reuters + AP on one wire, or BBC English + BBC Persian, is ONE
    # independent source (rulebook Step 1). Leads never count at all.
    credibility = {
        "bbc_en": SourceCredibility(tier=1, group="bbc"),
        "bbc_fa": SourceCredibility(tier=1, group="bbc"),
        "unlisted": SourceCredibility(tier=3, group=None),
        "tipster": SourceCredibility(tier="lead", group=None),
    }
    items = [
        _item("bbc_en", "https://x/1"), _item("bbc_fa", "https://x/2"),
        _item("unlisted", "https://x/3"), _item("tipster", "https://x/4"),
    ]
    same = _unit(1, 0)
    clusters = cluster_items(items, [same] * 4, 0.62)
    assert len(clusters) == 1
    # bbc_en + bbc_fa collapse to one; unlisted (group None) stands alone;
    # tipster is a lead and contributes nothing.
    assert clusters[0].independent_count(credibility) == 2


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


# --- Cluster.corroborating_count (fix 3, 2026-09-06) -----------------------
# independent_count (above) measures amplification across ALL tiers -- 17
# owner Telegram channels carry group: null, so a pile-up of reposts counts
# as "independence" there. corroborating_count is the rulebook Step 1
# definition validate.py already used for claim_status: distinct GROUPS
# among tier-1/2 members only. priority.py's cap key uses this one, not
# independent_count, so a tier-3 repost pile-up cannot out-rank a real
# two-source story.

def test_corroborating_count_ignores_pure_tier3_pileup():
    credibility = {
        "t3_a": SourceCredibility(tier=3, group=None),
        "t3_b": SourceCredibility(tier=3, group=None),
        "t3_c": SourceCredibility(tier=3, group=None),
    }
    items = [_item("t3_a", "https://x/1"), _item("t3_b", "https://x/2"),
             _item("t3_c", "https://x/3")]
    clusters = cluster_items(items, [_unit(1, 0)] * 3, 0.62)
    assert len(clusters) == 1
    # independent_count (all tiers) would be 3 here -- corroborating_count
    # must be 0: none of them is tier-1/2.
    assert clusters[0].independent_count(credibility) == 3
    assert clusters[0].corroborating_count(credibility) == 0


def test_corroborating_count_collapses_same_group_tier2():
    credibility = {
        "wire_en": SourceCredibility(tier=2, group="wire"),
        "wire_fa": SourceCredibility(tier=2, group="wire"),
    }
    items = [_item("wire_en", "https://x/1"), _item("wire_fa", "https://x/2")]
    clusters = cluster_items(items, [_unit(1, 0)] * 2, 0.62)
    assert len(clusters) == 1
    assert clusters[0].corroborating_count(credibility) == 1


def test_corroborating_count_mixed_cluster_counts_only_tier12():
    credibility = {
        "wire_a": SourceCredibility(tier=1, group="a"),
        "wire_b": SourceCredibility(tier=2, group="b"),
        "unlisted_tg": SourceCredibility(tier=3, group=None),
    }
    items = [_item("wire_a", "https://x/1"), _item("wire_b", "https://x/2"),
             _item("unlisted_tg", "https://x/3")]
    clusters = cluster_items(items, [_unit(1, 0)] * 3, 0.62)
    assert len(clusters) == 1
    assert clusters[0].corroborating_count(credibility) == 2


def test_corroborating_count_never_counts_leads():
    credibility = {
        "wire_a": SourceCredibility(tier=2, group="a"),
        "tipster": SourceCredibility(tier="lead", group=None),
    }
    items = [_item("wire_a", "https://x/1"), _item("tipster", "https://x/2")]
    clusters = cluster_items(items, [_unit(1, 0)] * 2, 0.62)
    assert len(clusters) == 1
    assert clusters[0].corroborating_count(credibility) == 1


# --- on_mission cap ordering (fix 2, 2026-09-06) ---------------------------
# The 2026-09-05 16:18 run made tier an absolute wall in the cap key, cutting
# 24 of 40 kept clusters that later came back irrelevant/clickbait while
# corroborated Iranian-naval-escalation clusters were cut with no LLM call.
# on_mission (binary, from config/relevance.yaml scoring) now leads the key
# as a DEMOTION -- off-mission still gets a slot on a quiet day.

def _hormuz_item(source_id: str, url: str, published_at=None) -> Item:
    return Item(source_id=source_id, url=url, title="Hormuz strait incident",
                body="Naval escalation near hormuz", published_at=published_at,
                lang="en", raw_hash="h" * 8)


def test_on_mission_outranks_higher_tier_off_mission():
    credibility = {
        "t1": SourceCredibility(tier=1, group=None),   # off-mission, tier 1
        "t3": SourceCredibility(tier=3, group=None),   # on-mission, tier 3
    }
    config = _config(credibility, relevance=_RELEVANCE, max_clusters_per_run=1)
    items = [
        _item("t1", "https://x/1", T0),          # no "hormuz" -- off-mission
        _hormuz_item("t3", "https://x/2", T0),   # on-mission
    ]
    clusters = cluster_items(items, [_unit(1, 0), _unit(0, 1)], 0.62)
    assert len(clusters) == 2

    kept, dropped = split_at_cap(clusters, 1, config, _Log())
    assert len(kept) == 1
    assert kept[0].members[0].source_id == "t3", (
        "on-mission tier-3 must outrank off-mission tier-1"
    )
    assert dropped[0].members[0].source_id == "t1"


def test_off_mission_still_kept_below_cap():
    # A demotion, never a drop: on a quiet day (nothing to compete with) the
    # off-mission cluster still survives the cap.
    credibility = {"t1": SourceCredibility(tier=1, group=None)}
    config = _config(credibility, relevance=_RELEVANCE, max_clusters_per_run=5)
    items = [_item("t1", "https://x/1", T0)]
    clusters = cluster_items(items, [_unit(1, 0)], 0.62)
    kept, dropped = split_at_cap(clusters, 5, config, _Log())
    assert len(kept) == 1
    assert dropped == []


def test_on_mission_lead_cannot_outrank_off_mission_tier():
    # Round-3 review, fix 1c: on_mission is now gated on tier_weight > 0 --
    # a `lead` cluster (weight 0.0, LEAD_HANDLING.md: leads cannot drive
    # priority) that matches a relevance keyword must NOT be promoted ahead
    # of an off-mission tier-2 cluster just because on_mission is the top
    # sort term.
    credibility = {
        "t2": SourceCredibility(tier=2, group=None),      # off-mission, tier 2
        "lead1": SourceCredibility(tier="lead", group=None),  # on-mission, lead
    }
    config = _config(credibility, relevance=_RELEVANCE, max_clusters_per_run=1)
    items = [
        _item("t2", "https://x/1", T0),               # no "hormuz" -- off-mission
        _hormuz_item("lead1", "https://x/2", T0),      # on-mission but a lead
    ]
    clusters = cluster_items(items, [_unit(1, 0), _unit(0, 1)], 0.62)
    assert len(clusters) == 2

    kept, dropped = split_at_cap(clusters, 1, config, _Log())
    assert kept[0].members[0].source_id == "t2", (
        "a lead cluster must never be promoted by on_mission"
    )
    assert dropped[0].members[0].source_id == "lead1"


def test_on_mission_ordering_is_deterministic():
    credibility = {
        "t1": SourceCredibility(tier=1, group=None),
        "t3": SourceCredibility(tier=3, group=None),
    }
    config = _config(credibility, relevance=_RELEVANCE, max_clusters_per_run=2)
    items = [
        _item("t1", "https://x/1", T0),
        _hormuz_item("t3", "https://x/2", T0),
    ]
    clusters = cluster_items(items, [_unit(1, 0), _unit(0, 1)], 0.62)
    first = [c.key for c in rank_and_truncate(clusters, 2, config, _Log())]
    second = [c.key for c in rank_and_truncate(clusters, 2, config, _Log())]
    assert first == second
