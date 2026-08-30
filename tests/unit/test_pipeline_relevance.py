"""Tests for pipeline/relevance.py: the deterministic Iran-relevance tier
matcher behind digest ranking (owner decision 2026-08-30 -- relevance leads,
category is a tie-breaker). No LLM, no network: config validation and
substring matching only."""

from __future__ import annotations

import pytest

from agent.config import ConfigError
from agent.pipeline.relevance import passes_gate, score_relevance, validate_relevance

_GOOD = {
    "weights": {"iran_direct": 8, "strategic": 4, "economy": 3},
    "keywords": {
        "iran_direct": ["ایران", "iran"],
        "strategic": ["جنگ", "war"],
        "economy": ["نفت", "oil"],
    },
}


def _cfg(**overrides):
    raw = {**_GOOD, **overrides}
    return validate_relevance(raw)


def test_good_config_loads():
    cfg = _cfg()
    assert cfg.weights["iran_direct"] == 8.0
    assert "جنگ" in cfg.keywords["strategic"]


def test_unknown_tier_rejected():
    with pytest.raises(ConfigError, match="unknown weight tier"):
        _cfg(weights={**_GOOD["weights"], "nope": 1})


def test_negative_weight_rejected():
    with pytest.raises(ConfigError, match="non-negative"):
        _cfg(weights={**_GOOD["weights"], "strategic": -1})


def test_non_string_keyword_rejected():
    with pytest.raises(ConfigError, match="non-empty strings"):
        _cfg(keywords={**_GOOD["keywords"], "economy": ["نفت", 3]})


def test_weight_without_keywords_rejected():
    with pytest.raises(ConfigError, match="weight but no keywords"):
        _cfg(keywords={k: v for k, v in _GOOD["keywords"].items() if k != "economy"})


def test_not_a_mapping_rejected():
    with pytest.raises(ConfigError, match="expected a mapping"):
        validate_relevance([1, 2])


def test_score_relevance_highest_tier_wins_not_additive():
    cfg = _cfg()
    # Matches both strategic ("جنگ") and economy ("نفت"): takes 4, not 7.
    assert score_relevance(cfg, "جنگ نفت در منطقه") == 4.0
    assert score_relevance(cfg, "قیمت نفت بالا رفت") == 3.0
    assert score_relevance(cfg, "no keywords here") == 0.0


def test_score_relevance_iran_direct_beats_strategic():
    cfg = _cfg()
    assert score_relevance(cfg, "حمله به ایران با موشک") == 8.0


def test_score_relevance_none_config_is_zero():
    assert score_relevance(None, "ایران") == 0.0
    assert score_relevance(_cfg(), "") == 0.0


def test_min_relevance_string_rejected():
    with pytest.raises(ConfigError, match="min_relevance"):
        validate_relevance({**_GOOD, "min_relevance": "high"})


def test_passes_gate_thresholds():
    cfg = _cfg(min_relevance=4)
    assert passes_gate(cfg, "قیمت نفت بالا رفت") is False  # economy 3 < 4
    assert passes_gate(cfg, "حمله به ایران") is True       # iran_direct 8 >= 4
    assert passes_gate(None, "anything") is True           # no config: no gate
