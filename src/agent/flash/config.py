"""The flash monitor's config dataclasses + the small validation helpers
the loader uses. YAML loading lives in loader.py (split 2026-08-31,
constraint 12 — config.py was 261 lines doing two jobs)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from agent.config import ConfigError
from agent.flash.textnorm import normalize

_LANGS = ("fa", "en", "ar")
_RINGS = ("city", "region", "iran_geo", "actors")
_TEMPLATE_KEYS = ("first", "followup", "system_down", "deescalation")
_REQUIRED_PLACEHOLDERS = {
    "first": ("{label}", "{headline}", "{location_token}", "{first_source}",
              "{more_sources}", "{convergence}", "{jalali}", "{tehran_time}"),
    "followup": ("{n}", "{label}", "{headline}"),
    "deescalation": ("{n}",),
}


@dataclass(frozen=True, slots=True)
class AlertClass:
    name: str
    label: str
    terms: Mapping[str, tuple[str, ...]]
    locations: Mapping[str, tuple[str, ...]]
    quiet_hours: int
    quiet_requires_sources: int
    burst_scope: str                 # "signature" | "class"
    collapse_window_minutes: int | None  # None = global burst default
    ring_requirements: Mapping[str, tuple[str, ...]]  # bucket -> allowed rings


@dataclass(frozen=True, slots=True)
class FlashConfig:
    enabled: bool
    flash_source_ids: tuple[str, ...]
    exclusions: tuple[str, ...]
    freshness_minutes: int
    window_chars: int
    collapse_window_minutes: int
    followup_window_minutes: int
    followups: tuple[int, ...]
    max_alerts_per_hour: int
    novelty_min_gap_minutes: int
    momentum_streak_window_days: int
    momentum_streak_repeat_threshold_days: int
    momentum_repeat_requires_sources: int
    deescalation_quiet_days: int
    deescalation_cooldown_days: int
    classes: Mapping[str, AlertClass]
    templates: Mapping[str, str]


# -- validation helpers (loader.py) ---------------------------------------


def check_str_list(value: object, label: str,
                   errors: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors.append(f"flash_alert.yaml: '{label}' must be a list of non-empty strings")
        return ()
    return tuple(v.strip() for v in value)


def check_int(value: object, label: str, minimum: int,
              errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        errors.append(
            f"flash_alert.yaml: '{label}' must be an int >= {minimum}, got {value!r}"
        )
        return minimum
    return value


def check_lang_map(value: object, label: str,
                   errors: list[str]) -> tuple[str, ...]:
    """A {fa: [...], en: [...], ar: [...]} keyword block -> one flat,
    NORMALIZED tuple. Normalization happens HERE (textnorm.normalize,
    the same transform the matcher applies to item text) so config and
    text always meet in the same space — raw ZWNJ keywords could never
    match normalized text (reviewer finding 2026-08-31)."""
    if not isinstance(value, dict):
        errors.append(f"flash_alert.yaml: '{label}' must be a lang mapping")
        return ()
    merged: list[str] = []
    for lang, kws in value.items():
        if lang not in _LANGS:
            errors.append(f"flash_alert.yaml: '{label}' unknown lang {lang!r}")
            continue
        merged.extend(normalize(k) for k in check_str_list(kws, f"{label}.{lang}", errors))
    return tuple(merged)


def check_class(raw: object, name: str, errors: list[str]) -> AlertClass | None:
    if not isinstance(raw, dict):
        errors.append(f"flash_alert.yaml: classes.{name} must be a mapping")
        return None
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        errors.append(f"flash_alert.yaml: classes.{name}.label must be a non-empty string")
        label = name

    terms_raw = raw.get("terms")
    terms: dict[str, tuple[str, ...]] = {}
    if not isinstance(terms_raw, dict):
        errors.append(f"flash_alert.yaml: classes.{name}.terms must be a mapping")
    else:
        for bucket, langs in terms_raw.items():
            if not isinstance(bucket, str) or not bucket.strip():
                errors.append(f"flash_alert.yaml: classes.{name} has an unnamed term bucket")
                continue
            merged = check_lang_map(langs, f"classes.{name}.terms.{bucket}", errors)
            if merged:
                terms[bucket] = merged

    locations_raw = raw.get("locations")
    locations: dict[str, tuple[str, ...]] = {}
    if not isinstance(locations_raw, dict):
        errors.append(f"flash_alert.yaml: classes.{name}.locations must be a mapping")
    else:
        for ring, langs in locations_raw.items():
            if ring not in _RINGS:
                errors.append(
                    f"flash_alert.yaml: classes.{name} unknown location ring {ring!r}"
                )
                continue
            merged = check_lang_map(langs, f"classes.{name}.locations.{ring}", errors)
            if merged:
                locations[ring] = merged

    quiet_hours = check_int(raw.get("quiet_hours"),
                            f"classes.{name}.quiet_hours", 0, errors)
    quiet_requires = check_int(raw.get("quiet_requires_sources"),
                               f"classes.{name}.quiet_requires_sources", 1, errors)

    burst_scope = raw.get("burst_scope", "signature")
    if burst_scope not in ("signature", "class"):
        errors.append(
            f"flash_alert.yaml: classes.{name}.burst_scope must be "
            f"'signature' or 'class', got {burst_scope!r}"
        )
        burst_scope = "signature"

    collapse = raw.get("collapse_window_minutes")
    if collapse is not None:
        collapse = check_int(collapse, f"classes.{name}.collapse_window_minutes",
                             1, errors)

    ring_reqs_raw = raw.get("ring_requirements")
    ring_requirements: dict[str, tuple[str, ...]] = {}
    if ring_reqs_raw is not None:
        if not isinstance(ring_reqs_raw, dict):
            errors.append(f"flash_alert.yaml: classes.{name}.ring_requirements "
                          "must be a mapping")
        else:
            for bucket, rings in ring_reqs_raw.items():
                if bucket not in terms:
                    errors.append(
                        f"flash_alert.yaml: classes.{name}.ring_requirements."
                        f"{bucket}: unknown term bucket"
                    )
                    continue
                ring_list = check_str_list(rings,
                                           f"classes.{name}.ring_requirements.{bucket}",
                                           errors)
                bad = [r for r in ring_list
                       if r not in _RINGS and r not in locations]
                for r in bad:
                    errors.append(
                        f"flash_alert.yaml: classes.{name}.ring_requirements."
                        f"{bucket}: unknown ring {r!r}"
                    )
                if ring_list:
                    ring_requirements[bucket] = ring_list

    if not terms:
        errors.append(f"flash_alert.yaml: classes.{name} has no term buckets")
    if not locations:
        errors.append(f"flash_alert.yaml: classes.{name} has no location rings")

    return AlertClass(
        name=name, label=label, terms=terms, locations=locations,
        quiet_hours=quiet_hours, quiet_requires_sources=quiet_requires,
        burst_scope=burst_scope, collapse_window_minutes=collapse,
        ring_requirements=ring_requirements,
    )
