"""Tests for pipeline/prerelevance.py: the deterministic, zero-LLM
pre-screen that REORDERS clusters by cosine to on-mission anchors.

Synthetic vectors only. sentence-transformers/torch is the [embeddings]
extra and is NOT installed here (and must not become a test dependency), so
every case builds its own 2-3 dim vectors -- which is also the only way to
test the ordering property directly instead of inferring it from a model's
idea of "Iran".
"""

from __future__ import annotations

import math

import pytest

from agent.pipeline.prerelevance import (
    DEFAULT_ANCHOR_TEXTS,
    cosine,
    reorder_for_relevance,
    relevance_score,
    relevance_scores,
)

# Axis-aligned anchors: WAR and SEC are two distinct missions, so a 3-dim
# [war, security, neither] space is enough to express on/off-mission.
WAR = [1.0, 0.0, 0.0]
SEC = [0.0, 1.0, 0.0]
ANCHORS = [WAR, SEC]


class C:
    """Stand-in for pipeline.cluster.Cluster: exposes `.centroid` plus a key
    for asserting identity through the reorder."""

    def __init__(self, key: str, centroid, text: str = "") -> None:
        self.key = key
        self.centroid = centroid
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return f"C({self.key})"


def _keys(items) -> list[str]:
    return [getattr(item, "key", item) for item in items]


ON_MISSION = lambda key: C(key, [0.95, 0.05, 0.0])  # noqa: E731
OFF_MISSION = lambda key: C(key, [0.0, 0.0, 1.0])  # noqa: E731


def test_output_is_a_permutation_of_the_input():
    items = [OFF_MISSION("off"), ON_MISSION("on"), C("mid", [0.0, 0.4, 0.6])]
    out = reorder_for_relevance(items, ANCHORS)
    assert len(out) == len(items)
    assert sorted(_keys(out)) == sorted(_keys(items))
    # Same OBJECTS, not equal copies: a reorder may not rebuild clusters.
    assert {id(item) for item in out} == {id(item) for item in items}


def test_input_list_is_not_mutated():
    items = [OFF_MISSION("off"), ON_MISSION("on")]
    reorder_for_relevance(items, ANCHORS)
    assert _keys(items) == ["off", "on"]


def test_on_mission_ranks_above_off_mission_in_either_input_order():
    on, off = ON_MISSION("on"), OFF_MISSION("off")
    assert _keys(reorder_for_relevance([off, on], ANCHORS)) == ["on", "off"]
    assert _keys(reorder_for_relevance([on, off], ANCHORS)) == ["on", "off"]


def test_mission_ranking_is_by_max_not_mean_similarity():
    # Near-total match to ONE anchor (war) must beat a vector that is
    # lukewarm about both -- max() is the intended aggregation.
    war_only = C("war_only", [0.99, 0.01, 0.0])
    both_ish = C("both_ish", [0.5, 0.5, 0.0])
    out = reorder_for_relevance([both_ish, war_only], ANCHORS)
    assert _keys(out) == ["war_only", "both_ish"]


def test_empty_input_returns_empty():
    assert reorder_for_relevance([], ANCHORS) == []
    assert reorder_for_relevance((), ANCHORS) == []


def test_single_item_returns_that_item():
    only = OFF_MISSION("solo")
    out = reorder_for_relevance([only], ANCHORS)
    assert out == [only]
    assert out[0] is only


def test_deterministic_and_equal_scores_keep_input_order():
    # Three identical centroids (identical scores) + one stronger item: the
    # tied trio must stay in input order, and repeated calls must match.
    tied = [ON_MISSION("a"), ON_MISSION("b"), ON_MISSION("c")]
    strongest = C("best", [1.0, 0.0, 0.0])
    items = [tied[0], OFF_MISSION("off"), tied[1], strongest, tied[2]]
    first = _keys(reorder_for_relevance(items, ANCHORS))
    assert first == ["best", "a", "b", "c", "off"]
    assert _keys(reorder_for_relevance(items, ANCHORS)) == first


def test_no_usable_anchors_is_the_identity_reorder():
    items = [OFF_MISSION("off"), ON_MISSION("on")]
    assert _keys(reorder_for_relevance(items, [])) == ["off", "on"]
    assert _keys(reorder_for_relevance(items, [None, "not-a-vector", []])) == ["off", "on"]


def test_unscorable_items_sink_last_and_keep_input_order():
    items = [C("no_vec", None), OFF_MISSION("off"), C("empty", []), ON_MISSION("on")]
    out = reorder_for_relevance(items, ANCHORS)
    assert _keys(out) == ["on", "off", "no_vec", "empty"]
    assert len(out) == len(items)


def test_cosine_uses_norms_not_the_raw_dot_product():
    # The normalisation trap: a tiny on-axis vector (dot 0.05) must beat a
    # large off-axis one (dot 10.0). A dot-product implementation inverts it.
    on_axis = C("on_axis", [0.05, 0.0, 0.0])
    off_axis = C("off_axis", [10.0, 10.0, 0.0])
    assert cosine(on_axis.centroid, WAR) == pytest.approx(1.0)
    assert cosine(off_axis.centroid, WAR) == pytest.approx(0.7071067, abs=1e-6)
    assert _keys(reorder_for_relevance([off_axis, on_axis], [WAR])) == ["on_axis", "off_axis"]


def test_cosine_degenerate_inputs_are_neutral_never_crash():
    assert cosine([0.0, 0.0], WAR) == 0.0          # zero vector
    assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0  # dimension mismatch
    assert cosine([float("nan"), 0.0], [1.0, 0.0]) == 0.0
    assert cosine("iran war", WAR) == 0.0          # not a vector at all
    assert cosine([3.0, 0.0], [3.0, 0.0]) == 1.0   # un-normalised, same dir
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_pair_and_bare_vector_items_are_accepted():
    # (text, vector) pairs -- the "cluster text + centroid vector" shape --
    # and a bare centroid sequence must both be scorable.
    pairs = [("off story", OFF_MISSION("x").centroid), ("on story", [0.9, 0.1, 0.0])]
    assert [text for text, _ in reorder_for_relevance(pairs, ANCHORS)] == ["on story", "off story"]
    assert relevance_score([0.9, 0.1, 0.0], ANCHORS) > relevance_score([0.0, 0.0, 1.0], ANCHORS)


def test_similarity_hook_overrides_the_default_metric():
    items = [ON_MISSION("on"), OFF_MISSION("off")]
    flipped = reorder_for_relevance(items, ANCHORS, similarity=lambda a, b: -cosine(a, b))
    assert _keys(flipped) == ["off", "on"]


def test_relevance_scores_reports_input_order_and_none_for_unscorable():
    items = [OFF_MISSION("off"), C("no_vec", None), ON_MISSION("on")]
    pairs = relevance_scores(items, ANCHORS)
    assert [getattr(item, "key") for item, _ in pairs] == ["off", "no_vec", "on"]
    assert pairs[0][1] == pytest.approx(0.0, abs=1e-9)
    assert pairs[1][1] is None
    assert pairs[2][1] > 0.9


def test_cosine_is_symmetric_on_partially_aligned_vectors():
    a, b = [1.0, 2.0, 0.0], [2.0, 1.0, 1.0]
    assert cosine(a, b) == pytest.approx(cosine(b, a))
    assert cosine(a, a) == pytest.approx(1.0)
    assert math.isclose(cosine(a, b), 4.0 / (math.sqrt(5.0) * math.sqrt(6.0)))


def test_default_anchor_texts_cover_the_five_missions():
    # Ships the wiring layer's anchor set; unused by the scoring path.
    assert len(DEFAULT_ANCHOR_TEXTS) == 5
    joined = " ".join(DEFAULT_ANCHOR_TEXTS).lower()
    for term in ("war", "security", "iran", "israel", "middle east"):
        assert term in joined
