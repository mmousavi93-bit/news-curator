"""Per-run budget enforcement for the router chokepoint: the global call
budget, provider-level spend guards and proactive RPM pacing.

Why here and nowhere else (PHASE_5_BRIEF §2): the router is the only
component that sees every LLM call. A budget enforced "per stage" is not
enforced -- the pot is shared, and session-5 decision 1 found that a budget
stated in prose is enforced by nothing.

Circuit breaker + backoff arithmetic live in breaker.py (split for the
~200-line cap, constraint #12).

The clock is injected everywhere. No time.time(), no datetime.now(), no
real sleep inside the logic: a pacer that reads the wall clock cannot be
tested and will be trusted anyway (PHASE_5_BRIEF §5).
"""

from __future__ import annotations

import logging
from typing import Callable


class CallBudget:
    """The one global per-run counter (CLAUDE.md constraint #2).

    acquire() runs BEFORE a request is built. Call N+1 is refused, logged
    once, and the run continues degraded -- it is never attempted and never
    raises into run.py.
    """

    def __init__(self, max_calls: int, logger: logging.Logger) -> None:
        self.max_calls = max_calls
        self._used = 0
        self._reserved: dict[str, int] = {}
        self._refused_labels: set[str] = set()
        self._logger = logger

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return self.max_calls - self._used

    def acquire(self, label: str) -> bool:
        if self.remaining <= 0:
            if label not in self._refused_labels:
                self._refused_labels.add(label)
                self._logger.error(
                    "llm budget: call refused for %s (cap %d reached) -- run degrades",
                    label, self.max_calls,
                )
            return False
        self._used += 1
        return True

    def reserve(self, n: int, label: str) -> bool:
        """Priority-ordered spending: pre-spend `n` slots for a stage that
        must always run (compose). Later calls see the reduced headroom and
        truncate themselves; the reserved stage consumes its slots via
        consume_reserved() so nothing is double-counted."""
        if n <= 0:
            return True
        if self.remaining < n:
            if label not in self._refused_labels:
                self._refused_labels.add(label)
                self._logger.error(
                    "llm budget: reserve %d for %s refused (used %d/%d)",
                    n, label, self._used, self.max_calls,
                )
            return False
        self._reserved[label] = self._reserved.get(label, 0) + n
        self._used += n
        return True

    def consume_reserved(self, label: str) -> bool:
        """Spend one previously reserved slot. Returns False if none left."""
        if self._reserved.get(label, 0) <= 0:
            return False
        self._reserved[label] -= 1
        return True

    def release(self, n: int, label: str) -> None:
        """Return unused reserved slots (e.g. the reserved stage was skipped)."""
        if self._reserved.get(label, 0) >= n:
            self._reserved[label] -= n
            self._used = max(0, self._used - n)


class ProviderBudget:
    """Constraint #15 guard rails, per provider (PHASE_5_BRIEF §10).

    Cross-run accounting is deliberately NOT built (brief §2): the failure
    constraint 15 exists to stop is a RETRY LOOP, which is a within-run
    event, so per-run accounting stops the actual threat. A metered
    provider's max_spend_usd_per_month is enforced as a per-run ceiling at
    the same dollar value -- a run that would burn a month of budget in one
    night halts instead, which is the right failure direction.
    """

    def __init__(
        self,
        *,
        name: str,
        max_calls_per_run: int | None,
        max_spend_usd: float | None,
        halt_on_exceeded: bool,
        input_usd_per_mtok: float,
        output_usd_per_mtok: float,
        logger: logging.Logger,
    ) -> None:
        self.name = name
        self._max_calls = max_calls_per_run
        self._max_spend = max_spend_usd
        self._halt = halt_on_exceeded
        self._in_price = input_usd_per_mtok
        self._out_price = output_usd_per_mtok
        self._logger = logger
        self.calls = 0
        self.spend_usd = 0.0
        self.halted = False
        self._warned = False

    def acquire(self) -> bool:
        if self.halted:
            return False
        if self._max_calls is not None and self.calls >= self._max_calls:
            if not self._warned:
                self._warned = True
                self._logger.error(
                    "llm provider %s: per-run call cap (%d) reached",
                    self.name, self._max_calls,
                )
            return False
        self.calls += 1
        return True

    def record_usage(self, in_tokens: int, out_tokens: int) -> None:
        cost = (
            (in_tokens / 1_000_000) * self._in_price
            + (out_tokens / 1_000_000) * self._out_price
        )
        self.spend_usd += cost
        if self._max_spend is not None and self.spend_usd > self._max_spend:
            if self._halt:
                self.halted = True
                self._logger.error(
                    "llm provider %s: budget halt -- spend $%.4f exceeds $%.2f; "
                    "all further calls to it are refused for the rest of this run",
                    self.name, self.spend_usd, self._max_spend,
                )
            elif not self._warned:
                self._warned = True
                self._logger.warning(
                    "llm provider %s: spend $%.4f exceeds $%.2f (halt flag off, continuing)",
                    self.name, self.spend_usd, self._max_spend,
                )


class RpmPacer:
    """Proactive pacing (PHASE_5_BRIEF §5). RPM binds long before RPD:
    40 Gemini calls at 10 RPM is a four-minute wall-clock floor. A pacer
    that waits for 429s spends the run budget on retries and discovers the
    limit the expensive way.

    Calls are serial and stay serial: parallelism would break the pacer and
    buys nothing against a 10 RPM ceiling.
    """

    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, name: str, rpm: int | None) -> None:
        """Sleep (through the injected sleep callable) until `interval` has
        elapsed since the previous call for this provider."""
        if rpm is None:
            return
        interval = 60.0 / rpm
        elapsed = self._clock() - self._last.get(name, float("-inf"))
        if elapsed < interval:
            self._sleep(interval - elapsed)
        self._last[name] = self._clock()
