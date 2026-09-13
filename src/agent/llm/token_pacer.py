"""Token-aware pacing (session 9s): a sliding 60-second window of tokens
per provider, charged on EVERY attempt including failures.

Why this exists -- the measured cause (agents/briefs/SESSION_9S_BRIEF.md,
evidence in outputs/output-log.txt): Groq's free tier for
qwen/qwen3.8-27b is RPM 30 / RPD 1K / **TPM 8K** / TPD 200K, and TPM is
the binder by an order of magnitude. One extraction call is ~3,450
tokens, so ~2.3 calls/min is the sustainable rate -- not the 30 the
RpmPacer was built for. The 2026-09-08 22:56Z run fired 8-9 req/min
(~30K tokens/min into an 8K window); groq answered 25 of 60 calls and
minutes 9-19 were 100% failure with zero recovery. Later-is-better across
the two runs that night killed the daily-ceiling explanation, which
leaves TPM -- and proves REJECTED requests consume the token window. A
pacer that only counts successes (or only counts requests at all)
reproduces the eleven-minute lockout exactly.

RpmPacer stays (RPM is still a real ceiling); this runs alongside it in
failover.py and, on groq, is the one that binds. The clock and the sleep
are injected, as everywhere in this package -- no time.time(), no real
sleeping in tests (PHASE_5_BRIEF §5).

New file rather than an addition to limits.py: that file sits at the
~200-line cap already (constraint 12; the brief flagged the risk itself).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Callable

_WINDOW_SECONDS = 60.0

# Character-based token estimate, deliberately coarse and documented as an
# ESTIMATE (the brief's own words). One measured extraction call is ~3,450
# tokens for a prompt of ~3,000-4,500 chars of mixed English template +
# Persian/Arabic/Hebrew items -- ~1.3-1.5 chars/token in the wild. 1.6
# errs slightly conservative for most batches: an overestimate costs a few
# seconds of sleep, an underestimate costs the eleven-minute 429 lockout.
# The owner-verification rule from the brief applies: if 429s persist at
# 8 calls/run, raise this estimate (or lower batch_size) -- the measured
# numbers land in run.csv's calls_/fails_ columns.
_CHARS_PER_TOKEN = 1.6
# Expected output tokens per extraction call (~400 measured). Added to the
# booked estimate because the window counts input AND output.
_OUTPUT_TOKENS_EST = 400


def estimate_tokens(text: str) -> int:
    """Cheap pre-send token estimate for one request. Chars/1.6 plus the
    expected output; never zero. No tokenizer here -- the sandbox has no
    PyPI, the pipeline must not import sentence-transformers-adjacent
    packages into its hot path, and a per-provider tokenizer table would
    be a dependency this project does not carry."""
    return max(1, int(len(text or "") / _CHARS_PER_TOKEN) + _OUTPUT_TOKENS_EST)


class TokenPacer:
    """Sliding 60s token window per provider. wait() books a request's
    estimate BEFORE the request leaves and sleeps until it fits; charge()
    adds a correction after the attempt when real usage is known. The
    booking happens on every attempt -- including failures, including 429s
    -- because rejected requests consume the window (measured, above)."""

    def __init__(
        self,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        logger: logging.Logger,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._logger = logger
        self._windows: dict[str, deque] = {}
        self._overfit_logged: set[str] = set()

    def _purge(self, name: str, now: float) -> None:
        window = self._windows.get(name)
        if window is None:
            return
        while window and now - window[0][0] >= _WINDOW_SECONDS:
            window.popleft()

    def _window_sum(self, name: str) -> int:
        return sum(tokens for _, tokens in self._windows.get(name, ()))

    def wait(self, name: str, tpm: int | None, estimated_tokens: int) -> None:
        """Book `estimated_tokens` for this provider and sleep (through the
        injected sleep callable) until the trailing 60s window has room.
        Called on EVERY attempt, before the request is sent. `tpm` None
        (or 0) means unconstrained -- no pacing, no booking, matching the
        "treat a missing value as unconstrained" settings contract."""
        if not tpm or tpm <= 0:
            return
        if estimated_tokens > tpm:
            # This single request exceeds the whole window: it can never
            # fit by waiting. Sleeping forever hangs an unattended run,
            # which is worse than sending and letting the provider 429.
            # Log once per provider per run, then proceed.
            if name not in self._overfit_logged:
                self._overfit_logged.add(name)
                self._logger.error(
                    "llm token pacer: %s request estimated at %d tokens "
                    "exceeds its %d-token minute window -- sending anyway",
                    name, estimated_tokens, tpm,
                )
            return
        window = self._windows.setdefault(name, deque())
        now = self._clock()
        self._purge(name, now)
        while window and self._window_sum(name) + estimated_tokens > tpm:
            # Sleep until the oldest booking ages out, then re-check --
            # a fresh clock read each round, because sleep moves the clock.
            oldest_at, _ = window[0]
            remaining = oldest_at + _WINDOW_SECONDS - self._clock()
            if remaining > 0:
                self._sleep(remaining)
            self._purge(name, self._clock())
        window.append((self._clock(), estimated_tokens))

    def charge(self, name: str, tokens: int) -> None:
        """Book a post-attempt CORRECTION (actual usage beyond the
        pre-send estimate, known only from the provider's usage report on
        success). Non-positive values are a no-op -- overestimates are
        deliberately never refunded, so the window errs conservative."""
        if tokens <= 0:
            return
        window = self._windows.setdefault(name, deque())
        window.append((self._clock(), tokens))
