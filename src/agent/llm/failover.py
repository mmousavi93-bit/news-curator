"""The failover LOOP: provider selection, rotation, pacing, cooling.

Split out of router.py 2026-08-31 when the router crossed the ~200-line
cap (constraint 12). The router owns the run's provider MACHINE (adapters,
budget, pacer, breaker, cooldowns, stats, skip-logging); this module owns
one iteration of the loop over that machine. What one attempt is lives in
call.py; the machine lives in router.py; the walk lives here.

Contract (PHASE_5_BRIEF §6): no exception reaches the caller -- every
outcome is an LlmResult.
"""

from __future__ import annotations

import hashlib
from collections import deque
from typing import TYPE_CHECKING, Sequence

from agent.llm.breaker import backoff_delay
from agent.llm.call import _OK, _SAME, Provider, attempt
from agent.llm.errors import FATAL, REFUSED_CAP, UNAVAILABLE, LlmResult
from agent.llm.providers import ImageInput

if TYPE_CHECKING:
    from agent.llm.router import Router


def _acquire_slot(router: "Router", stage: str, use_reservation: str | None) -> bool:
    if use_reservation is not None:
        if router._budget.consume_reserved(use_reservation):
            return True
        router._logger.error(
            "llm budget: reservation %s exhausted -- call refused", use_reservation
        )
        return False
    return router._budget.acquire(stage)


def failover(
    router: "Router",
    prompt: str,
    images: Sequence[ImageInput],
    *,
    stage: str,
    use_reservation: str | None,
) -> LlmResult:
    candidates = [
        p for p in router._providers if not images or p.adapter.supports_vision
    ]
    if not candidates:
        router._logger.error("llm: no provider available for stage=%s", stage)
        return LlmResult(ok=False, status=UNAVAILABLE)

    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    queue: deque[Provider] = deque(candidates)
    attempts = 0

    while queue and attempts < router._max_retries + 1:
        provider = queue.popleft()
        name = provider.name

        if router._breaker.is_open(name):
            if name not in router._skip_logged:
                router._skip_logged.add(name)
                router._logger.error("llm: skipping %s (circuit breaker open)", name)
            continue
        now = router._clock()
        if router._cooldowns.is_cooling(name, now):
            # 429'd provider rests; rotate past it while any other
            # provider is ready. All cooling -> proceed anyway.
            if router._cooldowns.any_ready((q.name for q in queue), now):
                queue.append(provider)
                continue
        if provider.spend is not None and not provider.spend.acquire():
            continue  # the guard logged it

        if not _acquire_slot(router, stage, use_reservation):
            return LlmResult(ok=False, status=REFUSED_CAP)

        router._pacer.wait(name, provider.rpm)
        router._call_index += 1
        outcome, result = attempt(
            provider=provider,
            prompt=prompt,
            images=images,
            prompt_hash=prompt_hash,
            stage=stage,
            transport=router._transport,
            breaker=router._breaker,
            clock=router._clock,
            call_index=router._call_index,
            logger=router._logger,
        )
        attempts += 1
        router.stats.record(name, outcome == _OK)

        if outcome == _OK:
            router._breaker.success(name)
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
        if getattr(result, "http_status", None) == 429:
            if not router._cooldowns.is_cooling(name, now):
                router._logger.info(
                    "llm: provider %s 429 -- cooling %ds (token wall, not sickness)",
                    name, router._cooldown_seconds,
                )
            router._cooldowns.cool(name, now)
        queue.append(provider)  # rotate: this provider goes to the back
        router._sleep(backoff_delay(attempts, router._base_delay))

    if stage not in router._unavailable_logged:
        router._unavailable_logged.add(stage)
        router._logger.error(
            "llm: stage=%s unavailable after %d attempt(s)", stage, attempts
        )
    return LlmResult(ok=False, status=UNAVAILABLE)
