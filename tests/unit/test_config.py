from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import ConfigError, _valid_credibility_tier, load_all, load_yaml


def test_missing_file_raises_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="settings.yaml"):
        load_yaml("settings.yaml", base=tmp_path)


def test_malformed_yaml_raises_config_error(tmp_path: Path):
    bad = tmp_path / "settings.yaml"
    bad.write_text("schedule: [this is not: closed", encoding="utf-8")
    with pytest.raises(ConfigError, match="malformed YAML"):
        load_yaml("settings.yaml", base=tmp_path)


def test_empty_document_yields_empty_dict_not_none(tmp_path: Path):
    empty = tmp_path / "settings.yaml"
    empty.write_text("# just a comment, no content\n", encoding="utf-8")
    assert load_yaml("settings.yaml", base=tmp_path) == {}


def test_duplicate_top_level_key_raises_config_error(tmp_path: Path):
    dup = tmp_path / "settings.yaml"
    dup.write_text("version: 1\nversion: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"duplicate key 'version'"):
        load_yaml("settings.yaml", base=tmp_path)


def test_duplicate_nested_key_raises_config_error(tmp_path: Path):
    dup = tmp_path / "settings.yaml"
    dup.write_text(
        "schedule:\n  timezone: \"Asia/Tehran\"\n  timezone: \"UTC\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"duplicate key 'timezone'"):
        load_yaml("settings.yaml", base=tmp_path)


@pytest.mark.parametrize(
    "tier,expected",
    [
        (1, True),
        (2, True),
        (3, True),
        ("lead", True),
        (True, False),   # bool must not pass as tier 1 via `True == 1`
        (1.0, False),    # float must not pass as tier 1 via `1.0 == 1`
        (7, False),
        ("Lead", False),
        (None, False),
    ],
)
def test_valid_credibility_tier_checks_type_identity(tier, expected):
    assert _valid_credibility_tier(tier) is expected


def test_load_all_reports_every_problem_in_one_exception(tmp_path: Path):
    # Three independent, unrelated problems:
    #   1. settings.yaml has an unknown top-level key
    #   2. settings.yaml is missing a required top-level key ("ops")
    #   3. credibility.yaml has a source with an invalid tier
    (tmp_path / "settings.yaml").write_text(
        """
version: 1
bogus_top_level_key: true
schedule: { timezone: "Asia/Tehran", pipeline_cron: "x", digest_cron: "x",
            canonical_daily_run: "07:00", lookback_hours: 6 }
collection: { max_items_per_source: 1, concurrency: 1, per_source_timeout_seconds: 1,
              respect_robots_txt: true, user_agent: "x", degraded_after_empty_runs: 1,
              date_only_max_age_hours: 72 }
pipeline: { max_clusters_per_run: 1, max_vision_calls_per_run: 1,
            cluster_similarity_threshold: 0.1, event_match_threshold: 0.1,
            vision_min_image_bytes: 1 }
retention: { url_hashes_days: 1, events_days: 1, embeddings_days: 1,
             signal_events_days: 1, speaker_statements_days: 1,
             score_history_days: 1, scheduled_events_days: 1 }
scoring: { tier_multipliers: {1: 1.0}, min_independent_sources: 2,
           tier3_can_corroborate: false, stateful_decay_from_state_end: true,
           category_caps: {}, convergence_step: 0.1, convergence_max: 1.0,
           deception_multiplier: 1.0, deception_window_hours: 1,
           novelty_quiet_days: 1, novelty_decay_factor: 0.1, novelty_floor: 0.1,
           novelty_reset_min_contribution: 1, display_cap: 100,
           persist_uncapped: true, delta_basis: "uncapped", tier_bands: {} }
alerting: { wartime_enter_tact: 1, wartime_enter_consecutive_days: 1,
            wartime_exit_tact: 1, wartime_exit_consecutive_days: 1,
            wartime_delta_trigger: 1, new_dimension_silent_days: 1,
            exit_from_wartime_is_full_alert: true }
markets: { fred_api_key_env: "x", daily_series: [], intraday_series: [],
           daily_stale_after_hours: 1, triggers: {} }
llm: { order: [], stages: {}, providers: {}, backoff: {} }
delivery: { telegram_max_chars: 4096, char_budget: {}, truncate_priority: [],
            output_language: "en", source_languages: [] }
""",
        encoding="utf-8",
    )
    (tmp_path / "credibility.yaml").write_text(
        """
version: 1
sources:
  some_source:
    tier: 7
    group: some_group
""",
        encoding="utf-8",
    )
    # Valid on purpose: the test exists to prove the THREE problems above
    # are all reported, not to add a fourth.
    (tmp_path / "relevance.yaml").write_text(
        """
weights: {iran_direct: 8, strategic: 4, economy: 3}
keywords:
  iran_direct: ["ایران"]
  strategic: ["جنگ"]
  economy: ["نفت"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_all(base=tmp_path)

    message = str(exc_info.value)
    assert "unknown key 'bogus_top_level_key'" in message
    assert "missing required key 'ops'" in message
    assert "invalid tier" in message
