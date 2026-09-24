"""Unit tests for prellm_drop.py: the gated pre-LLM drop + calibration probe.

Deterministic, no LLM, no network, no torch. Scoring runs against synthetic
vectors and a FakeEmbedder; the keyword guard is exercised against a real
Config built from the shared minimal fixture.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.pipeline.cluster import Cluster
from agent.pipeline.prellm_drop import (
    DEFAULT_ANCHOR_TEXTS,
    anchor_vectors,
    drop_off_mission,
    prellm_pass,
    score_histogram,
)
from agent.pipeline.relevance import validate_relevance
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

# One anchor: the on-mission direction. Everything is L2-normalised in
# production, so [1, 0] and [0, 1] are orthogonal here.
_ANCHOR = [1.0, 0.0]


class _FakeEmbedder:
    def embed(self, texts):
        return [_ANCHOR[:] for _ in texts]


def _config(enabled: bool, threshold: float | None) -> Config:
    settings = Settings.from_dict(yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")))
    pipeline = dataclasses.replace(
        settings.pipeline, prellm_drop_enabled=enabled, prellm_drop_threshold=threshold
    )
    settings = dataclasses.replace(settings, pipeline=pipeline)
    return Config(
        settings=settings,
        credibility={
            "t1": SourceCredibility(tier=1, group="g1"),
            "t2": SourceCredibility(tier=2, group="g2"),
            "t3": SourceCredibility(tier=3, group="g3"),
        },
        relevance=validate_relevance({
            "weights": {"iran_direct": 8},
            "keywords": {"iran_direct": ["ایران"]},
        }),
    )


def _cluster(source_id: str, url: str, title: str, vector: list[float]) -> Cluster:
    cluster = Cluster(key="")
    cluster.add(
        Item(source_id=source_id, url=url, title=title, body="", published_at=NOW,
             lang="fa", raw_hash="a" * 8),
        vector,
    )
    return cluster


# --- drop_off_mission (the pure gate) -------------------------------------

def test_drop_off_mission_identity_when_threshold_none():
    kept, dropped = drop_off_mission([1, 2, 3], [_ANCHOR], threshold=None)
    assert kept == [1, 2, 3]
    assert dropped == []


def test_drop_off_mission_splits_on_threshold():
    clusters = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]
    kept, dropped = drop_off_mission(clusters, [_ANCHOR], threshold=0.5)
    assert kept == [[1.0, 0.0], [1.0, 0.0]]
    assert dropped == [[0.0, 1.0]]


def test_drop_off_mission_keeps_unscorable():
    # A string has no vector and must never be dropped, whatever the threshold;
    # the scorable off-mission vector alongside it still drops.
    kept, dropped = drop_off_mission(["text", [0.0, 1.0]], [_ANCHOR], threshold=0.5)
    assert kept == ["text"]
    assert dropped == [[0.0, 1.0]]


def test_drop_off_mission_guard_never_drops_on_mission():
    a, b = [0.0], [0.0]  # both score 0.0 against [1.0]
    kept, dropped = drop_off_mission(
        [a, b], [[1.0]], threshold=0.5, on_mission=lambda c: c is b
    )
    assert kept == [b]
    assert dropped == [a]


def test_drop_off_mission_no_anchors_drops_nothing():
    # No usable anchor vectors -> every item is unscorable -> nothing dropped.
    kept, dropped = drop_off_mission([[1.0], [0.0]], [], threshold=0.5)
    assert kept == [[1.0], [0.0]]
    assert dropped == []


# --- score_histogram (the calibration probe's number-cruncher) ------------

def test_score_histogram_percentiles():
    hist = score_histogram([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    assert hist["n"] == 10
    assert hist["min"] == 0.1
    assert hist["max"] == 1.0
    assert hist["p50"] == 0.55
    assert hist["p10"] == 0.19


def test_score_histogram_ignores_none():
    hist = score_histogram([0.5, None, 0.9])
    assert hist["n"] == 2
    assert hist["min"] == 0.5
    assert hist["max"] == 0.9


def test_score_histogram_none_when_no_scores():
    assert score_histogram([None, None]) is None
    assert score_histogram([]) is None


# --- anchor_vectors -------------------------------------------------------

def test_anchor_vectors_empty_without_embedder():
    assert anchor_vectors(None) == []


def test_anchor_vectors_empty_when_embedder_raises():
    class Bad:
        def embed(self, texts):
            raise RuntimeError("no model")

    assert anchor_vectors(Bad()) == []


def test_anchor_vectors_embeds_anchors_once():
    class Counting:
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            return [[1.0] for _ in texts]

    embedder = Counting()
    anchors = anchor_vectors(embedder)
    assert embedder.calls == 1
    assert len(anchors) == len(DEFAULT_ANCHOR_TEXTS)


# --- prellm_pass (the wiring seam) ----------------------------------------

def test_prellm_pass_disabled_keeps_everything_and_records_scores():
    # Calibration mode: prellm_drop_enabled=false -> drops NOTHING, but still
    # scores every cluster so chosen.csv's prellm_score column is populated.
    on = _cluster("t1", "https://x/on", "ایران در آستانه توافق", [0.0, 1.0])
    off = _cluster("t2", "https://x/off", "unrelated", [0.0, 1.0])
    high = _cluster("t2", "https://x/high", "unrelated", [1.0, 0.0])
    clusters = [on, off, high]

    kept, dropped, scores = prellm_pass(
        clusters, _config(enabled=False, threshold=0.35), _FakeEmbedder(),
        logging.getLogger("test_prellm"),
    )
    assert kept == clusters
    assert dropped == []
    assert set(scores) == {on.key, off.key, high.key}
    assert scores[on.key] == 0.0
    assert scores[off.key] == 0.0
    assert scores[high.key] == 1.0


def test_prellm_pass_enabled_drops_off_mission_but_guard_keeps_on_mission():
    on = _cluster("t1", "https://x/on", "ایران در آستانه توافق", [0.0, 1.0])
    off = _cluster("t2", "https://x/off", "unrelated", [0.0, 1.0])
    high = _cluster("t2", "https://x/high", "unrelated", [1.0, 0.0])
    clusters = [on, off, high]

    kept, dropped, scores = prellm_pass(
        clusters, _config(enabled=True, threshold=0.5), _FakeEmbedder(),
        logging.getLogger("test_prellm"),
    )
    # `on` scores 0.0 (below threshold) but the keyword guard keeps it;
    # `high` scores 1.0 (above); only `off` is dropped.
    assert kept == [on, high]
    assert dropped == [off]
    assert scores[off.key] == 0.0


def test_prellm_pass_without_embedder_drops_nothing():
    on = _cluster("t1", "https://x/on", "ایران", [0.0, 1.0])
    kept, dropped, scores = prellm_pass(
        [on], _config(enabled=True, threshold=0.5), None,
        logging.getLogger("test_prellm"),
    )
    assert kept == [on]
    assert dropped == []
    assert scores[on.key] is None  # no anchors -> unscorable, never dropped
