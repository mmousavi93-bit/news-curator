"""Strict loader for config/flash_alert.yaml (same house rule as
relevance.py/topics.yaml): collect ALL problems and raise ConfigError
once. A typo'd bucket name, a non-string keyword, or a missing template
placeholder must fail the run loudly — a silent shape drift would
silently widen (spam) or narrow (dead monitor) the alert, and a dead
emergency monitor is worse than no monitor. Helper validators live in
config.py next to the dataclasses; this file is the orchestration."""

from __future__ import annotations

from agent.config import ConfigError
from agent.flash.config import (
    _REQUIRED_PLACEHOLDERS,
    _TEMPLATE_KEYS,
    AlertClass,
    FlashConfig,
    check_class,
    check_int,
    check_lang_map,
    check_str_list,
)

_CONFIG_VERSION = 3


def validate_flash(raw: object) -> FlashConfig:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"flash_alert.yaml: expected a mapping, got {type(raw).__name__}"
        )
    errors: list[str] = []

    version = raw.get("version")
    if version != _CONFIG_VERSION:
        errors.append(
            f"flash_alert.yaml: version must be {_CONFIG_VERSION}, got {version!r} "
            "— the loader only knows the config shape it was written for"
        )

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append(f"flash_alert.yaml: 'enabled' must be a bool, got {enabled!r}")
        enabled = True

    flash_source_ids = check_str_list(raw.get("flash_source_ids"),
                                      "flash_source_ids", errors)
    exclusions = check_lang_map(raw.get("exclusions"), "exclusions", errors)

    matching_raw = raw.get("matching")
    if not isinstance(matching_raw, dict):
        errors.append("flash_alert.yaml: 'matching' must be a mapping")
        matching_raw = {}
    freshness_minutes = check_int(matching_raw.get("freshness_minutes"),
                                  "matching.freshness_minutes", 1, errors)
    window_chars = check_int(matching_raw.get("window_chars"),
                             "matching.window_chars", 1, errors)

    burst_raw = raw.get("burst")
    if not isinstance(burst_raw, dict):
        errors.append("flash_alert.yaml: 'burst' must be a mapping")
        burst_raw = {}
    collapse = check_int(burst_raw.get("collapse_window_minutes"),
                         "burst.collapse_window_minutes", 1, errors)
    followup_window = check_int(burst_raw.get("followup_window_minutes"),
                                "burst.followup_window_minutes", 1, errors)
    followups_raw = burst_raw.get("followups")
    followups: tuple[int, ...] = ()
    if (isinstance(followups_raw, list)
            and all(isinstance(v, int) and not isinstance(v, bool) and v > 1
                    for v in followups_raw)):
        followups = tuple(sorted(followups_raw))
        if len(followups) != len(set(followups)):
            errors.append("flash_alert.yaml: burst.followups must not repeat values")
    else:
        errors.append("flash_alert.yaml: burst.followups must be a list of ints > 1")

    caps_raw = raw.get("caps")
    if not isinstance(caps_raw, dict):
        errors.append("flash_alert.yaml: 'caps' must be a mapping")
        caps_raw = {}
    max_alerts = check_int(caps_raw.get("max_alerts_per_hour"),
                           "caps.max_alerts_per_hour", 1, errors)

    classes_raw = raw.get("classes")
    classes: dict[str, AlertClass] = {}
    if not isinstance(classes_raw, dict):
        errors.append("flash_alert.yaml: 'classes' must be a mapping")
    else:
        for name, class_raw in classes_raw.items():
            alert_class = check_class(class_raw, name, errors)
            if alert_class is not None:
                classes[name] = alert_class

    templates_raw = raw.get("templates")
    templates: dict[str, str] = {}
    if not isinstance(templates_raw, dict):
        errors.append("flash_alert.yaml: 'templates' must be a mapping")
    else:
        for key in _TEMPLATE_KEYS:
            value = templates_raw.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"flash_alert.yaml: templates.{key} must be a non-empty string")
                continue
            for placeholder in _REQUIRED_PLACEHOLDERS.get(key, ()):
                if placeholder not in value:
                    errors.append(
                        f"flash_alert.yaml: templates.{key} missing {placeholder}"
                    )
            templates[key] = value

    if errors:
        raise ConfigError(
            "flash_alert.yaml invalid: " + "; ".join(errors[:10])
            + (f" (+{len(errors) - 10} more)" if len(errors) > 10 else "")
        )

    return FlashConfig(
        enabled=enabled,
        flash_source_ids=flash_source_ids,
        exclusions=exclusions,
        freshness_minutes=freshness_minutes,
        window_chars=window_chars,
        collapse_window_minutes=collapse,
        followup_window_minutes=followup_window,
        followups=followups,
        max_alerts_per_hour=max_alerts,
        classes=classes,
        templates=templates,
    )
