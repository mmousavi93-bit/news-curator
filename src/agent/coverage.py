"""The startup coverage check (session-5 decision 6).

Two failure shapes this exists to surface, both silent by design otherwise:

  - a signal covered by NO enabled source can never fire, and nobody would
    notice -- the scorer would simply stay quiet on that axis;
  - an enabled source covering NO signal is dead weight for the scorer.

Leads are exempt from the dead-weight rule: they carry weight 0 and exist
to mark topics for verification (session-3 decision 7), not to score.
G1/G2 are supplied by the markets fetcher (Phase 11), not by news sources
-- risk_weights.yaml says so explicitly.

Policy: this check WARNS by default (coverage_check_fails_build: false) and
can be promoted to a hard failure in settings -- same shape as the
credibility join, which is a hard failure because a missing entry corrupts
scores; a coverage gap merely leaves an axis silent.
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from agent.collectors.base import SourceSpec
from agent.config import ConfigError


def validate_weights(raw: object) -> dict[str, dict]:
    """Shape-check risk_weights.yaml: {'signals': {id: {base: ...}}}.
    Fail-loud: a typo'd weights file silently scoring nothing is the worst
    failure mode this project has."""
    if not isinstance(raw, dict) or not isinstance(raw.get("signals"), dict):
        raise ConfigError(
            "risk_weights.yaml: expected a mapping with a 'signals' mapping, "
            f"got {type(raw).__name__}"
        )
    signals: dict[str, dict] = {}
    for sid, spec in raw["signals"].items():
        if not isinstance(sid, str) or not isinstance(spec, dict):
            raise ConfigError(f"risk_weights.yaml: signals.{sid!r} must map to a dict")
        if "base" not in spec:
            raise ConfigError(f"risk_weights.yaml: signals.{sid} missing 'base'")
    signals.update(**{k: dict(v) for k, v in raw["signals"].items()})
    return signals


def check_coverage(
    sources: Sequence[SourceSpec],
    weights_raw: object,
    credibility: Mapping[str, object],
) -> list[str]:
    """Returns warnings; never raises for a gap (policy above). Raises
    ConfigError only for a malformed weights file."""
    signals = validate_weights(weights_raw)
    markets_covered = set(weights_raw.get("covered_by_markets_fetcher") or [])
    known = set(signals)

    def _is_lead(source_id: str) -> bool:
        entry = credibility.get(source_id)
        return bool(entry is not None and getattr(entry, "tier", None) == "lead")

    enabled = [s for s in sources if s.enabled and not _is_lead(s.id)]
    warnings: list[str] = []

    covered: set[str] = set()
    for source in enabled:
        bad = set(source.signals_covered) - known
        if bad:
            warnings.append(
                f"{source.id}: signals_covered names unknown signals {sorted(bad)}"
            )
        covered |= set(source.signals_covered) & known
        if not source.signals_covered:
            warnings.append(
                f"{source.id}: enabled but covers no signal -- dead weight for the scorer"
            )

    for sid in sorted(known - covered - markets_covered):
        warnings.append(
            f"signal {sid}: covered by no enabled source and not by the markets "
            "fetcher -- it can never fire"
        )
    return warnings


def run_startup_check(
    sources: Sequence[SourceSpec],
    weights_raw: object,
    credibility: Mapping[str, object],
    *,
    require_check: bool,
    fail_on_warnings: bool,
    logger: logging.Logger,
) -> None:
    """build_stages calls this: warn (or raise, per settings) and log."""
    if not require_check:
        return
    warnings = check_coverage(sources, weights_raw, credibility)
    for warning in warnings:
        logger.warning("coverage: %s", warning)
    if warnings and fail_on_warnings:
        raise ConfigError(
            "coverage check failed (coverage_check_fails_build: true): "
            + "; ".join(warnings)
        )
