"""Unit tests for pipeline/validate.py: independence by GROUP (decision 4),
deterministic claim_status, the lead split, and the silent lead_outcomes
writes. No LLM, no clock -- pure arithmetic over credibility.yaml shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent.collectors.base import Item
from agent.config import SourceCredibility
from agent.memory import db as memory_db
from agent.memory.event_models import Event, read_events
from agent.memory.lead_models import read_lead_outcomes
from agent.pipeline.cluster import Cluster
from agent.pipeline.validate import ValidateStage, classify_event, independent_groups

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


def _cred(**entries) -> dict:
    return entries


def _item(source_id: str, url: str) -> Item:
    return Item(source_id=source_id, url=url, title="t", body="b",
                published_at=NOW, lang="en", raw_hash="h" * 8)


def _cluster(members: list[Item]) -> Cluster:
    cluster = Cluster(key="")
    for m in members:
        cluster.add(m, [1.0])
    return cluster


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    events: list = field(default_factory=list)
    lead_events: list = field(default_factory=list)
    db: object = None
    now: datetime = NOW
    counters: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# classify_event / independent_groups
# ---------------------------------------------------------------------------


def test_same_group_sources_are_not_independent():
    # BBC English + BBC Persian: one newsroom, one report (decision 4).
    credibility = _cred(
        bbc_en=SourceCredibility(tier=2, group="bbc"),
        bbc_fa=SourceCredibility(tier=2, group="bbc"),
    )
    groups = independent_groups(["bbc_en", "bbc_fa"], credibility)
    assert groups == {"bbc"}


def test_same_wire_group_ap_reuters_are_not_independent():
    credibility = _cred(
        reuters_gnews=SourceCredibility(tier=2, group="wire_west"),
        ap_gnews=SourceCredibility(tier=2, group="wire_west"),
    )
    groups = independent_groups(["reuters_gnews", "ap_gnews"], credibility)
    assert len(groups) == 1


def test_different_groups_count_independently():
    credibility = _cred(
        reuters_gnews=SourceCredibility(tier=2, group="wire_west"),
        haaretz=SourceCredibility(tier=2, group="israeli_press"),
    )
    groups = independent_groups(["reuters_gnews", "haaretz"], credibility)
    assert len(groups) == 2


def test_tier3_and_lead_never_corroborate():
    credibility = _cred(
        t2=SourceCredibility(tier=2, group="g"),
        t3=SourceCredibility(tier=3, group="other"),
        lead=SourceCredibility(tier="lead", group="leadg"),
    )
    groups = independent_groups(["t2", "t3", "lead"], credibility)
    assert groups == {"g"}


def test_null_group_falls_back_to_own_id():
    credibility = _cred(
        a=SourceCredibility(tier=2, group=None),
        b=SourceCredibility(tier=2, group=None),
    )
    groups = independent_groups(["a", "b"], credibility)
    assert groups == {"a", "b"}


def test_single_tier2_source_is_unconfirmed_not_rumour():
    credibility = _cred(s=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("s", "https://x/1")])
    status, groups, leads = classify_event(cluster, credibility)
    assert status == "unconfirmed"  # one corroborating group
    assert groups == {"g"}
    assert leads == set()


def test_tier3_only_cluster_is_rumour():
    # Rulebook Step 1: >=2 independent NON-tier-3 sources. Tier 3 never
    # corroborates, so a tier-3-only cluster is a rumour.
    credibility = _cred(
        t3a=SourceCredibility(tier=3, group="ga"),
        t3b=SourceCredibility(tier=3, group="gb"),
    )
    cluster = _cluster([_item("t3a", "https://x/1"), _item("t3b", "https://x/2")])
    status, groups, leads = classify_event(cluster, credibility)
    assert status == "rumour"
    assert groups == set()


def test_two_independent_groups_are_likely():
    credibility = _cred(
        a=SourceCredibility(tier=1, group="ga"),
        b=SourceCredibility(tier=2, group="gb"),
    )
    cluster = _cluster([_item("a", "https://x/1"), _item("b", "https://x/2")])
    status, groups, _ = classify_event(cluster, credibility)
    assert status == "likely"
    assert len(groups) == 2


def test_lead_only_cluster_is_lead_only_not_rumour():
    credibility = _cred(lead=SourceCredibility(tier="lead", group="leadg"))
    cluster = _cluster([_item("lead", "https://x/1"), _item("lead", "https://x/2")])
    status, groups, leads = classify_event(cluster, credibility)
    assert status == "lead_only"
    assert leads == {"lead"}


# ---------------------------------------------------------------------------
# ValidateStage
# ---------------------------------------------------------------------------


def _stage(credibility) -> ValidateStage:
    return ValidateStage(credibility, _Log())


def test_stage_splits_lead_only_events_out_of_main_feed():
    credibility = _cred(
        t2=SourceCredibility(tier=2, group="g"),
        lead=SourceCredibility(tier="lead", group="leadg"),
    )
    main_cluster = _cluster([_item("t2", "https://x/1")])
    lead_cluster = _cluster([_item("lead", "https://x/2")])
    ctx = _Ctx(
        clusters=[main_cluster, lead_cluster],
        events=[Event(event_key=main_cluster.key, summary="s"),
                Event(event_key=lead_cluster.key, summary="lead s")],
    )
    _stage(credibility).run(ctx)
    assert len(ctx.events) == 1
    # Single tier-2 source = one corroborating group = unconfirmed.
    assert ctx.events[0].claim_status == "unconfirmed"
    assert len(ctx.lead_events) == 1
    assert ctx.counters["validate"] == 1


class _DictEmbedder:
    """Maps exact texts to fixed unit vectors -- the anti-repetition
    matching is what is under test, not the model."""

    def __init__(self, vectors):
        self._vectors = vectors
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            vec = self._vectors.get(t)
            if vec is None:
                vec = [0.0, 0.0, 1.0]  # unknown -> distinct
            out.append(vec)
        return out


def test_repeat_follow_up_is_dropped(tmp_path):
    """Owner decision 2026-08-29: a follow-up story on an event the owner
    already saw must not appear again. New event whose summary embeds to
    the same vector as a recent stored event -> dropped; a distinct one ->
    kept. Local embedder, zero LLM calls."""
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    from agent.memory.event_models import insert_events, mark_delivered
    insert_events(conn, [Event(event_key="o" * 16, summary="old summary",
                               category="politics", source_count=1,
                               first_seen_at=NOW, last_updated_at=NOW)])
    # The repeat window matches DELIVERED events only (owner decision
    # 2026-08-30) -- this prior event was received.
    mark_delivered(conn, ["o" * 16], NOW)
    conn.close()

    cluster_same = _cluster([_item("t2", "https://x/followup")])
    cluster_diff = _cluster([_item("t2", "https://x/genuinely-new")])
    embedder = _DictEmbedder({
        "old summary": [1.0, 0.0, 0.0],
        "follow-up on the same event": [1.0, 0.0, 0.0],   # identical -> repeat
        "a different event entirely": [0.0, 1.0, 0.0],    # orthogonal -> kept
    })
    ctx = _Ctx(
        clusters=[cluster_same, cluster_diff],
        events=[
            Event(event_key=cluster_same.key, summary="follow-up on the same event",
                  category="politics", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
            Event(event_key=cluster_diff.key, summary="a different event entirely",
                  category="politics", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
        ],
    )
    ctx.db = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    ctx.embedder = embedder
    ctx.config = _ctx_config()
    try:
        _stage(credibility).run(ctx)
    finally:
        ctx.db.close()

    kept_summaries = [e.summary for e in ctx.events]
    assert "follow-up on the same event" not in kept_summaries
    assert "a different event entirely" in kept_summaries


def test_this_runs_own_rows_are_not_self_repeat_dropped(tmp_path):
    """Regression 2026-08-30: the production sequence is understand
    INSERTING this run's events into the events table BEFORE validate
    reads the repeat window. Unfiltered, the fresh event's own row is
    among `recent` and self-cosine (1.0) drops it -- every run shipped
    the nothing-new one-liner. This run's keys must be excluded from the
    window, while a follow-up matching a PRIOR run's event still drops."""
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    from agent.memory.event_models import insert_events, mark_delivered
    insert_events(conn, [Event(event_key="o" * 16, summary="old summary",
                               category="politics", source_count=1,
                               first_seen_at=NOW, last_updated_at=NOW)])
    # The prior event was RECEIVED, so it belongs in the repeat window.
    mark_delivered(conn, ["o" * 16], NOW)
    conn.close()

    cluster_fresh = _cluster([_item("t2", "https://x/fresh")])
    cluster_followup = _cluster([_item("t2", "https://x/followup")])
    fresh_event = Event(event_key=cluster_fresh.key, summary="fresh summary",
                        category="politics", source_count=1,
                        first_seen_at=NOW, last_updated_at=NOW)
    followup_event = Event(event_key=cluster_followup.key, summary="old summary",
                           category="politics", source_count=1,
                           first_seen_at=NOW, last_updated_at=NOW)
    embedder = _DictEmbedder({
        "old summary": [1.0, 0.0, 0.0],
        # Orthogonal to the old row: only the self-match could drop it.
        "fresh summary": [0.0, 1.0, 0.0],
    })
    ctx = _Ctx(
        clusters=[cluster_fresh, cluster_followup],
        events=[fresh_event, followup_event],
    )
    ctx.db = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    # The understand half of the real sequence: this run's rows exist.
    insert_events(ctx.db, [fresh_event, followup_event])
    ctx.embedder = embedder
    ctx.config = _ctx_config()
    try:
        _stage(credibility).run(ctx)
    finally:
        ctx.db.close()

    kept_summaries = [e.summary for e in ctx.events]
    assert kept_summaries == ["fresh summary"]


def _ctx_config():
    from agent.config import Config
    from agent.settings import Settings
    import yaml as _yaml
    from pathlib import Path
    fixture = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
    settings = Settings.from_dict(_yaml.safe_load(fixture.read_text(encoding="utf-8")))
    return Config(settings=settings, credibility={})


def test_stage_persists_validation_and_lead_outcomes(tmp_path):
    credibility = _cred(
        a=SourceCredibility(tier=1, group="ga"),
        b=SourceCredibility(tier=2, group="gb"),
        lead=SourceCredibility(tier="lead", group="leadg"),
    )
    cluster = _cluster([
        _item("a", "https://x/1"), _item("b", "https://x/2"), _item("lead", "https://x/3"),
    ])
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    try:
        # The understand stage INSERTs events; validate UPDATEs them -- the
        # real sequence. Simulate the insert half first (dates mandatory:
        # events.first_seen_at is NOT NULL).
        from agent.memory.event_models import insert_events
        insert_events(conn, [Event(event_key=cluster.key, summary="s", source_count=3,
                                   first_seen_at=NOW, last_updated_at=NOW)])
    finally:
        conn.close()
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    ctx = _Ctx(clusters=[cluster],
               events=[Event(event_key=cluster.key, summary="s", source_count=3,
                             first_seen_at=NOW, last_updated_at=NOW)],
               db=conn)
    try:
        _stage(credibility).run(ctx)
    finally:
        conn.close()
    assert ctx.events[0].claim_status == "likely"
    assert ctx.events[0].independent_count == 2

    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    try:
        events = read_events(conn)
        outcomes = read_lead_outcomes(conn)
    finally:
        conn.close()
    assert events[0].claim_status == "likely"
    assert events[0].independent_count == 2
    assert outcomes == [("lead", cluster.key, "confirmed")]


def test_stored_but_never_delivered_event_does_not_block(tmp_path):
    """Owner decision 2026-08-30: an event the owner never RECEIVED (below
    min_score, dropped as a repeat, or by the Persian gate) must not
    suppress its own follow-up. The 72h window matches DELIVERED events
    only -- the same summary, stored but never sent, blocks nothing."""
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    from agent.memory.event_models import insert_events
    insert_events(conn, [Event(event_key="o" * 16, summary="old summary",
                               category="politics", source_count=1,
                               first_seen_at=NOW, last_updated_at=NOW)])
    conn.close()  # NOT marked delivered: the owner never saw it

    cluster = _cluster([_item("t2", "https://x/followup")])
    embedder = _DictEmbedder({"old summary": [1.0, 0.0, 0.0]})
    ctx = _Ctx(
        clusters=[cluster],
        events=[Event(event_key=cluster.key, summary="old summary",
                      category="politics", source_count=1,
                      first_seen_at=NOW, last_updated_at=NOW)],
    )
    ctx.db = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    ctx.embedder = embedder
    ctx.config = _ctx_config()
    try:
        _stage(credibility).run(ctx)
    finally:
        ctx.db.close()

    assert [e.summary for e in ctx.events] == ["old summary"]
