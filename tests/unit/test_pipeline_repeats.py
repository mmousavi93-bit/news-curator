"""Unit tests for pipeline/repeats.py: the TWO-BAND repeat gate (fix 1,
round-2 review 2026-09-06, replacing the single-threshold conjunction from
the first review round). A matched "repeat" (cosine >= event_match_threshold
against a previously DELIVERED event) is judged by which band its MAXIMUM
similarity across every matched prior falls into:

  HIGH band (sim >= event_repeat_threshold, 0.80): genuinely the same story
  retold -- the original conjunction applies: score >= repeat_bypass_score
  AND material development over the HIGH-WATER MARK across every matched
  prior (independent_count grew, or claim_status upgraded). Either
  condition alone, or both failing, is a hard drop.

  MID band (event_match_threshold <= sim < event_repeat_threshold): related
  but a DIFFERENT story -- no development test, since ratcheting a new
  story against an old one's corroboration is meaningless. Score floor
  alone decides: >= repeat_bypass_score survives as a follow-up, below it
  is dropped.

Round-1 finding (still true for the HIGH band): the OLD code was a 3-way
DISJUNCTION (any one of score-floor / count-growth / claim-upgrade
bypassed), which let a fresh corroborated story clear the floor alone with
no reference to the prior, AND let a same-run-close-but-weaker matched
prior (argmax similarity only) stand in for a stronger, already-delivered
one when judging "development" -- fixed by the conjunction + high-water
mark across every matched prior.

Round-2 finding: the single threshold (0.55) was being used as BOTH "worth
comparing" and "same story", and the measured run showed all 12 repeat-drops
sitting at 0.55-0.70 -- a strike and its retaliation share vocabulary and
land in exactly that range, so the conjunction blacked out real
developments. The MID band exists so a related-but-different story is
judged on importance alone, never against a prior's corroboration.

See test_pipeline_validate.py for the lead-gate interaction and compose's
rendering test
(test_pipeline_compose.py::test_follow_up_event_renders_as_compact_line_not_full_entry).

No LLM, no clock reads (ctx.now injected) -- pure arithmetic + a local
FakeEmbedder, like test_pipeline_validate.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.memory import db as memory_db
from agent.memory.event_models import Event, insert_events, mark_delivered
from agent.pipeline.cluster import Cluster
from agent.pipeline.validate import ValidateStage
from agent.settings import Settings

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"


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


def _item(source_id: str, url: str, published_at=NOW) -> Item:
    return Item(source_id=source_id, url=url, title="t", body="b",
                published_at=published_at, lang="en", raw_hash="h" * 8)


def _cluster(members: list[Item]) -> Cluster:
    cluster = Cluster(key="")
    for m in members:
        cluster.add(m, [1.0])
    return cluster


def _ctx_config() -> Config:
    settings = Settings.from_dict(yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")))
    return Config(settings=settings, credibility={})


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    events: list = field(default_factory=list)
    lead_events: list = field(default_factory=list)
    db: object = None
    now: datetime = NOW
    counters: dict = field(default_factory=dict)


class _DictEmbedder:
    """Maps exact texts to fixed vectors -- the anti-repetition matching is
    what is under test, not the model. Vectors must be unit-length for
    cosine == dot product (repeats.py's contract)."""

    def __init__(self, vectors):
        self._vectors = vectors

    def embed(self, texts):
        out = []
        for t in texts:
            vec = self._vectors.get(t)
            out.append(vec if vec is not None else [0.0, 0.0, 1.0])
        return out


def _run(tmp_path, credibility, new_cluster, new_event, *, priors, vectors
          ) -> tuple[list[Event], "_Ctx"]:
    """priors: list of (key, summary, independent_count, claim_status),
    each stored as a DELIVERED event inside the 72h window. vectors: text
    -> unit vector, covering every prior summary plus new_event.summary."""
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    keys = []
    for key, summary, independent_count, claim_status in priors:
        insert_events(conn, [Event(
            event_key=key, summary=summary, category="politics",
            source_count=1, independent_count=independent_count,
            claim_status=claim_status, first_seen_at=NOW, last_updated_at=NOW,
        )])
        keys.append(key)
    mark_delivered(conn, keys, NOW)
    conn.close()

    ctx = _Ctx(clusters=[new_cluster], events=[new_event])
    ctx.db = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    ctx.embedder = _DictEmbedder(vectors)
    ctx.config = _ctx_config()
    try:
        ValidateStage(credibility, _Log()).run(ctx)
    finally:
        ctx.db.close()
    return ctx.events, ctx


def _single_prior(tmp_path, credibility, new_cluster, new_event, *,
                   prior_independent, prior_claim_status):
    """The common case: one matched prior, identical vector (sim 1.0)."""
    return _run(
        tmp_path, credibility, new_cluster, new_event,
        priors=[("o" * 16, "old summary", prior_independent, prior_claim_status)],
        vectors={"old summary": [1.0, 0.0, 0.0], new_event.summary: [1.0, 0.0, 0.0]},
    )


def test_bypass_via_independent_count_growth_when_floor_also_met(tmp_path):
    # Fix A: BOTH conditions hold -- score clears the floor (military,
    # fresh, 3 groups -> 6+6+2+3+0.2=17.2 >= 11) AND independent_count
    # grows 2 -> 3 while claim_status stays "likely" both times (isolates
    # count-growth, not a claim upgrade).
    credibility = _cred(
        a=SourceCredibility(tier=2, group="ga"),
        b=SourceCredibility(tier=2, group="gb"),
        c=SourceCredibility(tier=2, group="gc"),
    )
    cluster = _cluster([
        _item("a", "https://x/1"), _item("b", "https://x/2"), _item("c", "https://x/3"),
    ])
    event = Event(event_key=cluster.key, summary="developing story",
                  category="military", source_count=3,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _single_prior(tmp_path, credibility, cluster, event,
                              prior_independent=2, prior_claim_status="likely")
    assert len(kept) == 1
    assert kept[0].follow_up is True
    # Round-4 review, fix 1: HIGH band must set follow_up_high so
    # render.py renders the compact one-liner, not a full entry.
    assert kept[0].follow_up_high is True
    assert kept[0].independent_count == 3
    assert kept[0].claim_status == "likely"  # unchanged -- isolates count growth
    # Fix 5, round-2 review: a bypass carries a reason exactly like a drop
    # does -- HIGH band (sim 1.0 here), kept=development.
    reason = ctx.repeat_drop_reasons[cluster.key]
    assert reason.startswith("band=high sim=1.00")
    assert "kept=development" in reason


def test_bypass_via_claim_upgrade_when_floor_also_met(tmp_path):
    # Fix A: BOTH conditions hold -- score clears the floor (military,
    # fresh, 1 group -> 6+2+2+3+0=13 >= 11) AND claim_status upgrades
    # rumour -> unconfirmed while independent_count stays 1 == 1 (isolates
    # the claim upgrade, not a count increase).
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("t2", "https://x/1")])
    event = Event(event_key=cluster.key, summary="developing story",
                  category="military", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _single_prior(tmp_path, credibility, cluster, event,
                              prior_independent=1, prior_claim_status="rumour")
    assert len(kept) == 1
    assert kept[0].follow_up is True
    assert kept[0].follow_up_high is True  # round-4 review, fix 1
    assert kept[0].independent_count == 1  # unchanged -- isolates the upgrade
    assert kept[0].claim_status == "unconfirmed"
    reason = ctx.repeat_drop_reasons[cluster.key]
    assert reason.startswith("band=high sim=1.00")
    assert "kept=development" in reason


def test_floor_met_without_development_is_dropped(tmp_path):
    # Fix A's core correction: the OLD disjunction bypassed on the score
    # floor ALONE. Same score profile as the claim-upgrade test above
    # (military, fresh, 1 group -> 13 >= 11) but independent_count AND
    # claim_status are IDENTICAL to the prior -- no development, so this
    # must now be DROPPED, not shipped as a follow-up.
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("t2", "https://x/1")])
    event = Event(event_key=cluster.key, summary="no development",
                  category="military", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _single_prior(tmp_path, credibility, cluster, event,
                              prior_independent=1, prior_claim_status="unconfirmed")
    assert kept == []
    assert "blocked=no_development" in ctx.repeat_drop_reasons[cluster.key]


def test_mid_band_bypasses_on_score_floor_alone_no_development_required(tmp_path):
    # Round-2 review fix 1: MID band (event_match_threshold <= sim <
    # event_repeat_threshold, i.e. 0.55 <= sim < 0.80 here) is a related
    # but DIFFERENT story -- no development test. independent_count and
    # claim_status are IDENTICAL to the prior (would be a hard drop under
    # the HIGH-band conjunction, see test_floor_met_without_development_is_
    # dropped above) yet this must still survive as a follow-up, because
    # similarity never clears event_repeat_threshold so there is nothing
    # valid to ratchet against.
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("t2", "https://x/1")])
    event = Event(event_key=cluster.key, summary="related different story",
                  category="military", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _run(
        tmp_path, credibility, cluster, event,
        priors=[("o" * 16, "old summary", 1, "unconfirmed")],
        vectors={
            "old summary": [1.0, 0.0, 0.0],
            "related different story": [0.65, 0.76, 0.0],
        },
    )
    assert len(kept) == 1
    assert kept[0].follow_up is True
    # Round-4 review, fix 1: MID band must NOT set follow_up_high -- it
    # renders as a full entry (a different story), not the compact line.
    assert kept[0].follow_up_high is False
    assert kept[0].independent_count == 1  # unchanged -- MID band needs no development
    assert kept[0].claim_status == "unconfirmed"
    reason = ctx.repeat_drop_reasons[cluster.key]
    assert reason.startswith("band=mid sim=0.65")
    assert "kept=above_floor" in reason


def test_mid_band_drops_below_score_floor(tmp_path):
    # Round-2 review fix 1: MID band still requires score >=
    # repeat_bypass_score to survive -- below it, drop, same as every other
    # gate. category="other" keeps the score under the floor (0+2+2+3+0=7
    # < 11, same profile as test_development_without_floor_is_dropped).
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("t2", "https://x/1")])
    event = Event(event_key=cluster.key, summary="related but unimportant",
                  category="other", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _run(
        tmp_path, credibility, cluster, event,
        priors=[("o" * 16, "old summary", 1, "unconfirmed")],
        vectors={
            "old summary": [1.0, 0.0, 0.0],
            "related but unimportant": [0.65, 0.76, 0.0],
        },
    )
    assert kept == []
    reason = ctx.repeat_drop_reasons[cluster.key]
    assert reason.startswith("band=mid sim=0.65")
    assert "blocked=below_floor" in reason


def test_development_without_floor_is_dropped(tmp_path):
    # The reviewer's flagged uncovered case: prior is tier-3 shape
    # (independent_count=0, claim_status="rumour"), the new event is
    # tier-2 shape (independent_count=1, "unconfirmed") -- real
    # development on both axes -- but the score stays below
    # repeat_bypass_score (category="other", fresh, 1 group ->
    # 0+2+2+3+0=7 < 11). Under the OLD disjunction this bypassed on
    # condition (b)/(c) alone; it must now be DROPPED.
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("t2", "https://x/1")])
    event = Event(event_key=cluster.key, summary="developing but unimportant",
                  category="other", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _single_prior(tmp_path, credibility, cluster, event,
                              prior_independent=0, prior_claim_status="rumour")
    assert kept == []
    assert "blocked=below_floor" in ctx.repeat_drop_reasons[cluster.key]


def test_matched_repeat_with_no_development_and_below_floor_is_dropped(tmp_path):
    # Baseline: neither condition holds (score below floor, no
    # development) -- dropped under both the old and new logic. Reason
    # must record BOTH terms failed, not just one.
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("t2", "https://x/1")])
    event = Event(event_key=cluster.key, summary="no development",
                  category="other", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _single_prior(tmp_path, credibility, cluster, event,
                              prior_independent=1, prior_claim_status="unconfirmed")
    assert kept == []
    assert "blocked=both" in ctx.repeat_drop_reasons[cluster.key]


def test_high_water_mark_spans_every_matched_prior_not_just_the_closest(tmp_path):
    # Fix B: TWO priors match above event_match_threshold (0.55) -- a
    # WEAK one (independent_count=0, "rumour") that is COSINE-CLOSER to
    # the new event (sim 0.9), and a STRONG one (independent_count=2,
    # "likely") that is farther but still matched (sim 0.6). The new
    # event (independent_count=1, "unconfirmed", floor met: military,
    # fresh, 1 group -> 13 >= 11) developed over the WEAK prior alone
    # (1>0, unconfirmed>rumour) -- an argmax-similarity-only comparison
    # (the pre-fix bug) would pick the closer, weaker prior and wrongly
    # bypass. The high-water mark across BOTH priors is
    # (independent_count=2, claim_rank=likely=2), which the new event
    # does NOT exceed on either axis -- correctly dropped.
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("t2", "https://x/1")])
    event = Event(event_key=cluster.key, summary="new event",
                  category="military", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _run(
        tmp_path, credibility, cluster, event,
        priors=[
            ("w" * 16, "old weak", 0, "rumour"),
            ("s" * 16, "old strong", 2, "likely"),
        ],
        vectors={
            "new event": [1.0, 0.0, 0.0],
            "old weak": [0.9, 0.4358898943540674, 0.0],   # cos = 0.9 (closer)
            "old strong": [0.6, 0.8, 0.0],                # cos = 0.6 (farther, still matched)
        },
    )
    assert kept == []
    assert "blocked=no_development" in ctx.repeat_drop_reasons[cluster.key]


def test_reason_records_the_best_matching_priors_event_key(tmp_path):
    # Round-3 review, fix 2: band=/sim=/score= told the owner a repeat
    # fired but never WHICH prior it matched, so the 0.80 tuning loop
    # could not pull up the actual pair. Two priors match (weak, closer
    # at sim 0.9; strong, farther at sim 0.6) -- best_sim is the WEAK
    # prior's, so the reason must name the weak prior's key, not the
    # strong one's, proving the tag tracks the argmax, not just the last
    # prior seen in the loop.
    credibility = _cred(t2=SourceCredibility(tier=2, group="g"))
    cluster = _cluster([_item("t2", "https://x/1")])
    event = Event(event_key=cluster.key, summary="new event",
                  category="military", source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW)
    kept, ctx = _run(
        tmp_path, credibility, cluster, event,
        priors=[
            ("w" * 16, "old weak", 0, "rumour"),
            ("s" * 16, "old strong", 2, "likely"),
        ],
        vectors={
            "new event": [1.0, 0.0, 0.0],
            "old weak": [0.9, 0.4358898943540674, 0.0],   # cos = 0.9 (closer)
            "old strong": [0.6, 0.8, 0.0],                # cos = 0.6 (farther)
        },
    )
    assert kept == []
    reason = ctx.repeat_drop_reasons[cluster.key]
    assert "prior=wwwwwwww" in reason
    assert "prior=ssssssss" not in reason
