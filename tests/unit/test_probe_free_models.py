"""Unit tests for tools/probe_free_models.py -- the deterministic quality
checks only (network untested by design, same contract as check_feeds.py).
Loaded via importlib because tools/ has no package and must not gain one."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "probe_free_models", _REPO_ROOT / "tools" / "probe_free_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pfm = _load()


def _payload(**overrides) -> dict:
    payload = {
        "headline": "حمله به یک کشتی در تنگه هرمز",
        "summary": "جزئیات تکمیلی درباره حادثه.",
        "entities": ["ایران"],
        "category": "military",
        "clickbait": False,
        "irrelevant": False,
    }
    payload.update(overrides)
    return payload


def test_check_response_passes_clean_payload():
    ok, reason = pfm.check_response(_payload(), "military")
    assert ok is True
    assert reason == ""


def test_check_response_missing_field_fails():
    payload = _payload()
    del payload["summary"]
    ok, reason = pfm.check_response(payload, "military")
    assert ok is False
    assert "missing field" in reason


def test_check_response_bad_category_fails():
    ok, reason = pfm.check_response(_payload(category="sports"), "military")
    assert ok is False
    assert "bad category" in reason


def test_check_response_arabic_output_fails():
    ok, reason = pfm.check_response(_payload(headline="هجوم علي سفينة في مضيق هرمز"), "military")
    assert ok is False
    assert "non-Persian" in reason


def test_check_response_refusal_marker_fails():
    ok, reason = pfm.check_response(
        _payload(summary="I cannot answer this question."), "military")
    assert ok is False
    assert "refusal marker" in reason


def test_check_response_wrong_category_for_sample_fails():
    # Softening proxy: the model reports a military event as "other".
    ok, reason = pfm.check_response(_payload(category="other"), "military")
    assert ok is False
    assert "expected" in reason


def test_check_response_empty_headline_fails():
    ok, _ = pfm.check_response(_payload(headline=""), "military")
    assert ok is False


def test_default_models_list_has_no_duplicates():
    assert len(pfm.DEFAULT_MODELS) == len(set(pfm.DEFAULT_MODELS))
    assert "thinkingmachines/inkling-small:free" in pfm.DEFAULT_MODELS
