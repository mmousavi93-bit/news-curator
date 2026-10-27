"""Serial RPM pacing and side-effect-free readiness."""

from typing import Callable


class RpmPacer:
    """Proactive pacing (PHASE_5_BRIEF §5). RPM binds long before RPD: 40 Gemini
    calls at 5 RPM is an eight-minute wall-clock floor; a pacer that waits for
    429s spends the run budget on retries and learns the limit expensively.
    Calls are serial and stay serial -- parallelism would break the pacer and
    buys nothing against a 5 RPM ceiling.
    """

    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def delay(self, name: str, rpm: int | None) -> float:
        """Seconds until ready, without booking or sleeping."""
        if rpm is None:
            return 0.0
        return max(0.0, 60.0 / rpm - (self._clock() - self._last.get(name, float("-inf"))))

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
