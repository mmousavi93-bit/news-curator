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

from agent.llm.breaker import CircuitBreaker, backoff_delay
from agent.llm.call import _OK, _SAME, Provider, attempt
from agent.llm.errors import FATAL, REFUSED_CAP, UNAVAILABLE, LlmResult
from agent.llm.limits import CallBudget, ProviderBudget, RpmPacer
from agent.llm.providers import DEFAULT_TIMEOUT, ImageInput, ProviderAdapter
from agent.llm.transport import HttpTransport, RequestsHttpTransport
from agent.util.logging import get_logger


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
        rpm_map = rpm_by_provider or {}
        limits = provider_limits or {}
        timeout_map = timeout_by_provider or {}
        self._providers = [
            Provider(
                p.name, p, rpm_map.get(p.name), limits.get(p.name),
                timeout=timeout_map.get(p.name, DEFAULT_TIMEOUT),
            ) for p in providers
        ]

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
        whose slot this call consumes (compose) instead of spending fresh."""
        return self._run(prompt, [], stage=stage, use_reservation=use_reservation)

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
        return self._run(prompt, images, stage=stage, use_reservation=use_reservation)

    # -- internals ----------------------------------------------------------

    def _acquire_slot(self, stage: str, use_reservation: str | None) -> bool:
        if use_reservation is not None:
            if self._budget.consume_reserved(use_reservation):
                return True
            self._logger.error(
                "llm budget: reservation %s exhausted -- call refused", use_reservation
            )
            return False
        return self._budget.acquire(stage)

    def _run(
        self,
        prompt: str,
        images: Sequence[ImageInput],
        *,
        stage: str,
        use_reservation: str | None,
    ) -> LlmResult:
        candidates = [
            p for p in self._providers if not images or p.adapter.supports_vision
        ]
        if not candidates:
            self._logger.error("llm: no provider available for stage=%s", stage)
            return LlmResult(ok=False, status=UNAVAILABLE)

        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        queue: deque[Provider] = deque(candidates)
        attempts = 0

        while queue and attempts < self._max_retries + 1:
            provider = queue.popleft()
            name = provider.name

            if self._breaker.is_open(name):
                if name not in self._skip_logged:
                    self._skip_logged.add(name)
                    self._logger.error("llm: skipping %s (circuit breaker open)", name)
                continue
            if provider.spend is not None and not provider.spend.acquire():
                continue  # the guard logged it

            if not self._acquire_slot(stage, use_reservation):
                return LlmResult(ok=False, status=REFUSED_CAP)

            self._pacer.wait(name, provider.rpm)
            self._call_index += 1
            outcome, result = attempt(
                provider=provider,
                prompt=prompt,
                images=images,
                prompt_hash=prompt_hash,
                stage=stage,
                transport=self._transport,
                breaker=self._breaker,
                clock=self._clock,
                call_index=self._call_index,
                logger=self._logger,
            )
            attempts += 1

            if outcome == _OK:
                self._breaker.success(name)
                if provider.spend is not None:
                    provider.spend.record_usage(
                        result.usage.get("in", 0), result.usage.get("out", 0)
                    )
                return result
            if outcome == FATAL:
                return result

            if outcome == _SAME and not provider.schema_retried:
                provider.schema_retried = True
                queue.appendleft(provider)  # retry once on the same provider
                continue
            provider.schema_retried = False
            queue.append(provider)  # rotate: this provider goes to the back
            self._sleep(backoff_delay(attempts, self._base_delay))

        if stage not in self._unavailable_logged:
            self._unavailable_logged.add(stage)
            self._logger.error(
                "llm: stage=%s unavailable after %d attempt(s)", stage, attempts
            )
        return LlmResult(ok=False, status=UNAVAILABLE)
