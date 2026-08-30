"""Leaf dataclasses for each top-level section of config/settings.yaml, plus the
table of (key, dataclass, allowed-fields) that settings.py validates against.

Split out of settings.py to keep that file under the ~200-line limit (CLAUDE.md
constraint #12) -- this module is data shape only, no validation logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent.settings_llm import BackoffSettings, ProviderSettings


@dataclass(frozen=True, slots=True)
class ScheduleSettings:
    timezone: str
    pipeline_cron: str
    digest_cron: str
    canonical_daily_run: str
    lookback_hours: int


@dataclass(frozen=True, slots=True)
class CollectionSettings:
    max_items_per_source: int
    concurrency: int
    per_source_timeout_seconds: int
    respect_robots_txt: bool
    user_agent: str
    degraded_after_empty_runs: int
    date_only_max_age_hours: int


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    max_clusters_per_run: int
    max_vision_calls_per_run: int
    cluster_similarity_threshold: float
    event_match_threshold: float
    vision_min_image_bytes: int
    embed_model: str
    item_body_chars: int


@dataclass(frozen=True, slots=True)
class RetentionSettings:
    url_hashes_days: int
    events_days: int
    embeddings_days: int
    signal_events_days: int
    speaker_statements_days: int
    score_history_days: int
    scheduled_events_days: int


@dataclass(frozen=True, slots=True)
class ScoringSettings:
    tier_multipliers: Mapping[int, float]
    min_independent_sources: int
    tier3_can_corroborate: bool
    stateful_decay_from_state_end: bool
    category_caps: Mapping[str, int]
    convergence_step: float
    convergence_max: float
    deception_multiplier: float
    deception_window_hours: int
    novelty_quiet_days: int
    novelty_decay_factor: float
    novelty_floor: float
    novelty_reset_min_contribution: int
    display_cap: int
    persist_uncapped: bool
    delta_basis: str
    tier_bands: Mapping[int, Sequence[float]]


@dataclass(frozen=True, slots=True)
class AlertingSettings:
    wartime_enter_tact: int
    wartime_enter_consecutive_days: int
    wartime_exit_tact: int
    wartime_exit_consecutive_days: int
    wartime_delta_trigger: int
    new_dimension_silent_days: int
    exit_from_wartime_is_full_alert: bool


@dataclass(frozen=True, slots=True)
class MarketsSettings:
    fred_api_key_env: str
    daily_series: Sequence[Mapping[str, str]]
    intraday_series: Sequence[Mapping[str, str]]
    daily_stale_after_hours: int
    triggers: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class LlmSettings:
    max_calls_per_run: int
    order: Sequence[str]
    stages: Mapping[str, Any]
    providers: Mapping[str, ProviderSettings]
    backoff: BackoffSettings


@dataclass(frozen=True, slots=True)
class DigestRankSettings:
    """Digest ranking -- NOT the risk engine (Phase 11). This scores "what
    should the owner read first" from what the run already knows:
    category, corroboration, source tier, recency, volume. Owner-editable;
    deterministic (constraint 3)."""

    category_weights: Mapping[str, int]
    corroboration_weight: float
    tier_bonus: Mapping[int, float]
    recency_max_bonus: float
    recency_window_hours: float
    size_boost_per_member: float
    size_boost_cap: float
    min_score: float
    max_messages: int
    repeat_window_hours: int


@dataclass(frozen=True, slots=True)
class DeliverySettings:
    telegram_max_chars: int
    char_budget: Mapping[str, int]
    truncate_priority: Sequence[str]
    output_language: str
    source_languages: Sequence[str]


@dataclass(frozen=True, slots=True)
class OpsSettings:
    mock_mode: bool
    dry_run: bool
    halt_on_state_decrypt_failure: bool
    halt_on_db_integrity_failure: bool
    require_signal_coverage_check: bool
    coverage_check_fails_build: bool


# (top-level key, dataclass, its own required/allowed keys)
SECTIONS: tuple[tuple[str, type, tuple[str, ...]], ...] = (
    ("schedule", ScheduleSettings,
     ("timezone", "pipeline_cron", "digest_cron", "canonical_daily_run", "lookback_hours")),
    ("collection", CollectionSettings,
     ("max_items_per_source", "concurrency", "per_source_timeout_seconds",
      "respect_robots_txt", "user_agent", "degraded_after_empty_runs",
      "date_only_max_age_hours")),
    ("pipeline", PipelineSettings,
     ("max_clusters_per_run", "max_vision_calls_per_run", "cluster_similarity_threshold",
      "event_match_threshold", "vision_min_image_bytes", "embed_model",
      "item_body_chars")),
    ("retention", RetentionSettings,
     ("url_hashes_days", "events_days", "embeddings_days", "signal_events_days",
      "speaker_statements_days", "score_history_days", "scheduled_events_days")),
    ("scoring", ScoringSettings,
     ("tier_multipliers", "min_independent_sources", "tier3_can_corroborate",
      "stateful_decay_from_state_end", "category_caps", "convergence_step",
      "convergence_max", "deception_multiplier", "deception_window_hours",
      "novelty_quiet_days", "novelty_decay_factor", "novelty_floor",
      "novelty_reset_min_contribution", "display_cap", "persist_uncapped",
      "delta_basis", "tier_bands")),
    ("alerting", AlertingSettings,
     ("wartime_enter_tact", "wartime_enter_consecutive_days", "wartime_exit_tact",
      "wartime_exit_consecutive_days", "wartime_delta_trigger",
      "new_dimension_silent_days", "exit_from_wartime_is_full_alert")),
    ("markets", MarketsSettings,
     ("fred_api_key_env", "daily_series", "intraday_series", "daily_stale_after_hours",
      "triggers")),
    ("llm", LlmSettings, ("max_calls_per_run", "order", "stages", "providers", "backoff")),
    ("digest_rank", DigestRankSettings,
     ("category_weights", "corroboration_weight", "tier_bonus",
      "recency_max_bonus", "recency_window_hours", "size_boost_per_member",
      "size_boost_cap", "min_score", "max_messages", "repeat_window_hours")),
    ("delivery", DeliverySettings,
     ("telegram_max_chars", "char_budget", "truncate_priority", "output_language",
      "source_languages")),
    ("ops", OpsSettings,
     ("mock_mode", "dry_run", "halt_on_state_decrypt_failure",
      "halt_on_db_integrity_failure", "require_signal_coverage_check",
      "coverage_check_fails_build")),
)

TOP_KEYS: tuple[str, ...] = ("version",) + tuple(name for name, _, _ in SECTIONS)
