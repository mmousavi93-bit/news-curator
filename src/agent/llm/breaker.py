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

    Note (flagged for the Phase 6 wiring decision): with the drafted
    backoff.max_retries=3 the rotation loop makes at most 4 attempts total,
    so no single provider can reach 5 consecutive failures in one run --
    the breaker never opens at the shipped defaults. The mechanism is
    correct and gate-tested; the defaults interact badly and one of the two
    numbers should move when the pipeline is wired.
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
