"""HTTP client for Telegram's sendMessage endpoint: rate limiting, retries,
and an injectable transport so the whole delivery path is testable offline
(mock mode is mandatory). The retry logic below only knows about
transport.TransportError -- it never imports `requests` itself, so mock
mode and the test suite never need the package installed (RequestsTransport
is the one place that does, and only when a real send is constructed). No
python-telegram-bot, no async framework, no Telethon/MTProto (constraint 6).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from agent.delivery.credentials import register_credentials, validate_channel_id
from agent.delivery.transport import RequestsTransport, Transport, TransportError
from agent.util.logging import get_logger

logger = get_logger(__name__)

API_BASE = "https://api.telegram.org"
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 15.0
MAX_ATTEMPTS = 4
MIN_SEND_INTERVAL_SECONDS = 1.0  # Telegram allows 1 msg/sec per chat
MAX_RETRY_AFTER_SECONDS = 60.0  # cap a server-supplied retry_after so a hostile
# or buggy response can't hang an unattended run for hours


@dataclass(frozen=True, slots=True)
class SendResult:
    ok: bool
    status_code: int | None
    description: str | None
    attempts: int
    mocked: bool = False


class _RateLimiter:
    """Client-side minimum interval between sends -- Telegram allows 1
    message/second per chat and the server is not trusted to enforce it."""

    def __init__(
        self, min_interval: float, sleep: Callable[[float], None], now: Callable[[], float]
    ) -> None:
        self._min_interval = min_interval
        self._sleep = sleep
        self._now = now
        self._last: float | None = None

    def wait(self) -> None:
        current = self._now()
        if self._last is not None:
            elapsed = current - self._last
            if elapsed < self._min_interval:
                self._sleep(self._min_interval - elapsed)
        self._last = self._now()


def _retry_after(body: Mapping[str, object]) -> float | None:
    """Extract Telegram's authoritative retry_after from a 429 body, clamped
    to [0, MAX_RETRY_AFTER_SECONDS] so a negative value never reaches
    time.sleep() (which raises ValueError) and a huge one never stalls an
    unattended run for hours. Returns None -- "use the normal exponential
    backoff instead" -- when the field is absent or not a number, rather
    than crashing or inventing a value."""
    parameters = body.get("parameters")
    if isinstance(parameters, Mapping):
        value = parameters.get("retry_after")
        if isinstance(value, (int, float)):
            return max(0.0, min(float(value), MAX_RETRY_AFTER_SECONDS))
    return None


class TelegramClient:
    """Sends one message at a time to one Telegram chat/channel via
    sendMessage. Absent credentials put the client in mock mode
    automatically -- no network call is ever attempted in that state, and
    `transport` may be None in that state since it is then never touched."""

    def __init__(
        self,
        token: str | None,
        channel_id: str | None,
        transport: Transport | None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        random_fn: Callable[[], float] = random.random,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._token = token
        self._channel_id = channel_id
        self._transport = transport
        self._sleep = sleep
        self._random_fn = random_fn
        self._max_attempts = max_attempts
        self._rate_limiter = _RateLimiter(MIN_SEND_INTERVAL_SECONDS, sleep, now)

    @classmethod
    def from_env(cls, env: Mapping[str, str], transport: Transport | None = None) -> "TelegramClient":
        """Read both credentials from `env`, register them with the
        redaction filter before doing anything else with them, validate the
        channel id's shape if present, and build a client. Absent env vars
        never raise -- that is mock mode, not a config error. Only builds
        the real (`requests`-backed) transport when both credentials are
        actually present -- constructing it unconditionally would make
        mock mode, --send-test, and the test suite require `requests` to be
        installed even though mock mode never sends a byte over the network."""
        token = env.get("TELEGRAM_BOT_TOKEN") or None
        channel_id = env.get("TELEGRAM_CHANNEL_ID") or None
        register_credentials(token, channel_id)
        if channel_id:
            validate_channel_id(channel_id)
        if transport is None and token and channel_id:
            transport = RequestsTransport()
        return cls(token, channel_id, transport)

    @property
    def mock_mode(self) -> bool:
        return not (self._token and self._channel_id)

    def _backoff_delay(self, attempt: int) -> float:
        return (2 ** (attempt - 1)) + self._random_fn()

    def send(self, text: str, *, parse_mode: str = "HTML") -> SendResult:
        if self.mock_mode:
            logger.info("mock mode: would send %d-char message, no network call made", len(text))
            return SendResult(ok=True, status_code=None, description=None, attempts=0, mocked=True)

        url = f"{API_BASE}/bot{self._token}/sendMessage"
        payload = {"chat_id": self._channel_id, "text": text, "parse_mode": parse_mode}
        status_code: int | None = None
        description: str | None = None

        for attempt in range(1, self._max_attempts + 1):
            self._rate_limiter.wait()
            try:
                response = self._transport.post(
                    url, json=payload, timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
                )
            except TransportError as exc:
                logger.warning(
                    "telegram send attempt %d/%d: network error: %s", attempt, self._max_attempts, exc
                )
                if attempt == self._max_attempts:
                    return SendResult(ok=False, status_code=None, description="network error", attempts=attempt)
                self._sleep(self._backoff_delay(attempt))
                continue

            # A response body is only trusted to be a mapping after this
            # check -- valid JSON that is a list, null, string or number is
            # otherwise a crash waiting to happen on the next .get() call.
            body = response.body if isinstance(response.body, Mapping) else {}

            if response.status_code == 200 and body.get("ok"):
                return SendResult(ok=True, status_code=200, description=None, attempts=attempt)

            status_code = response.status_code
            description = str(body.get("description") or "")

            if status_code == 429:
                retry_after = _retry_after(body)
                if retry_after is None:
                    retry_after = self._backoff_delay(attempt)
                logger.warning(
                    "telegram send attempt %d/%d: rate limited, retry_after=%ss",
                    attempt, self._max_attempts, retry_after,
                )
                if attempt == self._max_attempts:
                    break
                self._sleep(retry_after)
                continue

            if 500 <= status_code < 600:
                logger.warning(
                    "telegram send attempt %d/%d: server error %d", attempt, self._max_attempts, status_code
                )
                if attempt == self._max_attempts:
                    break
                self._sleep(self._backoff_delay(attempt))
                continue

            # Any other 4xx is permanent -- retrying wastes the run and looks like abuse.
            logger.error("telegram send failed permanently: status=%d description=%s", status_code, description)
            return SendResult(ok=False, status_code=status_code, description=description, attempts=attempt)

        logger.error(
            "telegram send failed after %d attempts: status=%s description=%s",
            self._max_attempts, status_code, description,
        )
        return SendResult(ok=False, status_code=status_code, description=description, attempts=self._max_attempts)
