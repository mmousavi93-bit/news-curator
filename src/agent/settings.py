"""Typed, frozen dataclass mirroring config/settings.yaml, with strict validation.

Strict by design: an unknown key or a missing required key is an error, never a
silent default. A typo'd threshold that silently falls back to a default is the
worst failure mode this project has, because risk scores would then be computed
from a value the owner never actually set (CLAUDE.md constraint #3).

Section shapes live in settings_schema.py to keep this file under the ~200-line
limit (CLAUDE.md constraint #12).
"""

from __future__ import annotations

import collections.abc as cabc
import typing
from dataclasses import dataclass
from typing import Any, get_type_hints

from agent.settings_schema import (
    SECTIONS,
    TOP_KEYS,
    AlertingSettings,
    CollectionSettings,
    DeliverySettings,
    LlmSettings,
    MarketsSettings,
    OpsSettings,
    PipelineSettings,
    RetentionSettings,
    ScheduleSettings,
    ScoringSettings,
)


class SettingsError(Exception):
    """Raised when a raw settings dict fails strict schema validation."""


def _type_name(expected: Any) -> str:
    return expected.__name__ if isinstance(expected, type) else str(expected)


def _type_matches(value: Any, expected: Any) -> bool:
    """True if `value`'s runtime type matches the leaf annotation `expected`.

    Two traps this exists to close, both real in PyYAML output:
      - `isinstance(True, int)` is True in Python, so a naive int/float check
        would silently accept `max_items_per_source: true`. bool is checked
        and rejected explicitly, for both int and float fields.
      - A numeric-looking string (`"20"`) is never coerced. If the field
        expects int/float and the value is a str, that is a type mismatch,
        full stop -- coercion would hide a config typo instead of failing on it.
    """
    origin = typing.get_origin(expected)
    if origin is not None:
        if isinstance(origin, type) and issubclass(origin, cabc.Mapping):
            return isinstance(value, dict)
        if isinstance(origin, type) and issubclass(origin, cabc.Sequence):
            return isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes))
        return isinstance(value, origin)
    if expected is Any:
        return True
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is str:
        return isinstance(value, str)
    return isinstance(value, expected)


def _range_ok(value: Any) -> bool:
    """Every numeric leaf in this schema is a count, limit, interval, or
    threshold -- none has a legitimate negative value."""
    return not (isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0)


def _check(
    path: str, raw: Any, fields: tuple[str, ...], dc: type, errors: list[str]
) -> dict[str, Any]:
    """Verify `raw` is a mapping with exactly `fields` as keys, each leaf typed
    per `dc`'s annotations and, for numeric leaves, non-negative. Records every
    problem into `errors` instead of raising, so callers can report all of them
    in one exception."""
    if not isinstance(raw, dict):
        errors.append(f"{path}: expected a mapping, got {type(raw).__name__}")
        return {}
    raw_keys = set(raw)
    field_set = set(fields)
    for missing in sorted(field_set - raw_keys):
        errors.append(f"{path}: missing required key '{missing}'")
    for extra in sorted(raw_keys - field_set):
        errors.append(f"{path}: unknown key '{extra}'")

    expected_types = get_type_hints(dc)
    result: dict[str, Any] = {}
    for key in fields:
        if key not in raw:
            continue
        value = raw[key]
        expected = expected_types.get(key)
        if expected is not None and not _type_matches(value, expected):
            errors.append(
                f"{path}.{key}: expected {_type_name(expected)}, "
                f"got {type(value).__name__} (value={value!r})"
            )
            continue
        if expected in (int, float) and not _range_ok(value):
            errors.append(f"{path}.{key}: must not be negative, got {value!r}")
            continue
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class Settings:
    version: int
    schedule: ScheduleSettings
    collection: CollectionSettings
    pipeline: PipelineSettings
    retention: RetentionSettings
    scoring: ScoringSettings
    alerting: AlertingSettings
    markets: MarketsSettings
    llm: LlmSettings
    delivery: DeliverySettings
    ops: OpsSettings

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        """Strict. An unknown key is an error, not a warning. Every problem in
        the whole document is collected before a single SettingsError is raised."""
        if not isinstance(raw, dict):
            raise SettingsError(f"settings: expected a mapping, got {type(raw).__name__}")

        errors: list[str] = []
        raw_keys = set(raw)
        for missing in sorted(set(TOP_KEYS) - raw_keys):
            errors.append(f"settings: missing required key '{missing}'")
        for extra in sorted(raw_keys - set(TOP_KEYS)):
            errors.append(f"settings: unknown key '{extra}'")

        section_kwargs: dict[str, dict[str, Any]] = {}
        for name, dc, fields in SECTIONS:
            if name in raw:
                section_kwargs[name] = _check(f"settings.{name}", raw[name], fields, dc, errors)

        version = raw.get("version")
        if "version" in raw:
            # isinstance(True, int) is True in Python -- reject bool explicitly,
            # the same trap _type_matches() closes for every section leaf.
            if isinstance(version, bool) or not isinstance(version, int):
                errors.append(
                    f"settings.version: expected int, got {type(version).__name__} "
                    f"(value={version!r})"
                )
            elif version < 0:
                errors.append(f"settings.version: must not be negative, got {version!r}")

        if errors:
            raise SettingsError("; ".join(errors))

        built = {name: dc(**section_kwargs[name]) for name, dc, _ in SECTIONS}
        return cls(version=version, **built)
