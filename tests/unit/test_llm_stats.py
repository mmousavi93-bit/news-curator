"""Unit tests for llm/stats.py: per-provider attempt counters rendered
into run.csv (owner request 2026-08-30)."""

from __future__ import annotations

from agent.llm.stats import ProviderStats


def test_record_counts_calls_and_failures():
    stats = ProviderStats(["gemini", "bai"])
    stats.record("gemini", ok=True)
    stats.record("gemini", ok=False)
    stats.record("bai", ok=False)
    assert stats.as_dict() == {
        "gemini": {"calls": 2, "failed": 1},
        "bai": {"calls": 1, "failed": 1},
    }


def test_as_dict_returns_a_copy():
    stats = ProviderStats(["gemini"])
    stats.record("gemini", ok=True)
    view = stats.as_dict()
    view["gemini"]["calls"] = 999
    assert stats.as_dict()["gemini"]["calls"] == 1


def test_unused_providers_report_zero():
    stats = ProviderStats(["gemini", "groq", "bai"])
    stats.record("bai", ok=True)
    assert stats.as_dict()["gemini"] == {"calls": 0, "failed": 0}
