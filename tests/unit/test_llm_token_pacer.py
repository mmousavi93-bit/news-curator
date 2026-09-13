"""Unit tests for llm/token_pacer.py (session 9s): the sliding 60-second
token window that binds on groq's TPM 8,000. Everything clock-injected --
no test sleeps and no test reads the wall clock (PHASE_5_BRIEF §5)."""

from __future__ import annotations

from agent.llm.token_pacer import TokenPacer, estimate_tokens


class FakeClock:
    """A clock the tests own. `sleep` advances it, which is what a real
    sleep does to a real monotonic clock."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def error(self, msg, *args):
        self.messages.append(("error", msg % args if args else msg))

    def warning(self, msg, *args):
        self.messages.append(("warning", msg % args if args else msg))

    def info(self, msg, *args):
        self.messages.append(("info", msg % args if args else msg))


def _pacer():
    clock = FakeClock()
    return TokenPacer(clock, clock.sleep, RecordingLogger()), clock


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_is_never_zero_and_counts_output():
    assert estimate_tokens("") == 400  # 0 chars -> 0/1.6 + 400 output
    assert estimate_tokens("x" * 1600) == 1400  # 1600/1.6 + 400


def test_estimate_tokens_scales_with_prompt_length():
    small = estimate_tokens("x" * 160)
    large = estimate_tokens("x" * 1600)
    assert large > small


# ---------------------------------------------------------------------------
# window accounting
# ---------------------------------------------------------------------------


def test_first_wait_never_sleeps():
    pacer, clock = _pacer()
    pacer.wait("groq", 8000, 5800)
    assert clock.sleeps == []


def test_second_wait_sleeps_when_window_full():
    pacer, clock = _pacer()
    pacer.wait("groq", 8000, 5800)  # t=0, window=5800
    clock.t = 5.0
    pacer.wait("groq", 8000, 5800)  # 5800+5800 > 8000 -> sleep 55s
    assert clock.sleeps == [55.0]


def test_window_expires_after_sixty_seconds():
    pacer, clock = _pacer()
    pacer.wait("groq", 8000, 5800)
    clock.t = 61.0
    pacer.wait("groq", 8000, 5800)  # first booking aged out
    assert clock.sleeps == []


def test_failed_attempts_consume_the_window():
    # The 2026-09-08 evidence: rejected requests consume the token window,
    # so pacing must book EVERY attempt before it leaves -- a failure
    # cannot later be refunded and is not skipped.
    pacer, clock = _pacer()
    pacer.wait("groq", 8000, 5800)   # attempt 1 (will fail)
    clock.t = 2.0
    pacer.wait("groq", 8000, 5800)   # attempt 2: window has both bookings
    assert clock.sleeps == [58.0]    # must wait for attempt 1 to age out


def test_charge_correction_counts_toward_the_window():
    pacer, clock = _pacer()
    pacer.wait("groq", 8000, 6000)   # booked estimate
    clock.t = 3.0
    pacer.charge("groq", 2500)       # actual usage was higher (correction)
    pacer.wait("groq", 8000, 100)    # 6000+2500+100 > 8000 -> sleeps
    assert clock.sleeps == [57.0]    # until the first booking expires


def test_charge_nonpositive_is_a_noop():
    pacer, clock = _pacer()
    pacer.wait("groq", 8000, 6000)
    pacer.charge("groq", 0)
    pacer.charge("groq", -500)
    clock.t = 61.0
    pacer.wait("groq", 8000, 6000)   # nothing extra in the window
    assert clock.sleeps == []


def test_estimate_over_tpm_logs_once_and_proceeds():
    pacer, clock = _pacer()
    logger = RecordingLogger()
    pacer._logger = logger
    pacer.wait("groq", 8000, 9000)  # can never fit: send anyway, log once
    pacer.wait("groq", 8000, 9000)
    assert clock.sleeps == []
    errors = [m for m in logger.messages if "exceeds" in m[1]]
    assert len(errors) == 1


def test_no_tpm_means_no_pacing_and_no_booking():
    pacer, clock = _pacer()
    pacer.wait("bai", None, 5800)
    pacer.wait("bai", None, 5800)
    assert clock.sleeps == []


def test_windows_are_per_provider():
    pacer, clock = _pacer()
    pacer.wait("groq", 8000, 5800)
    clock.t = 1.0
    pacer.wait("gemini", 8000, 5800)  # gemini's own window: empty -> no sleep
    assert clock.sleeps == []
