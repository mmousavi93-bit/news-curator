"""Construction of a production Router from the validated settings block.

Split out of router.py to keep every file under the ~200-line cap
(CLAUDE.md constraint #12): router.py is the failover loop, this is config
wiring -- adapter selection (build_adapters) and the Router build
(build_router). The Router class itself stays fully constructible by hand,
which is what the tests do.
"""

from __future__ import annotations

import logging
import time as _time
from typing import Callable, Mapping, Sequence

from agent.llm.limits import ProviderBudget
from agent.llm.health import cascade_order
from agent.llm.providers import (
    API_KEY_ENV,
    DEFAULT_TIMEOUT,
    BaiAdapter,
    GeminiAdapter,
    GroqAdapter,
    OpenRouterAdapter,
    ProviderAdapter,
)
from agent.llm.router import Router
from agent.llm.transport import HttpTransport
from agent.settings_llm import ProviderSettings
from agent.settings_schema import LlmSettings
from agent.util.logging import get_logger

_ADAPTERS: dict[str, type] = {
    "gemini": GeminiAdapter,
    "groq": GroqAdapter,
    "bai": BaiAdapter,
    "openrouter": OpenRouterAdapter,
    # "anthropic": deliberately not implemented in Phase 5 (brief "Out of
    # scope"); the budget guardrails for it ARE built and tested.
}


def build_adapters(
    order: Sequence[str],
    provider_cfgs: Mapping[str, ProviderSettings],
    env: Mapping[str, str],
    logger: logging.Logger,
) -> list[ProviderAdapter]:
    """Adapters for every provider in `order` that is implemented, enabled,
    configured and has an API key. Each skip is logged once -- a provider
    without a key is "unconfigured", not an error."""
    adapters: list[ProviderAdapter] = []
    skipped: set[str] = set()

    def _skip(name: str, reason: str) -> None:
        if name not in skipped:
            skipped.add(name)
            logger.error("llm provider %s: %s -- skipped this run", name, reason)

    for name in order:
        cls = _ADAPTERS.get(name)
        if cls is None:
            _skip(name, "adapter not implemented")
            continue
        cfg = provider_cfgs.get(name)
        if cfg is None:
            _skip(name, "no providers: entry")
            continue
        if cfg.enabled is False:
            _skip(name, "disabled in settings")
            continue
        if not cfg.model:
            _skip(name, "no model configured")
            continue
        env_name = API_KEY_ENV.get(name, "")
        api_key = env.get(env_name) or ""
        if not api_key:
            _skip(name, f"no {env_name} in environment")
            continue
        adapters.append(cls(cfg.model, api_key))
    return adapters


def build_router(
    settings: LlmSettings,
    env: Mapping[str, str],
    *,
    transport: HttpTransport | None = None,
    clock: Callable[[], float] = _time.monotonic,
    sleep: Callable[[float], None] = _time.sleep,
    logger: logging.Logger | None = None,
    health: Mapping[str, Mapping] | None = None,
) -> Router:
    """Build a production router from the validated `llm:` settings block.

    Provider-level budget guards come from the same block, for ANY provider
    carrying them (PHASE_5_BRIEF §10) -- enabling a metered provider later
    is a config edit, not a new code path written under pressure.

    `health` (llm/health.py, owner-approved move 2, 2026-08-31) reorders
    the cascade: providers measurably sick in the last 7 days start last,
    so priority clusters hit the provider that actually worked yesterday.
    """
    logger = logger or get_logger("agent.llm.router")
    order = cascade_order(settings.order, health or {})
    adapters = build_adapters(order, settings.providers, env, logger)
    limits: dict[str, ProviderBudget] = {}
    rpm_map: dict[str, int | None] = {}
    timeout_map: dict[str, tuple[float, float]] = {}
    for name, cfg in settings.providers.items():
        rpm_map[name] = cfg.rpm
        if cfg.read_timeout_seconds is not None:
            # Connect timeout is shared; only the read leg is overridable
            # (2026-08-30 decision: primary reads time out at 20s).
            timeout_map[name] = (DEFAULT_TIMEOUT[0], float(cfg.read_timeout_seconds))
        if cfg.max_calls_per_run is not None or cfg.max_spend_usd_per_month is not None:
            limits[name] = ProviderBudget(
                name=name,
                max_calls_per_run=cfg.max_calls_per_run,
                max_spend_usd=cfg.max_spend_usd_per_month,
                halt_on_exceeded=bool(cfg.halt_on_budget_exceeded),
                input_usd_per_mtok=cfg.input_usd_per_mtok or 0.0,
                output_usd_per_mtok=cfg.output_usd_per_mtok or 0.0,
                logger=logger,
            )
    return Router(
        adapters,
        transport=transport,
        max_calls=settings.max_calls_per_run,
        max_retries=settings.backoff.max_retries,
        base_delay_seconds=settings.backoff.base_delay_seconds,
        breaker_threshold=settings.backoff.circuit_breaker_failures,
        provider_limits=limits,
        rpm_by_provider=rpm_map,
        timeout_by_provider=timeout_map,
        clock=clock,
        sleep=sleep,
        logger=logger,
    )
