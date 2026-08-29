"""Strict nested validation for the `llm:` block of config/settings.yaml
(PHASE_5_BRIEF §10).

Split out of settings_schema.py to keep both files under the ~200-line cap
(CLAUDE.md constraint #12). Imported by settings.py (validation),
settings_schema.py (dataclass shapes) and agent.llm.providers (typing).

What the nested validation closes, and why it matters (PHASE_5_BRIEF §10):
before it, `providers` was Mapping[str, Any], so `rpm: "ten"`,
`supports_vision: 1` and `enabled: "no"` all loaded silently. Phase 1's
review found exactly this class twice -- leaf types unchecked, and
`True == 1` passing an integer check. A typo'd threshold that silently
falls back to a default is the worst failure mode this project has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent.leaf_types import _type_matches, _type_name

# ---------------------------------------------------------------------------
# Nested llm shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """One `providers:` entry. Optional fields are None when unset;
    `supports_vision` is required by build_llm (below)."""

    model: str | None = None
    rpm: int | None = None
    rpd: int | None = None
    supports_vision: bool = False
    enabled: bool | None = None
    prompt_cache: bool | None = None
    max_calls_per_run: int | None = None
    max_spend_usd_per_month: float | None = None
    halt_on_budget_exceeded: bool | None = None
    input_usd_per_mtok: float | None = None
    output_usd_per_mtok: float | None = None


@dataclass(frozen=True, slots=True)
class BackoffSettings:
    max_retries: int
    base_delay_seconds: float
    circuit_breaker_failures: int


_PROVIDER_FIELDS: dict[str, type] = {
    "model": str,
    "rpm": int,
    "rpd": int,
    "supports_vision": bool,
    "enabled": bool,
    "prompt_cache": bool,
    "max_calls_per_run": int,
    "max_spend_usd_per_month": float,
    "halt_on_budget_exceeded": bool,
    "input_usd_per_mtok": float,
    "output_usd_per_mtok": float,
}
_PROVIDER_REQUIRED: tuple[str, ...] = ("supports_vision",)

_BACKOFF_FIELDS: dict[str, type] = {
    "max_retries": int,
    "base_delay_seconds": float,
    "circuit_breaker_failures": int,
}


def _typed_subblock(
    path: str,
    raw: Mapping[str, Any] | None,
    fields: Mapping[str, type],
    required: Sequence[str],
    errors: list[str],
) -> dict[str, Any]:
    """Shared strict check for one nested mapping (a provider entry, the
    backoff block): exactly the known keys, each leaf typed, bool rejected
    for numerics, no negatives. Returns the validated kwargs."""
    if not isinstance(raw, dict):
        errors.append(f"{path}: expected a mapping, got {type(raw).__name__}")
        return {}
    kwargs: dict[str, Any] = {}
    keys = set(raw)
    for missing in sorted(set(required) - keys):
        errors.append(f"{path}: missing required key '{missing}'")
    for extra in sorted(keys - set(fields)):
        errors.append(f"{path}: unknown key '{extra}'")
    for key, expected in fields.items():
        if key not in raw:
            continue
        value = raw[key]
        if not _type_matches(value, expected):
            errors.append(
                f"{path}.{key}: expected {_type_name(expected)}, "
                f"got {type(value).__name__} (value={value!r})"
            )
            continue
        if expected in (int, float) and value < 0:
            errors.append(f"{path}.{key}: must not be negative, got {value!r}")
            continue
        kwargs[key] = value
    return kwargs


def build_llm(raw: Any, errors: list[str]) -> dict[str, Any]:
    """Strict nested validation + construction for `settings.llm`. Top-level
    key presence/types are already checked by the generic section check; this
    builds the typed nested shapes and adds the cross-references that the
    generic check cannot express:

      - every provider entry: typed sub-keys, `supports_vision` required;
      - backoff: typed sub-keys;
      - every provider named in `order` or `stages.*.provider` (and
        `cascade_adjudicator`) must have a `providers:` entry -- a typo'd
        name silently selecting no provider would otherwise degrade to
        "unavailable" on the one night the pipeline is needed.

    Returns constructor kwargs for LlmSettings. Never raises; problems are
    collected into `errors` for settings.py to raise all at once.
    """
    path = "settings.llm"
    if not isinstance(raw, dict):
        errors.append(f"{path}: expected a mapping, got {type(raw).__name__}")
        return {}

    providers: dict[str, ProviderSettings] = {}
    providers_raw = raw.get("providers")
    if isinstance(providers_raw, dict):
        for name, entry in providers_raw.items():
            kwargs = _typed_subblock(
                f"{path}.providers.{name}", entry, _PROVIDER_FIELDS,
                _PROVIDER_REQUIRED, errors,
            )
            if "supports_vision" in kwargs:
                providers[name] = ProviderSettings(**kwargs)

    known = set(providers) | set(providers_raw or {})

    order_raw = raw.get("order")
    if isinstance(order_raw, (list, tuple)):
        for name in order_raw:
            if name not in known:
                errors.append(f"{path}.order: provider '{name}' has no providers: entry")

    stages_raw = raw.get("stages")
    if isinstance(stages_raw, dict):
        for stage_name, stage_cfg in stages_raw.items():
            if not isinstance(stage_cfg, dict):
                errors.append(
                    f"{path}.stages.{stage_name}: expected a mapping, "
                    f"got {type(stage_cfg).__name__}"
                )
                continue
            provider_name = stage_cfg.get("provider")
            if not isinstance(provider_name, str):
                errors.append(
                    f"{path}.stages.{stage_name}.provider: expected str, "
                    f"got {type(provider_name).__name__}"
                )
            elif provider_name not in known:
                errors.append(
                    f"{path}.stages.{stage_name}.provider: provider "
                    f"'{provider_name}' has no providers: entry"
                )
            adjudicator = stage_cfg.get("cascade_adjudicator")
            if isinstance(adjudicator, str) and adjudicator not in known:
                errors.append(
                    f"{path}.stages.{stage_name}.cascade_adjudicator: provider "
                    f"'{adjudicator}' has no providers: entry"
                )

    backoff = _typed_subblock(
        f"{path}.backoff", raw.get("backoff"), _BACKOFF_FIELDS, tuple(_BACKOFF_FIELDS),
        errors,
    )
    backoff_obj = BackoffSettings(**backoff) if len(backoff) == len(_BACKOFF_FIELDS) else None

    result: dict[str, Any] = {"providers": providers, "backoff": backoff_obj}
    for key in ("max_calls_per_run", "order", "stages"):
        if key in raw:
            result[key] = raw[key]
    return result
