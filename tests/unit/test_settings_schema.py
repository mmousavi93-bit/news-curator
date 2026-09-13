from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from agent.settings import Settings, SettingsError

_REPO_ROOT = Path(__file__).parent.parent.parent
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_real_repo_settings_yaml_loads_clean():
    raw = _load(_REPO_ROOT / "config" / "settings.yaml")
    settings = Settings.from_dict(raw)
    assert isinstance(settings, Settings)
    assert settings.version == 1
    assert settings.schedule.timezone == "Asia/Tehran"


def test_unknown_top_level_key_raises():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["totally_made_up"] = True
    with pytest.raises(SettingsError, match="unknown key 'totally_made_up'"):
        Settings.from_dict(raw)


def test_unknown_nested_key_raises():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["ops"]["totally_made_up"] = True
    with pytest.raises(SettingsError, match="settings.ops: unknown key"):
        Settings.from_dict(raw)


def test_missing_required_top_level_key_raises():
    raw = copy.deepcopy(_load(_FIXTURE))
    del raw["ops"]
    with pytest.raises(SettingsError, match="missing required key 'ops'"):
        Settings.from_dict(raw)


def test_missing_required_nested_key_raises():
    raw = copy.deepcopy(_load(_FIXTURE))
    del raw["schedule"]["timezone"]
    with pytest.raises(SettingsError, match="settings.schedule: missing required key 'timezone'"):
        Settings.from_dict(raw)


def test_bool_rejected_where_int_expected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["collection"]["max_items_per_source"] = True
    with pytest.raises(SettingsError, match=r"max_items_per_source: expected int, got bool"):
        Settings.from_dict(raw)


def test_numeric_looking_string_rejected_where_int_expected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["collection"]["max_items_per_source"] = "twenty"
    with pytest.raises(SettingsError, match=r"max_items_per_source: expected int, got str"):
        Settings.from_dict(raw)


def test_numeric_string_that_looks_valid_is_still_rejected_not_coerced():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["collection"]["max_items_per_source"] = "20"
    with pytest.raises(SettingsError, match=r"max_items_per_source: expected int, got str"):
        Settings.from_dict(raw)


def test_bool_rejected_where_float_expected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["pipeline"]["cluster_similarity_threshold"] = True
    with pytest.raises(SettingsError, match=r"cluster_similarity_threshold: expected float, got bool"):
        Settings.from_dict(raw)


def test_negative_count_rejected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["collection"]["max_items_per_source"] = -1
    with pytest.raises(SettingsError, match=r"max_items_per_source: must not be negative"):
        Settings.from_dict(raw)


def test_negative_version_rejected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["version"] = -1
    with pytest.raises(SettingsError, match=r"settings.version: must not be negative"):
        Settings.from_dict(raw)


def test_bool_version_rejected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["version"] = True
    with pytest.raises(SettingsError, match=r"settings.version: expected int, got bool"):
        Settings.from_dict(raw)


def test_settings_object_is_immutable():
    raw = copy.deepcopy(_load(_FIXTURE))
    settings = Settings.from_dict(raw)
    with pytest.raises(FrozenInstanceError):
        settings.version = 2  # type: ignore[misc]


def test_nested_section_is_immutable():
    raw = copy.deepcopy(_load(_FIXTURE))
    settings = Settings.from_dict(raw)
    with pytest.raises(FrozenInstanceError):
        settings.ops.mock_mode = False  # type: ignore[misc]


# --- round-3 review, fix 3: cross-section threshold validation -------------
# settings.yaml's comment block next to these values explicitly invites the
# owner to nudge them; the per-leaf check only verified type/non-negativity,
# so a typo like "1.5" or "0" passed clean and broke invariants other code
# relies on silently (a >1.0 threshold never gates anything; max_messages=0
# crashes delivery/budget.py on pages[-1]).

def test_event_match_threshold_above_one_rejected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["pipeline"]["event_match_threshold"] = 1.5
    with pytest.raises(SettingsError, match=r"event_match_threshold: must be between 0.0 and 1.0"):
        Settings.from_dict(raw)


def test_event_match_threshold_negative_rejected():
    # Caught by the pre-existing generic non-negative check (runs before
    # the new cross-section fraction check) -- still rejected, just with
    # the older message. Pinned so fix 3's test class documents the full
    # [0.0, 1.0] requirement, upper bound AND lower.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["pipeline"]["event_match_threshold"] = -0.1
    with pytest.raises(SettingsError, match=r"event_match_threshold: must not be negative"):
        Settings.from_dict(raw)


def test_event_repeat_threshold_above_one_rejected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["digest_rank"]["event_repeat_threshold"] = 1.2
    with pytest.raises(SettingsError, match=r"event_repeat_threshold: must be between 0.0 and 1.0"):
        Settings.from_dict(raw)


def test_event_repeat_threshold_below_match_threshold_rejected():
    # The repeat gate's HIGH ("same story") band must not be looser than
    # its MID ("worth comparing") floor -- see pipeline/repeats.py's
    # two-band design. Fixture ships event_match_threshold=0.55.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["digest_rank"]["event_repeat_threshold"] = 0.50
    with pytest.raises(SettingsError, match=r"event_repeat_threshold \(0.5\) must be >= "
                                            r"settings.pipeline.event_match_threshold \(0.55\)"):
        Settings.from_dict(raw)


def test_event_repeat_threshold_equal_to_match_threshold_is_allowed():
    # >= , not >: a MID band of zero width (every match is HIGH) is a valid
    # owner choice, not a config error.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["pipeline"]["event_match_threshold"] = 0.6
    raw["digest_rank"]["event_repeat_threshold"] = 0.6
    settings = Settings.from_dict(raw)
    assert settings.digest_rank.event_repeat_threshold == 0.6


def test_max_messages_zero_rejected():
    # max_messages=0 currently crashes delivery/budget.py on pages[-1] --
    # this must be caught at config-load time, not at send time.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["digest_rank"]["max_messages"] = 0
    with pytest.raises(SettingsError, match=r"max_messages: must be at least 1, got 0"):
        Settings.from_dict(raw)


def test_repeat_bypass_score_negative_still_rejected_by_generic_check():
    # Not new logic -- the existing generic non-negative check on every
    # numeric leaf already covers this field; pinned here so fix 3's test
    # class documents the full requirement list, not just the two new
    # cross-section rules.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["digest_rank"]["repeat_bypass_score"] = -1.0
    with pytest.raises(SettingsError, match=r"repeat_bypass_score: must not be negative"):
        Settings.from_dict(raw)


# ---------------------------------------------------------------------------
# Session 9s: batch_size + per-provider tpm guard (settings_guard.py)
# ---------------------------------------------------------------------------


def test_batch_size_zero_rejected():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["llm"]["batch_size"] = 0
    with pytest.raises(SettingsError, match=r"batch_size: must be at least 1"):
        Settings.from_dict(raw)


def test_batch_size_one_is_the_allowed_rollback_path():
    # Brief requirement 3: 1 must remain a valid value -- the guard skips
    # it entirely, whatever the tpms are.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["llm"]["batch_size"] = 1
    raw["llm"]["providers"]["groq"]["tpm"] = 8000
    settings = Settings.from_dict(raw)
    assert settings.llm.batch_size == 1


def test_batch_size_exceeding_smallest_tpm_rejected():
    # The brief's arithmetic table: nominal batch = ~2,300 template + ~700
    # per cluster. batch 12 => ~10,700 > groq's 8,000 TPM => a batch that
    # 429s permanently regardless of pacing. Refused at load.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["llm"]["batch_size"] = 12
    raw["llm"]["providers"]["groq"]["tpm"] = 8000
    with pytest.raises(SettingsError, match=r"permanently"):
        Settings.from_dict(raw)


def test_batch_size_within_tpm_allowed():
    # batch 7 => ~7,200 <= 8,000: the brief's "fits, no headroom" row.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["llm"]["batch_size"] = 7
    raw["llm"]["providers"]["groq"]["tpm"] = 8000
    settings = Settings.from_dict(raw)
    assert settings.llm.batch_size == 7


def test_providers_without_tpm_do_not_bind_the_guard():
    # bai's tpm is unrecorded (settings.yaml): missing tpm = unconstrained,
    # so a batch fits whatever groq/gemini allow and bai never binds.
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["llm"]["batch_size"] = 5
    settings = Settings.from_dict(raw)  # fixture providers carry no tpm
    assert settings.llm.batch_size == 5


def test_samerun_pair_log_floor_must_be_a_fraction():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["pipeline"]["samerun_pair_log_floor"] = 1.5
    with pytest.raises(SettingsError, match=r"samerun_pair_log_floor: must be between"):
        Settings.from_dict(raw)


def test_tpm_must_be_int():
    raw = copy.deepcopy(_load(_FIXTURE))
    raw["llm"]["providers"]["groq"]["tpm"] = "8000"
    with pytest.raises(SettingsError, match=r"tpm: expected int"):
        Settings.from_dict(raw)
