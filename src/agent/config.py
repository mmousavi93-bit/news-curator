"""Config discovery, parsing, and validation.

Fail-fast: load_all raises ConfigError listing EVERY problem found across every
config file in one exception, never just the first. A config-validation error
message must name the offending key path, never echo the value -- some day that
value will be a Telegram chat ID or a leaked fragment of a URL query string.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from agent.settings import Settings, SettingsError

_VALID_INT_TIERS = (1, 2, 3)
_CREDIBILITY_ENTRY_KEYS = {"tier", "group", "note"}


class ConfigError(Exception):
    """Raised for any config file or schema problem."""


class _DuplicateKeyCheckingLoader(yaml.SafeLoader):
    """SafeLoader that raises ConfigError on a duplicate mapping key.

    Plain `yaml.safe_load` silently accepts duplicate keys, last-write-wins
    (`version: 1` then `version: 2` loads as 2, no error). That is a silent
    config-corruption path, so every YAML load in this codebase goes through
    this loader instead.
    """

    def construct_mapping(self, node, deep=False):
        seen: dict[Any, int] = {}
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            line = key_node.start_mark.line + 1
            if key in seen:
                raise ConfigError(
                    f"duplicate key '{key}' at line {line} (first seen at line {seen[key]})"
                )
            seen[key] = line
        return super().construct_mapping(node, deep=deep)


def _valid_credibility_tier(tier: object) -> bool:
    """Exactly the ints 1, 2, 3, or the string 'lead'. Checked by type
    identity, not `in` membership -- `True == 1` and `1.0 == 1` in Python, so
    a naive `tier in {1, 2, 3, "lead"}` membership test would accept
    `tier: true` and `tier: 1.0` and silently store them with the wrong type."""
    if isinstance(tier, bool):
        return False
    if isinstance(tier, int):
        return tier in _VALID_INT_TIERS
    if isinstance(tier, str):
        return tier == "lead"
    return False


def config_dir() -> Path:
    """Resolve config/ from AGENT_CONFIG_DIR env var, else repo root. No side effects."""
    env = os.environ.get("AGENT_CONFIG_DIR")
    if env:
        return Path(env)
    # src/agent/config.py -> src/agent -> src -> repo root
    return Path(__file__).resolve().parents[2] / "config"


def load_yaml(name: str, *, base: Path | None = None) -> dict:
    """Parse one config file. Raises ConfigError on missing file or malformed YAML.
    Never returns None for an empty document -- returns {}."""
    directory = base if base is not None else config_dir()
    path = directory / name
    if not path.is_file():
        raise ConfigError(f"config file not found: {name} (looked in {directory})")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read {name}: {exc}") from exc
    try:
        data = yaml.load(text, Loader=_DuplicateKeyCheckingLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"malformed YAML in {name}: {exc}") from exc
    except ConfigError as exc:
        raise ConfigError(f"{name}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{name} must parse to a mapping, got {type(data).__name__}")
    return data


@dataclass(frozen=True, slots=True)
class SourceCredibility:
    """One entry from config/credibility.yaml.

    `tier` drives signal weight (1/2/3, or "lead" -- zero confirmation weight,
    can never corroborate another signal). `group` drives independence: two
    sources confirm each other only if group_a != group_b.
    """

    tier: int | str
    group: str | None
    note: str | None = None


def _validate_credibility(raw: dict, errors: list[str]) -> dict[str, SourceCredibility]:
    sources_raw = raw.get("sources", {})
    if not isinstance(sources_raw, dict):
        errors.append("credibility.sources: expected a mapping")
        return {}
    result: dict[str, SourceCredibility] = {}
    for source_id in sorted(sources_raw):
        entry = sources_raw[source_id]
        if not isinstance(entry, dict):
            errors.append(f"credibility.sources.{source_id}: expected a mapping")
            continue
        unknown = sorted(set(entry) - _CREDIBILITY_ENTRY_KEYS)
        if unknown:
            errors.append(f"credibility.sources.{source_id}: unknown key(s) {unknown}")
        tier = entry.get("tier")
        if not _valid_credibility_tier(tier):
            errors.append(
                f"credibility.sources.{source_id}: invalid tier "
                f"(must be 1, 2, 3, or 'lead'; got {tier!r} of type {type(tier).__name__})"
            )
            continue
        result[source_id] = SourceCredibility(
            tier=tier, group=entry.get("group"), note=entry.get("note")
        )
    return result


@dataclass(frozen=True, slots=True)
class Config:
    settings: Settings
    credibility: Mapping[str, SourceCredibility]


def load_all(*, base: Path | None = None) -> Config:
    """Load settings + credibility, validate, freeze, return.

    Fail-fast: raises ConfigError listing EVERY problem found, not just the
    first. Risk weights and sources are added to this loader in Phases 7/8 --
    they do not exist yet, so they are not loaded here.
    """
    directory = base if base is not None else config_dir()
    errors: list[str] = []

    settings_raw: dict | None = None
    try:
        settings_raw = load_yaml("settings.yaml", base=directory)
    except ConfigError as exc:
        errors.append(str(exc))

    credibility_raw: dict | None = None
    try:
        credibility_raw = load_yaml("credibility.yaml", base=directory)
    except ConfigError as exc:
        errors.append(str(exc))

    settings_obj: Settings | None = None
    if settings_raw is not None:
        try:
            settings_obj = Settings.from_dict(settings_raw)
        except SettingsError as exc:
            errors.append(str(exc))

    credibility_obj: dict[str, SourceCredibility] = {}
    if credibility_raw is not None:
        credibility_obj = _validate_credibility(credibility_raw, errors)

    if errors:
        raise ConfigError("; ".join(errors))

    assert settings_obj is not None  # guaranteed: no errors means it loaded and validated
    return Config(settings=settings_obj, credibility=credibility_obj)
