"""Provider router: the only module callers import.

One complete() and one see(), behind which providers fail over, rate limits
are paced, budgets are enforced, and total provider collapse degrades the
run instead of ending it (ARCHITECTURE.md §8). No caller ever chooses a
provider; no exception from this package reaches a caller (PHASE_5_BRIEF
§6) -- every outcome is an LlmResult.

Policy (PHASE_5_BRIEF §3):
  rotate on 429 / 5xx / timeout / connection failure;
  never rotate on 400 / 401 / 403 -- fatal is fatal, surface it and stop;
  a schema-invalid parse retries once on the same provider, then rotates;
  total attempts bounded by backoff.max_retries + 1;
  every retry delay capped at BACKOFF_CAP_SECONDS (Phase 2 precedent).

The failover LOOP lives here; what one attempt is, and how it is logged,
lives in call.py. Budget/pacing state lives in limits.py, failure-state in
breaker.py.
"""

from __future__ import annotations

import hashlib
import logging
import time as _time
from collections import deque
from typing import Callable, Mapping, Sequence

from agent.llm.breaker import CircuitBreaker, CooldownRegister, backoff_delay
from agent.llm.call import _OK, _SAME, Provider, attempt
from agent.llm.errors import FATAL, REFUSED_CAP, UNAVAILABLE, LlmResult
from agent.llm.failover import failover
from agent.llm.limits import CallBudget, ProviderBudget, RpmPacer
from agent.llm.providers import DEFAULT_TIMEOUT, ImageInput, ProviderAdapter
from agent.llm.stats import ProviderStats
from agent.llm.transport import HttpTransport, RequestsHttpTransport
from agent.util.logging import get_logger

# 429 cooldown seconds (2026-08-31): Groq's free wall is a ~60s token
# budget; 30s of rest lets it refill while other providers serve.
_COOLDOWN_SECONDS = 30.0


class Router:
    """Selection, failover, pacing, breaker, budget. Stateless between runs
    by construction -- the pipeline builds one per run."""

    def __init__(
        self,
        providers: Sequence[ProviderAdapter],
        *,
        transport: HttpTransport | None = None,
        max_calls: int = 51,
        max_retries: int = 3,
        base_delay_seconds: float = 2.0,
        breaker_threshold: int = 5,
        provider_limits: Mapping[str, ProviderBudget] | None = None,
        rpm_by_provider: Mapping[str, int | None] | None = None,
        timeout_by_provider: Mapping[str, tuple[float, float]] | None = None,
        clock: Callable[[], float] = _time.monotonic,
        sleep: Callable[[float], None] = _time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self._logger = logger or get_logger("agent.llm.router")
        self._transport = transport or RequestsHttpTransport()
        self._clock = clock
        self._sleep = sleep
        self._budget = CallBudget(max_calls, self._logger)
        self._pacer = RpmPacer(clock, sleep)
        self._breaker = CircuitBreaker(breaker_threshold, self._logger)
        self._max_retries = max_retries
        self._base_delay = base_delay_seconds
        self._call_index = 0
        # Breaker-skip lines are logged once per provider per RUN, not once
        # per cluster (2026-08-30: 54 identical lines in one run's log).
        self._skip_logged: set[str] = set()
        # The final "stage unavailable" line likewise: once per stage per run
        # (2026-08-30: 14 identical lines while both breakers were open).
        self._unavailable_logged: set[str] = set()
        # 429 cooldown: a token wall is a waiting room, not sickness
        # (16 wasted calls in one run) -- state lives in breaker.py.
        self._cooldowns = CooldownRegister(_COOLDOWN_SECONDS)
        self._cooldown_seconds = _COOLDOWN_SECONDS
        rpm_map = rpm_by_provider or {}
        limits = provider_limits or {}
        timeout_map = timeout_by_provider or {}
        self._providers = [
            Provider(
                p.name, p, rpm_map.get(p.name), limits.get(p.name),
                timeout=timeout_map.get(p.name, DEFAULT_TIMEOUT),
            ) for p in providers
        ]
        # Per-provider attempt counters, rendered into run.csv (report_csv).
        self.stats = ProviderStats(p.name for p in self._providers)

    # -- budget helpers (pipeline: priority-ordered spending) --------------

    def reserve(self, n: int, label: str) -> bool:
        return self._budget.reserve(n, label)

    def release(self, n: int, label: str) -> None:
        self._budget.release(n, label)

    # -- public API --------------------------------------------------------

    def complete(
        self, prompt: str, *, stage: str = "understand",
        use_reservation: str | None = None,
    ) -> LlmResult:
        """Text completion. `use_reservation` names a prior reserve() label
        whose slot this call consumes (compose) instead of spending fresh.
        The loop lives in failover.py (split out 2026-08-31, constraint 12)."""
        return failover(self, prompt, [], stage=stage, use_reservation=use_reservation)

    def see(
        self, prompt: str, images: Sequence[ImageInput], *, stage: str = "vision",
        use_reservation: str | None = None,
    ) -> LlmResult:
        """Vision. Structurally incapable of reaching a text-only provider:
        the candidate list is filtered on supports_vision BEFORE any other
        logic runs (PHASE_5_BRIEF §4). No images, no call -- a text model
        inventing a caption violates constraints #10 and #11 at the source."""
        if not images:
            return LlmResult(ok=False, status=UNAVAILABLE)
        return failover(self, prompt, images, stage=stage, use_reservation=use_reservation)
