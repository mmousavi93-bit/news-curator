"""Session-22 unit tests for the NOVELTY-AWARE same-run merge in
pipeline/samerun_dedup.py: novelty used to be observed only (logged into
pairs_<ts>.csv) and never acted on, so a FRAGMENT pair -- neither side
bringing a new number/entity -- was kept until cosine reached the 0.80 HIGH
band. It now merges from FRAGMENT_FLOOR (0.70); a DEVELOPMENT pair (a new
fact on either side) still needs 0.80.

SYNTHETIC vectors only: sentence-transformers/MiniLM is CI-only and not
installed locally, so the embedder is a stub returning pre-seeded unit
vectors whose dot product (== the pass's _cosine) is the intended cosine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.pipeline.samerun_dedup import FRAGMENT_FLOOR, drop_same_run_dups
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
_WEAK = "strike on the tanker"                 # no digits, no entities
_STRONG = "tanker hit in the strait"           # no digits, no entities
_DEVELOPED = f"{_STRONG}, 5 killed"            # a new NUMBER -- development


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    warning = info = error


class _VecEmbedder:
    """Returns the seeded vectors in call order -- one per event, aligned
    with the event list the pass embeds (no model, no text hashing)."""

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = list(vectors)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._vectors[: len(texts)]


def _settings(**overrides) -> Settings:
    raw = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        raw["pipeline"][key] = value
    return Settings.from_dict(raw)


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    embedder: object = None
    config: Config = field(
        default_factory=lambda: Config(settings=_settings(), credibility={}))
    now: datetime = NOW


def _unit(cos: float) -> list[float]:
    """A unit vector whose cosine with [1, 0, 0] is exactly `cos`."""
    return [cos, math.sqrt(1.0 - cos * cos), 0.0]


def _cluster(n: int, base: str) -> Cluster:
    cluster = Cluster(key="")
    for i in range(n):
        cluster.add(Item(source_id="s", url=f"https://x/{base}/{i}",
                         title=f"t{base}{i}", body="b", published_at=NOW,
                         lang="en", raw_hash="h" * 8), [1.0])
    return cluster


def _event(key: str, summary: str, independent: int,
           entities: tuple[str, ...] = ()) -> Event:
    return Event(event_key=key, summary=summary, headline=f"h-{key[:4]}",
                 independent_count=independent, first_seen_at=NOW,
                 last_updated_at=NOW, entities=entities)


def _run(summaries, similarity, *, independent=(0, 2), sizes=(1, 3),
         entities=((), ())):
    """Event A (weaker side: fewer independent sources AND fewer members)
    vs event B at `similarity` cosine. Returns (keys, kept, dropped,
    reasons, pairs) with the events keyed by their real cluster keys."""
    clusters = [_cluster(sizes[0], "a"), _cluster(sizes[1], "b")]
    keys = [c.key for c in clusters]
    events = [
        _event(keys[0], summaries[0], independent[0], entities[0]),
        _event(keys[1], summaries[1], independent[1], entities[1]),
    ]
    ctx = _Ctx(clusters=clusters,
               embedder=_VecEmbedder([_unit(1.0), _unit(similarity)]))
    return keys, *drop_same_run_dups(ctx, events, _Log(), log_floor=0.0)


# ---------------------------------------------------------------------------
# (a) fragment pair at 0.76 merges -- the session-22 fix
# ---------------------------------------------------------------------------


def test_fragment_pair_at_076_drops_the_weaker_side():
    keys, kept, dropped, reasons, _ = _run((_WEAK, _STRONG), 0.76)
    assert [e.event_key for e in kept] == [keys[1]]      # survivor ranking intact
    assert [e.event_key for e in dropped] == [keys[0]]
    assert reasons[keys[0]] == "same_run_dup sim=0.76"


def test_fragment_merge_still_logs_the_pair_row():
    keys, _kept, _dropped, _reasons, pairs = _run((_WEAK, _STRONG), 0.76)
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.decision == "dropped_a" and pair.novelty == "fragment"
    assert pair.similarity == 0.76
    assert (pair.key_a, pair.key_b) == (keys[0], keys[1])
    assert (pair.n_members_a, pair.n_members_b) == (1, 3)


def test_fragment_floor_is_inclusive_at_070():
    _keys, kept, dropped, _reasons, _pairs = _run((_WEAK, _STRONG), FRAGMENT_FLOOR)
    assert len(kept) == 1 and len(dropped) == 1


# ---------------------------------------------------------------------------
# (b) development pair at 0.76 keeps BOTH (a new fact is a real development)
# ---------------------------------------------------------------------------


def test_development_pair_at_076_keeps_both_new_number_right():
    _keys, kept, dropped, reasons, pairs = _run((_WEAK, _DEVELOPED), 0.76)
    assert len(kept) == 2 and dropped == [] and reasons == {}
    assert pairs[0].decision == "kept_below_threshold"
    assert pairs[0].novelty == "development"


def test_development_pair_at_076_keeps_both_new_number_left():
    left = f"{_WEAK}, 5 killed"
    _keys, kept, dropped, _reasons, pairs = _run((left, _STRONG), 0.76)
    assert len(kept) == 2 and dropped == []
    assert pairs[0].novelty == "development"


def test_development_pair_at_076_keeps_both_new_entity():
    _keys, kept, dropped, _reasons, pairs = _run(
        ("port strike", "port strike"), 0.76,
        entities=(("Hodeidah",), ("Hodeidah", "CENTCOM")))
    assert len(kept) == 2 and dropped == []
    assert pairs[0].novelty == "development"


def test_development_pair_just_below_high_band_keeps_both():
    _keys, kept, dropped, _reasons, _pairs = _run((_WEAK, _DEVELOPED), 0.79)
    assert len(kept) == 2 and dropped == []


# ---------------------------------------------------------------------------
# (c) below FRAGMENT_FLOOR keeps both, whatever the novelty
# ---------------------------------------------------------------------------


def test_below_fragment_floor_keeps_both_fragment():
    _keys, kept, dropped, reasons, pairs = _run((_WEAK, _STRONG), 0.55)
    assert len(kept) == 2 and dropped == [] and reasons == {}
    assert pairs[0].decision == "kept_below_threshold"
    assert pairs[0].novelty == "fragment"


def test_below_fragment_floor_keeps_both_development():
    _keys, kept, dropped, _reasons, _pairs = _run((_WEAK, _DEVELOPED), 0.59)
    assert len(kept) == 2 and dropped == []


# ---------------------------------------------------------------------------
# (d) the 0.80 HIGH band still drops regardless of novelty
# ---------------------------------------------------------------------------


def test_high_band_drops_fragment_pair():
    _keys, kept, dropped, _reasons, pairs = _run((_WEAK, _STRONG), 0.85)
    assert len(kept) == 1 and len(dropped) == 1
    assert pairs[0].novelty == "fragment"


def test_high_band_drops_development_pair():
    keys, kept, dropped, _reasons, pairs = _run((_WEAK, _DEVELOPED), 0.85)
    assert [e.event_key for e in kept] == [keys[1]]
    assert [e.event_key for e in dropped] == [keys[0]]
    assert pairs[0].novelty == "development"


# ---------------------------------------------------------------------------
# preserved 9r behaviour the change sits next to
# ---------------------------------------------------------------------------


def test_constant_is_a_module_floor_below_the_high_band():
    # Above the different-story ceiling (synthetic B/C fixture 0.6724 from
    # test_pipeline_validate.py:487), at or below the lowest same-story
    # fragment (Trump-Xi 0.7226, brief run 35866102212), and below the 0.80
    # HIGH band (event_repeat_threshold) it must not merge into.
    assert 0.6724 < FRAGMENT_FLOOR <= 0.7226
    assert FRAGMENT_FLOOR < _settings().digest_rank.event_repeat_threshold


def test_non_transitive_deletion_break_survives():
    """9r fix 2: a dropped `event` must not go on deleting later events.
    sim(A,B) = sim(A,C) = 0.85 (A loses to B and, on the (0, 1)-vs-(0, 1)
    tie-break, would have deleted C); sim(B,C) = 0.445, so C must survive."""
    clusters = [_cluster(1, "ta"), _cluster(3, "tb"), _cluster(1, "tc")]
    keys = [c.key for c in clusters]
    events = [_event(keys[0], "text a", 0), _event(keys[1], "text b", 3),
              _event(keys[2], "text c", 0)]
    vectors = [[1.0, 0.0, 0.0], _unit(0.85), [0.85, -_unit(0.85)[1], 0.0]]
    ctx = _Ctx(clusters=clusters, embedder=_VecEmbedder(vectors))
    kept, dropped, _reasons, _pairs = drop_same_run_dups(
        ctx, events, _Log(), log_floor=0.0)
    assert [e.event_key for e in kept] == [keys[1], keys[2]]
    assert [e.event_key for e in dropped] == [keys[0]]
