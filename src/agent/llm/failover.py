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
    consecutive_429: dict[str, int] = {}
    # The last provider-fatal result, kept so an all-fatal call still
    # surfaces the diagnostic status instead of a generic "unavailable".
    fatal_result: LlmResult | None = None

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
            # 400/401/403 is a PROVIDER fact, not a request fact. The old
            # doctrine ("it fails identically everywhere, so stop") was
            # falsified on 2026-09-05: bai_deepseek 400'd and openrouter
            # 403'd on prompts groq and gemini answered seconds later, and
            # the early return destroyed three clusters -- one of them the
            # run's most-corroborated story (7 members, 5 sources: US
            # strikes on Iranian oil tankers) -- while three healthy
            # providers sat in the queue. A 400 means "this gateway
            # rejected this model id or payload shape"; a 403 means "this
            # account's policy says no". Both are per-provider, exactly
            # like the 404 case already rotated for the same reason.
            #
            # Cost is bounded WITHOUT the early return: call.py counted the
            # failure against the breaker, so after two fatals the provider
            # is skipped for the rest of the run -- ~2 wasted calls per
            # provider per RUN, not one per cluster. Do not re-queue: a
            # malformed request will not become well-formed on a retry.
            fatal_result = result
            continue

        if outcome == _SAME and not provider.schema_retried:
            provider.schema_retried = True
            queue.appendleft(provider)  # retry once on the same provider
            continue
        provider.schema_retried = False
        if getattr(result, "http_status", None) == 429:
            # 429 = "slow down, I'll be back". Cool the provider, then
            # retry it AFTER one cooldown ONLY if no ready alternative is
            # waiting -- a healthy downstream provider must not be starved
            # by a walled mid-cascade one (2026-09-05 review finding: the
            # always-retry form burned the whole attempt budget on Groq's
            # wall while a healthy bai sat idle). Bounded by
            # max_429_retries, then rotate.
            now = router._clock()  # fresh: pacer wait + attempt have elapsed
            router._cooldowns.cool(name, now)
            consecutive_429[name] = consecutive_429.get(name, 0) + 1
            ready_alt = any(
                q.name != name
                and not router._breaker.is_open(q.name)
                and not router._cooldowns.is_cooling(q.name, now)
                for q in queue
            )
            if not ready_alt and consecutive_429[name] <= router._max_429_retries:
                router._logger.info(
                    "llm: provider %s 429 -- waiting %ds then retrying same "
                    "provider (token wall, not sickness)",
                    name, router._cooldown_seconds,
                )
                queue.appendleft(provider)
                router._sleep(router._cooldown_seconds)
                continue
            consecutive_429[name] = 0
        else:
            consecutive_429[name] = 0
        queue.append(provider)  # rotate: this provider goes to the back
        router._sleep(backoff_delay(attempts, router._base_delay))

    if stage not in router._unavailable_logged:
        router._unavailable_logged.add(stage)
        router._logger.error(
            "llm: stage=%s unavailable after %d attempt(s)", stage, attempts
        )
    if fatal_result is not None:
        # Every candidate was fatal: keep the diagnostic status so the
        # caller's fate column says WHY, not just "unavailable".
        return fatal_result
    return LlmResult(ok=False, status=UNAVAILABLE)
