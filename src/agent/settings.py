"""Typed, frozen dataclass mirroring config/settings.yaml, with strict validation.

Strict by design: an unknown key or a missing required key is an error, never a
silent default. A typo'd threshold that silently falls back to a default is the
worst failure mode this project has, because risk scores would then be computed
from a value the owner never actually set (CLAUDE.md constraint #3).

Section shapes live in settings_schema.py to keep this file under the ~200-line
limit (CLAUDE.md constraint #12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_type_hints

from agent.leaf_types import _type_matches, _type_name
from agent.settings_llm import build_llm
from agent.settings_schema import (
    SECTIONS,
    TOP_KEYS,
    AlertingSettings,
    CollectionSettings,
    DeliverySettings,
    DigestRankSettings,
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


def _range_ok(value: Any) -> bool:
    """Every numeric leaf in this schema is a count, limit, interval, or
    threshold -- none has a legitimate negative value."""
    return not (isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0)


def _cross_section_errors(built: dict[str, Any]) -> list[str]:
    """Round-3 review, fix 3: settings.yaml invites the owner to nudge
    `event_match_threshold`/`event_repeat_threshold`/`max_messages` (the
    comment block next to them says so), but the per-leaf check above only
    verifies type and non-negativity -- it cannot see relationships BETWEEN
    sections, or that a threshold is a fraction, not just a positive float.
    Runs only once every leaf has already validated clean (see from_dict),
    so every attribute read here is guaranteed present."""
    errors: list[str] = []
    pipeline = built["pipeline"]
    digest_rank = built["digest_rank"]

    def _fraction(path: str, value: float) -> None:
        if not (0.0 <= value <= 1.0):
            errors.append(f"{path}: must be between 0.0 and 1.0, got {value!r}")

    _fraction("settings.pipeline.event_match_threshold", pipeline.event_match_threshold)
    _fraction("settings.digest_rank.event_repeat_threshold", digest_rank.event_repeat_threshold)

    if (
        0.0 <= pipeline.event_match_threshold <= 1.0
        and 0.0 <= digest_rank.event_repeat_threshold <= 1.0
        and digest_rank.event_repeat_threshold < pipeline.event_match_threshold
    ):
        errors.append(
            "settings.digest_rank.event_repeat_threshold "
            f"({digest_rank.event_repeat_threshold!r}) must be >= "
            "settings.pipeline.event_match_threshold "
            f"({pipeline.event_match_threshold!r}) -- the repeat gate's "
            "HIGH ('same story') band cannot be looser than its MID "
            "('worth comparing') floor, see pipeline/repeats.py"
        )

    if digest_rank.max_messages < 1:
        errors.append(
            "settings.digest_rank.max_messages: must be at least 1, got "
            f"{digest_rank.max_messages!r} -- 0 crashes delivery/budget.py "
            "on an empty page list"
        )

    return errors


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
        if expected is not None and getattr(expected, "__dataclass_fields__", None):
            # Nested dataclass (llm.backoff): leaf checks cannot express it.
            # A section-specific builder validates and rebuilds it after this
            # loop (settings_llm.build_llm); pass the raw dict through.
            result[key] = value
            continue
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
    digest_rank: DigestRankSettings
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
        if "llm" in raw:
            # The llm block has nested shapes the generic section check cannot
            # express (typed provider entries, order/stages cross-references).
            # settings_llm.build_llm rebuilds the constructor kwargs strictly.
            section_kwargs["llm"] = build_llm(raw["llm"], errors)

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

        cross_errors = _cross_section_errors(built)
        if cross_errors:
            raise SettingsError("; ".join(cross_errors))

        return cls(version=version, **built)
