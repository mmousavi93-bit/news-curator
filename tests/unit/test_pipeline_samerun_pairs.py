"""Unit tests for session-9s pair logging in pipeline/samerun_dedup.py:
every pairwise comparison at or above pipeline.samerun_pair_log_floor is
recorded with its text, while the DROP DECISION stays byte-identical to
9r. Self-contained helpers (the validate-stage suite has its own); the
settings come from the real fixture so the new schema key is exercised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.pipeline.samerun_dedup import drop_same_run_dups
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


class _VecEmbedder:
    """Maps a dict of text -> vector; identity otherwise."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors.get(t, [1.0, 0.0]) for t in texts]


def _settings(**overrides) -> Settings:
    raw = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        raw["pipeline"][key] = value
    return Settings.from_dict(raw)


def _config() -> Config:
    return Config(settings=_settings(), credibility={})


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    events: list = field(default_factory=list)
    embedder: object = None
    config: Config = field(default_factory=_config)
    now: datetime = NOW


def _cluster(n: int, base: str) -> Cluster:
    """A cluster of n members; its KEY is computed from the urls, exactly
    as in production -- tests must use cluster.key for their events."""
    cluster = Cluster(key="")
    for i in range(n):
        cluster.add(Item(source_id="s", url=f"https://x/{base}/{i}",
                         title=f"t{base}{i}", body="b", published_at=NOW,
                         lang="en", raw_hash="h" * 8), [1.0])
    return cluster


def _event(key: str, summary: str, headline: str, independent: int) -> Event:
    return Event(event_key=key, summary=summary, headline=headline,
                 independent_count=independent, first_seen_at=NOW,
                 last_updated_at=NOW)


def _run(vectors, clusters, events, **kwargs):
    ctx = _Ctx(clusters=clusters, events=events, embedder=_VecEmbedder(vectors))
    return drop_same_run_dups(ctx, events, _Log(), **kwargs)


# ---------------------------------------------------------------------------
# invariance (brief requirement 4: logging observes, never decides)
# ---------------------------------------------------------------------------


def test_surviving_set_invariant_to_log_floor():
    ca, cb, cc = _cluster(3, "ca"), _cluster(1, "cb"), _cluster(1, "cc")
    clusters = [ca, cb, cc]
    events = [
        _event(ca.key, "strike on the tanker", "h-a", 2),
        _event(cb.key, "strike on the tanker", "h-b", 0),
        _event(cc.key, "retaliation for the strike", "h-c", 0),
    ]
    vectors = {
        "strike on the tanker": [1.0, 0.0, 0.0],
        "retaliation for the strike": [0.3, 0.9539, 0.0],  # sim 0.30 < 0.40 floor
    }
    kept_0, dropped_0, reasons_0, pairs_0 = _run(vectors, clusters, events, log_floor=0.0)
    kept_4, dropped_4, reasons_4, pairs_4 = _run(vectors, clusters, events, log_floor=0.40)
    # The drop decision is byte-identical across floors...
    assert [e.event_key for e in kept_0] == [e.event_key for e in kept_4]
    assert [e.event_key for e in dropped_0] == [e.event_key for e in dropped_4]
    assert reasons_0 == reasons_4
    # ...only the ROW COUNT changes (0.30-pair below the 0.40 floor).
    assert len(pairs_0) == 2 and len(pairs_4) == 1


def test_dropped_pair_recorded_with_decision_and_text():
    ca, cb = _cluster(3, "da"), _cluster(1, "db")
    clusters = [ca, cb]
    events = [
        _event(ca.key, "identical story text", "خبر اول", 2),
        _event(cb.key, "identical story text", "خبر دوم", 0),
    ]
    vectors = {"identical story text": [1.0, 0.0, 0.0]}
    kept, dropped, reasons, pairs = _run(vectors, clusters, events)
    assert len(kept) == 1 and kept[0].event_key == ca.key  # 9r survivor rule
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.similarity == 1.0
    assert pair.decision == "dropped_b"  # the second, weaker event lost
    assert pair.key_a == ca.key and pair.key_b == cb.key
    assert pair.n_members_a == 3 and pair.n_members_b == 1
    assert pair.independent_count_a == 2 and pair.independent_count_b == 0
    assert pair.headline_a == "خبر اول" and pair.headline_b == "خبر دوم"
    assert pair.run_at_utc == NOW.isoformat()


def test_below_threshold_pair_recorded_kept_below_threshold():
    ca, cb = _cluster(2, "fa"), _cluster(2, "fb")
    clusters = [ca, cb]
    events = [
        _event(ca.key, "strike on the island", "h-a", 1),
        _event(cb.key, "retaliation for the strike", "h-b", 1),
    ]
    vectors = {
        "strike on the island": [1.0, 0.0, 0.0],
        "retaliation for the strike": [0.5, 0.866, 0.0],  # sim 0.50 < 0.80
    }
    kept, dropped, reasons, pairs = _run(vectors, clusters, events)
    assert len(kept) == 2 and dropped == []  # MID band: both survive (9r)
    assert len(pairs) == 1
    assert pairs[0].decision == "kept_below_threshold"
    assert pairs[0].similarity == 0.5


def test_pairs_empty_without_embedder():
    cluster = _cluster(1, "ga")
    ctx = _Ctx(clusters=[cluster],
               events=[_event(cluster.key, "s", "h", 0)])
    kept, dropped, reasons, pairs = drop_same_run_dups(ctx, ctx.events, _Log())
    assert (kept, dropped, reasons, pairs) == (ctx.events, [], {}, [])


def test_log_floor_read_from_settings_when_not_passed():
    ca, cb = _cluster(1, "ha"), _cluster(1, "hb")
    clusters = [ca, cb]
    events = [
        _event(ca.key, "text one", "h-a", 0),
        _event(cb.key, "text two", "h-b", 0),
    ]
    vectors = {
        "text one": [1.0, 0.0, 0.0],
        "text two": [0.6, 0.8, 0.0],  # sim 0.60: above default floor 0.40
    }
    kept, _, _, pairs = _run(vectors, clusters, events)  # no log_floor kwarg
    assert len(pairs) == 1  # recorded via pipeline.samerun_pair_log_floor
