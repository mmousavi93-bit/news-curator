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


def test_null_group_falls_back_to_prefixed_self_id():
    # Fix F, 2026-09-06 review: the fallback must be a PREFIXED
    # self-identifier (matching cluster.py's `__self__:<id>` convention),
    # not the bare source id -- see the collision test below for why.
    credibility = _cred(
        a=SourceCredibility(tier=2, group=None),
        b=SourceCredibility(tier=2, group=None),
    )
    groups = independent_groups(["a", "b"], credibility)
    assert groups == {"__self__:a", "__self__:b"}


def test_null_group_fallback_does_not_collide_with_a_real_group_name():
    # The bug fix F closes: 19 strings in credibility.yaml are
    # simultaneously a source id (with group: null) and another source's
    # EXPLICIT `group:` value. A bare-id fallback for "al_jazeera" would
    # silently merge with a second, unrelated source explicitly declaring
    # group: "al_jazeera" -- two independent reports would count as one,
    # UNDER-counting corroboration. With the source-id fallback (pre-fix),
    # independent_groups(["al_jazeera", "other_outlet"], ...) would wrongly
    # return a single group ({"al_jazeera"}). The prefixed fallback keeps
    # them apart.
    credibility = _cred(
        al_jazeera=SourceCredibility(tier=2, group=None),
        other_outlet=SourceCredibility(tier=2, group="al_jazeera"),
    )
    groups = independent_groups(["al_jazeera", "other_outlet"], credibility)
    assert groups == {"__self__:al_jazeera", "al_jazeera"}
    assert len(groups) == 2


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
    # independent_count=1 / claim_status="unconfirmed" match what a real
    # single-tier-2-source event would have been validated to (fix 1,
    # 2026-09-06): with the DEFAULT independent_count=0 the new event's
    # own independent_count=1 would exceed it and bypass condition (b)
    # would fire, turning this into a kept follow-up instead of a drop.
    insert_events(conn, [Event(event_key="o" * 16, summary="old summary",
                               category="politics", source_count=1,
                               independent_count=1, claim_status="unconfirmed",
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


def test_lead_event_bypasses_the_repeat_gate_entirely(tmp_path):
    """Fix D REVERTED, round-2 review 2026-09-06 (see validate.py's ValidateStage.run
    comment for the full rationale): a round-1 change routed lead events through
    drop_repeats/drop_same_run_dups so a repeating lead would eventually be
    hard-dropped. That never worked -- a lead event never enters
    ctx.compose_kept_keys (compose.py excludes leads from the received-marker
    keys by design), so it is never mark_delivered, so the 72h repeat window
    (which matches DELIVERED events only) could never contain a lead prior in
    the first place; the "old lead summary" prior below, even though it embeds
    identically to the new one, is UNREACHABLE by construction. Leads are split
    out during classification and never touch either anti-repetition pass:
    ctx.lead_events carries this event through completely unchanged, and it
    must never appear in ctx.repeat_dropped."""
    credibility = _cred(lead=SourceCredibility(tier="lead", group="leadg"))
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    from agent.memory.event_models import insert_events, mark_delivered
    insert_events(conn, [Event(event_key="o" * 16, summary="old lead summary",
                               category="military", source_count=1,
                               independent_count=0, claim_status="unconfirmed",
                               first_seen_at=NOW, last_updated_at=NOW)])
    mark_delivered(conn, ["o" * 16], NOW)
    conn.close()

    lead_cluster = _cluster([_item("lead", "https://x/followup")])
    embedder = _DictEmbedder({
        "old lead summary": [1.0, 0.0, 0.0],
        "new lead summary": [1.0, 0.0, 0.0],  # identical -> would match, if reachable
    })
    lead_event = Event(event_key=lead_cluster.key, summary="new lead summary",
                       category="military", source_count=1,
                       first_seen_at=NOW, last_updated_at=NOW)
    ctx = _Ctx(clusters=[lead_cluster], events=[lead_event])
    ctx.db = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    ctx.embedder = embedder
    ctx.config = _ctx_config()
    try:
        _stage(credibility).run(ctx)
    finally:
        ctx.db.close()

    assert ctx.events == []
    assert ctx.repeat_dropped == []
    assert len(ctx.lead_events) == 1
    assert ctx.lead_events[0].event_key == lead_cluster.key
    assert ctx.lead_events[0].summary == "new lead summary"


def test_same_run_duplicate_pair_collapses_to_larger_cluster(tmp_path):
    # 2026-08-30: the same Hormuz tanker incident was delivered twice in one
    # digest -- _drop_repeats only compares against PREVIOUS runs, so two new
    # events telling the same story never met each other. The same-run pass
    # collapses the pair; the larger cluster survives.
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    big = _cluster([_item("t2", "https://x/a1"), _item("t2", "https://x/a2")])
    small = _cluster([_item("t2", "https://x/b1")])
    embedder = _DictEmbedder({
        "tanker hit in hormuz": [1.0, 0.0, 0.0],
        "same tanker hit again": [1.0, 0.0, 0.0],   # identical story
    })
    ctx = _Ctx(
        clusters=[big, small],
        events=[
            Event(event_key=big.key, summary="tanker hit in hormuz",
                  category="military", source_count=2,
                  first_seen_at=NOW, last_updated_at=NOW),
            Event(event_key=small.key, summary="same tanker hit again",
                  category="military", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
        ],
    )
    ctx.db = conn
    ctx.embedder = embedder
    ctx.config = _ctx_config()
    try:
        _stage(credibility).run(ctx)
    finally:
        conn.close()
    assert [e.summary for e in ctx.events] == ["tanker hit in hormuz"]
    assert [e.event_key for e in ctx.repeat_dropped] == [small.key]


def test_same_run_dedup_ranks_independent_count_ahead_of_member_count(tmp_path):
    # Fix 2 part 2, round-2 review: drop_same_run_dups' survivor was chosen on
    # len(cluster.members) ALONE, with zero tier awareness -- a pile of
    # tier-3 reposts could outnumber and delete a single corroborated
    # tier-1/2 report of the same story. Pile-up: 3 tier-3 members, no two
    # groups ever count (tier 3 never corroborates -- classify_event yields
    # independent_count=0). Corroborated: 1 tier-2 member, one group
    # (independent_count=1). Same story (identical embedding vector) -- the
    # corroborated single-member cluster must survive despite having FEWER
    # members.
    credibility = _cred(
        t3a=SourceCredibility(tier=3, group="ga"),
        t3b=SourceCredibility(tier=3, group="gb"),
        t3c=SourceCredibility(tier=3, group="gc"),
        t2=SourceCredibility(tier=2, group="g"),
    )
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    pile = _cluster([
        _item("t3a", "https://x/p1"), _item("t3b", "https://x/p2"), _item("t3c", "https://x/p3"),
    ])
    corroborated = _cluster([_item("t2", "https://x/c1")])
    embedder = _DictEmbedder({
        "pile-up repost": [1.0, 0.0, 0.0],
        "corroborated report": [1.0, 0.0, 0.0],  # identical story
    })
    ctx = _Ctx(
        clusters=[pile, corroborated],
        events=[
            Event(event_key=pile.key, summary="pile-up repost",
                  category="military", source_count=3,
                  first_seen_at=NOW, last_updated_at=NOW),
            Event(event_key=corroborated.key, summary="corroborated report",
                  category="military", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
        ],
    )
    ctx.db = conn
    ctx.embedder = embedder
    ctx.config = _ctx_config()
    try:
        _stage(credibility).run(ctx)
    finally:
        conn.close()
    assert [e.summary for e in ctx.events] == ["corroborated report"]
    assert [e.event_key for e in ctx.repeat_dropped] == [pile.key]


def test_same_run_distinct_events_both_survive(tmp_path):
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    first = _cluster([_item("t2", "https://x/c1")])
    second = _cluster([_item("t2", "https://x/c2")])
    embedder = _DictEmbedder({
        "one story": [1.0, 0.0, 0.0],
        "another story": [0.0, 1.0, 0.0],
    })
    ctx = _Ctx(
        clusters=[first, second],
        events=[
            Event(event_key=first.key, summary="one story",
                  category="military", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
            Event(event_key=second.key, summary="another story",
                  category="politics", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
        ],
    )
    ctx.db = conn
    ctx.embedder = embedder
    ctx.config = _ctx_config()
    try:
        _stage(credibility).run(ctx)
    finally:
        conn.close()
    assert len(ctx.events) == 2
    assert ctx.repeat_dropped == []


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
    # independent_count=1 / claim_status="unconfirmed" (fix 1, 2026-09-06):
    # see test_repeat_follow_up_is_dropped -- the default independent_count=0
    # would let bypass condition (b) turn followup_event into a kept
    # follow-up instead of the excluded row this test asserts.
    insert_events(conn, [Event(event_key="o" * 16, summary="old summary",
                               category="politics", source_count=1,
                               independent_count=1, claim_status="unconfirmed",
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


def test_repeat_reason_survives_a_same_run_duplicate_drop(tmp_path):
    # Round-3 review, fix 4: validate.py's OLD `{**repeat_reasons,
    # **same_run_reasons}` merge let a same-run drop silently overwrite the
    # cross-run repeat-gate reason for the same event_key. Event A survives
    # drop_repeats as a MID-band follow-up (matches a delivered prior at
    # sim=0.70, inside [event_match_threshold=0.55, event_repeat_threshold=
    # 0.80) -- floor met, no development required) but is then itself
    # dropped by drop_same_run_dups against event B, a more-corroborated
    # same-run event (sim=0.638 >= event_match_threshold). The merged reason
    # must carry BOTH the repeat-gate context (which prior it matched) and
    # the same-run-dup context (which same-run event beat it) -- neither
    # pass's information may be silently dropped.
    credibility = _cred(
        ga=SourceCredibility(tier=2, group="ga"),
        gb1=SourceCredibility(tier=2, group="gb1"),
        gb2=SourceCredibility(tier=2, group="gb2"),
    )
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    from agent.memory.event_models import insert_events, mark_delivered
    insert_events(conn, [Event(event_key="o" * 16, summary="old summary",
                               category="politics", source_count=1,
                               independent_count=1, claim_status="unconfirmed",
                               first_seen_at=NOW, last_updated_at=NOW)])
    mark_delivered(conn, ["o" * 16], NOW)
    conn.close()

    cluster_a = _cluster([_item("ga", "https://x/a1")])
    cluster_b = _cluster([_item("gb1", "https://x/b1"), _item("gb2", "https://x/b2")])
    embedder = _DictEmbedder({
        "old summary": [1.0, 0.0, 0.0],
        # cos(A, old) = 0.7 -- MID band (0.55 <= sim < 0.80).
        "event a": [0.7, 0.714143, 0.0],
        # cos(A, B) = 0.638486 -- above event_match_threshold (0.55), so
        # drop_same_run_dups treats them as the same story; cos(B, old) =
        # 0.3, below 0.55, so B never touches the repeat gate at all.
        "event b": [0.3, 0.6, 0.74162],
    })
    event_a = Event(event_key=cluster_a.key, summary="event a",
                    category="military", source_count=1,
                    first_seen_at=NOW, last_updated_at=NOW)
    event_b = Event(event_key=cluster_b.key, summary="event b",
                    category="military", source_count=2,
                    first_seen_at=NOW, last_updated_at=NOW)
    ctx = _Ctx(clusters=[cluster_a, cluster_b], events=[event_a, event_b])
    ctx.db = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    ctx.embedder = embedder
    ctx.config = _ctx_config()
    try:
        _stage(credibility).run(ctx)
    finally:
        ctx.db.close()

    # B (independent_count=2, two members) outranks A (independent_count=1,
    # one member) in drop_same_run_dups' (independent_count, size) key -- A
    # is the one dropped, B survives.
    assert [e.summary for e in ctx.events] == ["event b"]
    assert [e.event_key for e in ctx.repeat_dropped] == [cluster_a.key]

    reason = ctx.repeat_drop_reasons[cluster_a.key]
    # The repeat-gate half: A matched the delivered prior in the MID band
    # and cleared the score floor (military, fresh, 1 group -> 13 >= 11).
    assert "band=mid" in reason
    assert "kept=above_floor" in reason
    # The same-run-dedup half: A was then cut as a same-run duplicate of B.
    assert "same_run_dup" in reason
    # Both halves present, joined, neither overwriting the other.
    assert reason.index("band=mid") < reason.index("same_run_dup")


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
