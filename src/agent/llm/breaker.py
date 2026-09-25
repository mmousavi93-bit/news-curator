"""Circuit breaker and backoff arithmetic.

Split out of limits.py to keep every file under the ~200-line cap
(CLAUDE.md constraint #12): limits.py is budget/pacing, this is
failure-state. Both are per-run state owned by the router.
"""

from __future__ import annotations

import logging

# Phase 2 precedent: GitHub kills a job at 6 hours and an unbounded backoff
# finds that limit unattended. Every retry delay is capped at this.
BACKOFF_CAP_SECONDS = 60.0


def backoff_delay(attempt: int, base_seconds: float) -> float:
    """Exponential: base * 2^(attempt-1), capped at BACKOFF_CAP_SECONDS.
    Pure arithmetic -- no clock involved, so tests assert exact values."""
    delay = base_seconds * (2 ** max(0, attempt - 1))
    return min(delay, BACKOFF_CAP_SECONDS)


class CircuitBreaker:
    """Per-provider, per-run (PHASE_5_BRIEF §6). After `threshold`
    consecutive failures the provider is skipped for the rest of the run --
    the run degrades, it does not crash. No reset within a run: the next run
    starts with a fresh router.

    Threshold = 2 (settings.yaml backoff.circuit_breaker_failures,
    reconciled 2026-09-18): two consecutive provider-fatal failures (schema
    garbage, 400/403/5xx) open the breaker and skip the provider for the
    rest of the run. 429 and 503 deliberately never count (call.py) --
    transient saturation is "slow down", not "broken", so it cannot retire
    a healthy provider (2026-09-18 run 35335314225: gemini 503s had opened
    the breaker under threshold 2, locking out a provider that recovered in
    ~27 min). No reset within a run: the next run starts fresh.
    """

    def __init__(self, threshold: int, logger: logging.Logger) -> None:
        self.threshold = threshold
        self._failures: dict[str, int] = {}
        self._open: set[str] = set()
        self._logged: set[str] = set()
        self._logger = logger

    def is_open(self, name: str) -> bool:
        return name in self._open

    def failure(self, name: str) -> None:
        self._failures[name] = self._failures.get(name, 0) + 1
        if self._failures[name] >= self.threshold and name not in self._open:
            self._open.add(name)
            if name not in self._logged:
                self._logged.add(name)
                self._logger.error(
                    "llm breaker: provider %s open after %d consecutive failures "
                    "-- skipped for the rest of this run",
                    name, self.threshold,
                )

    def success(self, name: str) -> None:
        self._failures[name] = 0


class CooldownRegister:
    """Transient-wall state (2026-08-31): a per-minute token wall is a
    waiting room, not sickness -- 16 budget calls were burned retrying
    into it in one run. A walled provider rests for `seconds`; others
    serve meanwhile. When EVERY remaining provider is cooling, the
    router proceeds anyway (spinning forever is worse than the wall).

    `cool(..., seconds=)` lets a caller override the default rest per
    event -- a 503 saturation (gemini free tier) rests far longer than a
    429 token wall (see failover._SATURATION_COOLDOWN_SECONDS)."""

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self._until: dict[str, float] = {}

    def cool(self, name: str, now: float, seconds: float | None = None) -> None:
        self._until[name] = now + (self._seconds if seconds is None else seconds)

    def is_cooling(self, name: str, now: float) -> bool:
        return now < self._until.get(name, 0.0)

    def any_ready(self, names, now: float) -> bool:
        return any(now >= self._until.get(n, 0.0) for n in names)
