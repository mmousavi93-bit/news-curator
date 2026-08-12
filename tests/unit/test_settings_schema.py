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
