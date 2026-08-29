"""Unit tests for llm/breaker.py and llm/limits.RpmPacer: backoff
arithmetic, proactive pacing and the circuit breaker. Split out of
test_llm_limits.py for the ~200-line convention. Everything clock-injected:
no test sleeps and no test reads the wall clock (PHASE_5_BRIEF §5).
"""

from __future__ import annotations

from agent.llm.breaker import BACKOFF_CAP_SECONDS, CircuitBreaker, backoff_delay
from agent.llm.limits import RpmPacer


class FakeClock:
    """A clock the tests own. `sleep` advances it, which is what a real
    sleep does to a real monotonic clock -- so pacing arithmetic runs
    against a clock that moves exactly as the pacer believes it does."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class RecordingLogger:
    """Minimal logger double: records (level, message) tuples."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def error(self, msg, *args):
        self.messages.append(("error", msg % args if args else msg))

    def warning(self, msg, *args):
        self.messages.append(("warning", msg % args if args else msg))

    def info(self, msg, *args):
        self.messages.append(("info", msg % args if args else msg))


# ---------------------------------------------------------------------------
# backoff_delay
# ---------------------------------------------------------------------------


def test_backoff_delay_exact_values():
    assert backoff_delay(1, 2.0) == 2.0
    assert backoff_delay(2, 2.0) == 4.0
    assert backoff_delay(3, 2.0) == 8.0
    assert backoff_delay(4, 2.0) == 16.0


def test_backoff_delay_caps_at_hard_clamp():
    # Phase 2 precedent: an unbounded backoff finds the 6-hour job kill
    # unattended. Every delay, however deep, caps at 60s.
    assert backoff_delay(8, 2.0) == BACKOFF_CAP_SECONDS
    assert backoff_delay(20, 5.0) == BACKOFF_CAP_SECONDS


# ---------------------------------------------------------------------------
# RpmPacer
# ---------------------------------------------------------------------------


def test_pacer_first_call_never_sleeps():
    clock = FakeClock()
    pacer = RpmPacer(clock, clock.sleep)
    pacer.wait("gemini", 10)
    assert clock.sleeps == []


def test_pacer_sleeps_until_interval_elapses():
    clock = FakeClock()
    pacer = RpmPacer(clock, clock.sleep)
    pacer.wait("gemini", 10)          # interval = 6.0s at rpm 10
    clock.t = 2.0
    pacer.wait("gemini", 10)
    assert clock.sleeps == [4.0]


def test_pacer_no_eleventh_call_inside_a_60s_window_at_rpm_10():
    # Gate 4. 11 calls at 10 RPM: intervals are 6s, so the 11th call cannot
    # start before 60s have passed since the first. The fake clock advances
    # only through sleeps, so the total sleep for 11 calls must be >= 60s.
    clock = FakeClock()
    pacer = RpmPacer(clock, clock.sleep)
    for _ in range(11):
        pacer.wait("gemini", 10)
    assert len(clock.sleeps) == 10
    assert sum(clock.sleeps) == 60.0
    assert min(clock.sleeps) >= 0.0


def test_pacer_skips_sleep_when_enough_time_passed():
    clock = FakeClock()
    pacer = RpmPacer(clock, clock.sleep)
    pacer.wait("gemini", 10)
    clock.t = 6.5
    pacer.wait("gemini", 10)
    assert clock.sleeps == []


def test_pacer_without_rpm_never_sleeps():
    clock = FakeClock()
    pacer = RpmPacer(clock, clock.sleep)
    pacer.wait("gemini", None)
    pacer.wait("gemini", None)
    assert clock.sleeps == []


def test_pacer_tracks_providers_independently():
    clock = FakeClock()
    pacer = RpmPacer(clock, clock.sleep)
    pacer.wait("gemini", 10)   # t=0; first call never sleeps
    clock.t = 1.0
    pacer.wait("gemini", 10)   # sleeps 5.0 -> t=6
    pacer.wait("groq", 30)     # groq's FIRST call: never sleeps, whatever
    clock.t = 6.5              # the other provider's history is
    pacer.wait("groq", 30)     # groq interval 2.0 -> sleeps 1.5
    assert clock.sleeps == [5.0, 1.5]


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


def test_circuit_breaker_opens_at_threshold_and_logs_once():
    logger = RecordingLogger()
    breaker = CircuitBreaker(2, logger)
    breaker.failure("gemini")
    assert breaker.is_open("gemini") is False
    breaker.failure("gemini")
    assert breaker.is_open("gemini") is True
    breaker.failure("gemini")  # extra failures must not re-log the opening
    opens = [m for m in logger.messages if "breaker" in m[1]]
    assert len(opens) == 1


def test_circuit_breaker_success_resets_consecutive_count():
    logger = RecordingLogger()
    breaker = CircuitBreaker(2, logger)
    breaker.failure("gemini")
    breaker.success("gemini")
    breaker.failure("gemini")
    assert breaker.is_open("gemini") is False
