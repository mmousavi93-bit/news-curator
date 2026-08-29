"""Unit tests for the startup coverage check (session-5 decision 6):
dead-weight sources, uncovered signals, markets-fetcher exemptions, lead
exemptions, and the fail-loud shapes."""

from __future__ import annotations

import logging

import pytest

from agent.collectors.base import SourceSpec
from agent.config import ConfigError, SourceCredibility
from agent.coverage import check_coverage, run_startup_check, validate_weights

WEIGHTS = {
    "signals": {
        "A1": {"base": 18, "half_life_days": 14},
        "B3": {"base": 15, "half_life_days": 3},
        "G1": {"base": 6, "half_life_days": 2},
    },
    "covered_by_markets_fetcher": ["G1"],
}


def _source(source_id: str, signals=("A1",), enabled=True) -> SourceSpec:
    return SourceSpec(id=source_id, name=source_id, url=f"https://x/{source_id}",
                      type="rss", lang="en", enabled=enabled,
                      signals_covered=tuple(signals))


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


def test_full_coverage_no_warnings():
    credibility = {"s1": SourceCredibility(tier=1, group=None),
                   "s2": SourceCredibility(tier=2, group=None)}
    warnings = check_coverage(
        [_source("s1", ("A1",)), _source("s2", ("B3",))], WEIGHTS, credibility
    )
    assert warnings == []  # G1 exempt via markets fetcher


def test_uncovered_signal_warns():
    credibility = {"s1": SourceCredibility(tier=1, group=None)}
    warnings = check_coverage([_source("s1", ("A1",))], WEIGHTS, credibility)
    assert any("B3" in w and "can never fire" in w for w in warnings)


def test_dead_weight_source_warns():
    credibility = {"s1": SourceCredibility(tier=1, group=None),
                   "s2": SourceCredibility(tier=2, group=None)}
    warnings = check_coverage(
        [_source("s1", ("A1",)), _source("s2", ())], WEIGHTS, credibility
    )
    assert any("dead weight" in w for w in warnings)


def test_lead_sources_exempt_from_dead_weight():
    credibility = {"s1": SourceCredibility(tier=1, group=None),
                   "lead1": SourceCredibility(tier="lead", group=None)}
    warnings = check_coverage(
        [_source("s1", ("A1", "B3")), _source("lead1", ())], WEIGHTS, credibility
    )
    assert warnings == []


def test_disabled_source_does_not_cover():
    credibility = {"s1": SourceCredibility(tier=1, group=None)}
    warnings = check_coverage(
        [_source("s1", ("A1",), enabled=False)], WEIGHTS, credibility
    )
    assert any("A1" in w and "can never fire" in w for w in warnings)


def test_unknown_signal_id_warns():
    credibility = {"s1": SourceCredibility(tier=1, group=None)}
    warnings = check_coverage([_source("s1", ("A1", "ZZ9"))], WEIGHTS, credibility)
    assert any("unknown signals" in w and "ZZ9" in w for w in warnings)


def test_malformed_weights_raise():
    with pytest.raises(ConfigError, match="signals"):
        validate_weights({"nope": {}})
    with pytest.raises(ConfigError, match="base"):
        validate_weights({"signals": {"A1": {"half_life_days": 14}}})


def test_run_startup_check_respects_require_flag():
    log = _Log()
    credibility = {"s1": SourceCredibility(tier=1, group=None)}
    run_startup_check([_source("s1", ())], WEIGHTS, credibility,
                      require_check=False, fail_on_warnings=False, logger=log)
    assert log.messages == []


def test_run_startup_check_fails_build_when_promoted():
    log = _Log()
    credibility = {"s1": SourceCredibility(tier=1, group=None)}
    with pytest.raises(ConfigError, match="coverage check failed"):
        run_startup_check([_source("s1", ())], WEIGHTS, credibility,
                          require_check=True, fail_on_warnings=True, logger=log)
