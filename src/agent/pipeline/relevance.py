"""Iran-relevance GATE for digest ranking (owner decision 2026-08-30, v2).

Deterministic, zero LLM calls: substring-matches config/relevance.yaml
keyword tiers against the event's headline + summary + entities and returns
the highest matching tier's weight. The owner's design (refined mid-session):
relevance is a FILTER -- an event below `min_relevance` never reaches the
digest -- and importance (category, corroboration, tier, recency) is the
SORT within the surviving set. Detection is not relevance: a commentary
piece mentioning Iran still matches iran_direct; the understand prompt's
commentary rule is the first defense, this gate is the second.

Shape-checked strictly (same house rule as topics.yaml): a typo'd tier
name or a non-string keyword must fail the run at config load, never
silently drop a tier to zero -- a silent zero is an order change with no
error anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from agent.config import ConfigError

_VALID_TIERS = ("iran_direct", "strategic", "economy")


@dataclass(frozen=True, slots=True)
class RelevanceConfig:
    weights: Mapping[str, float]
    keywords: Mapping[str, tuple[str, ...]]
    min_relevance: float


def validate_relevance(raw: object) -> RelevanceConfig:
    """Strict shape: {'weights': {tier: number >= 0}, 'keywords': {tier:
    [non-empty str, ...]}, 'min_relevance': number >= 0}. Every weight tier
    needs a keyword list and vice versa. Collects ALL problems and raises
    ConfigError once."""
    if not isinstance(raw, dict):
        raise ConfigError(f"relevance.yaml: expected a mapping, got {type(raw).__name__}")
    errors: list[str] = []

    min_raw = raw.get("min_relevance", 0)
    if not isinstance(min_raw, (int, float)) or isinstance(min_raw, bool) or min_raw < 0:
        errors.append(
            f"relevance.yaml: 'min_relevance' must be a non-negative number, "
            f"got {min_raw!r}"
        )
        min_raw = 0

    weights_raw = raw.get("weights")
    if not isinstance(weights_raw, dict):
        errors.append("relevance.yaml: 'weights' must be a mapping")
        weights_raw = {}
    keywords_raw = raw.get("keywords")
    if not isinstance(keywords_raw, dict):
        errors.append("relevance.yaml: 'keywords' must be a mapping")
        keywords_raw = {}

    weights: dict[str, float] = {}
    for tier, value in weights_raw.items():
        if not isinstance(tier, str) or tier not in _VALID_TIERS:
            errors.append(f"relevance.yaml: unknown weight tier {tier!r}")
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            errors.append(
                f"relevance.yaml: weights.{tier} must be a non-negative number, "
                f"got {value!r}"
            )
            continue
        weights[tier] = float(value)

    keywords: dict[str, tuple[str, ...]] = {}
    for tier, kws in keywords_raw.items():
        if not isinstance(tier, str) or tier not in _VALID_TIERS:
            errors.append(f"relevance.yaml: unknown keyword tier {tier!r}")
            continue
        if not isinstance(kws, list) or not all(isinstance(k, str) and k.strip() for k in kws):
            errors.append(
                f"relevance.yaml: keywords.{tier} must be a list of non-empty strings"
            )
            continue
        keywords[tier] = tuple(kws)

    for tier in _VALID_TIERS:
        if tier in weights and tier not in keywords:
            errors.append(f"relevance.yaml: tier '{tier}' has a weight but no keywords")
        if tier in keywords and tier not in weights:
            errors.append(f"relevance.yaml: tier '{tier}' has keywords but no weight")

    if errors:
        raise ConfigError("; ".join(errors))
    return RelevanceConfig(
        weights=weights, keywords=keywords, min_relevance=float(min_raw))


def score_relevance(cfg: RelevanceConfig | None, text: str) -> float:
    """The highest matching tier's weight, 0.0 when nothing matches or no
    config is loaded. Case-insensitive substring matching."""
    if cfg is None or not text:
        return 0.0
    lowered = text.casefold()
    best = 0.0
    for tier, weight in cfg.weights.items():
        if any(keyword.casefold() in lowered for keyword in cfg.keywords.get(tier, ())):
            best = max(best, weight)
    return best


def passes_gate(cfg: RelevanceConfig | None, text: str) -> bool:
    """The relevance FILTER: does this event clear min_relevance? No config
    loaded (tests constructing Config directly) means no gate -- everything
    passes, matching the pre-gate behaviour."""
    if cfg is None:
        return True
    return score_relevance(cfg, text) >= cfg.min_relevance
