"""Tests for the strict nested `llm:` block validation (PHASE_5_BRIEF §10,
settings_llm.py). Before it, `providers` was Mapping[str, Any] and
`rpm: "ten"`, `supports_vision: 1` and `enabled: "no"` all loaded silently.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from agent.settings import Settings, SettingsError

_REPO_ROOT = Path(__file__).parent.parent.parent
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"


def _raw() -> dict:
    return yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))


def _expect_error(mutate, match):
    raw = _raw()
    mutate(raw)
    with pytest.raises(SettingsError, match=match):
        Settings.from_dict(raw)


def test_real_settings_yaml_has_owner_decided_ceiling():
    raw = yaml.safe_load((_REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    settings = Settings.from_dict(raw)
    # Owner decision 2026-08-31 (session 9o): 51 -> 70. Evidence: the 13:03
    # run exhausted 51 calls having processed 21 of 40 clusters while groq
    # was still answering. 70 x 6 runs = 420/day ceiling; Gemini's free tier
    # is now 20 RPD (see the gemini provider block). Groq carries the bulk.
    # Do not change without re-checking the arithmetic in the comment.
    assert settings.llm.max_calls_per_run == 70


def test_fixture_loads_under_new_validation():
    settings = Settings.from_dict(_raw())
    assert settings.llm.max_calls_per_run == 70
    assert set(settings.llm.providers) == {"gemini", "groq", "openrouter"}


def test_rpm_string_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"]["gemini"].__setitem__("rpm", "ten"),
        r"providers\.gemini\.rpm: expected int, got str",
    )


def test_rpm_bool_rejected():
    # True == 1 in Python; the bool check must run first.
    _expect_error(
        lambda r: r["llm"]["providers"]["gemini"].__setitem__("rpm", True),
        r"providers\.gemini\.rpm: expected int, got bool",
    )


def test_rpd_bool_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"]["groq"].__setitem__("rpd", True),
        r"providers\.groq\.rpd: expected int, got bool",
    )


def test_supports_vision_must_be_a_bool_not_int():
    _expect_error(
        lambda r: r["llm"]["providers"]["gemini"].__setitem__("supports_vision", 1),
        r"providers\.gemini\.supports_vision: expected bool, got int",
    )


def test_supports_vision_missing_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"]["gemini"].pop("supports_vision"),
        r"providers\.gemini: missing required key 'supports_vision'",
    )


def test_enabled_string_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"].__setitem__(
            "anthropic",
            {"enabled": "no", "supports_vision": True},
        ),
        r"providers\.anthropic\.enabled: expected bool, got str",
    )


def test_unknown_provider_key_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"]["gemini"].__setitem__("rpm_limit", 10),
        r"providers\.gemini: unknown key 'rpm_limit'",
    )


def test_negative_rpm_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"]["gemini"].__setitem__("rpm", -1),
        r"providers\.gemini\.rpm: must not be negative",
    )


def test_order_naming_unknown_provider_rejected():
    _expect_error(
        lambda r: r["llm"].__setitem__("order", ["gemini", "nope"]),
        r"order: provider 'nope' has no providers: entry",
    )


def test_stage_provider_naming_unknown_provider_rejected():
    _expect_error(
        lambda r: r["llm"]["stages"].__setitem__("vision", {"provider": "nope"}),
        r"stages\.vision\.provider: provider 'nope' has no providers: entry",
    )


def test_cascade_adjudicator_naming_unknown_provider_rejected():
    _expect_error(
        lambda r: r["llm"]["stages"].__setitem__(
            "understand", {"provider": "gemini", "cascade_adjudicator": "nope"}
        ),
        r"stages\.understand\.cascade_adjudicator: provider 'nope' has no providers: entry",
    )


def test_missing_max_calls_per_run_rejected():
    _expect_error(
        lambda r: r["llm"].pop("max_calls_per_run"),
        r"settings\.llm: missing required key 'max_calls_per_run'",
    )


def test_backoff_numeric_string_rejected():
    _expect_error(
        lambda r: r["llm"]["backoff"].__setitem__("max_retries", "3"),
        r"backoff\.max_retries: expected int, got str",
    )


def test_backoff_unknown_key_rejected():
    _expect_error(
        lambda r: r["llm"]["backoff"].__setitem__("jitter", 1),
        r"backoff: unknown key 'jitter'",
    )


def test_metered_provider_block_types_validated_and_built():
    raw = _raw()
    raw["llm"]["providers"]["anthropic"] = {
        "enabled": False,
        "model": "claude-haiku-4-5",
        "supports_vision": True,
        "prompt_cache": True,
        "max_calls_per_run": 15,
        "max_spend_usd_per_month": 20,
        "halt_on_budget_exceeded": True,
        "input_usd_per_mtok": 1.0,
        "output_usd_per_mtok": 5.0,
    }
    settings = Settings.from_dict(raw)
    anthropic = settings.llm.providers["anthropic"]
    assert anthropic.enabled is False
    assert anthropic.max_calls_per_run == 15
    assert anthropic.max_spend_usd_per_month == 20.0
    assert anthropic.input_usd_per_mtok == 1.0


def test_metered_spend_cap_string_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"].__setitem__(
            "anthropic",
            {
                "enabled": False,
                "supports_vision": True,
                "max_spend_usd_per_month": "20",
            },
        ),
        r"providers\.anthropic\.max_spend_usd_per_month: expected float, got str",
    )


def test_settings_llm_block_is_immutable():
    settings = Settings.from_dict(_raw())
    with pytest.raises(FrozenInstanceError):
        settings.llm.max_calls_per_run = 1  # type: ignore[misc]


def test_read_timeout_seconds_accepted_and_defaults_to_none():
    raw = _raw()
    raw["llm"]["providers"]["gemini"]["read_timeout_seconds"] = 20
    settings = Settings.from_dict(raw)
    assert settings.llm.providers["gemini"].read_timeout_seconds == 20
    assert settings.llm.providers["groq"].read_timeout_seconds is None


def test_read_timeout_seconds_string_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"]["gemini"].__setitem__("read_timeout_seconds", "20"),
        r"providers\.gemini\.read_timeout_seconds: expected int, got str",
    )


def test_read_timeout_seconds_negative_rejected():
    _expect_error(
        lambda r: r["llm"]["providers"]["gemini"].__setitem__("read_timeout_seconds", -1),
        r"providers\.gemini\.read_timeout_seconds: must not be negative",
    )
