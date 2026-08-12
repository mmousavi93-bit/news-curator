"""retry_after edge values, non-dict JSON bodies, and the real redaction
wiring during a failed send. Split out of test_telegram.py to keep that
file under the ~200-line limit (CLAUDE.md constraint #12) -- these are the
regression tests for the CRITICAL/MAJOR defects the independent review
found: a negative or non-numeric `retry_after` reaching `time.sleep()`
uncaught, and `.get()` on a JSON body that isn't a dict.
"""

from __future__ import annotations

import io
import logging

import pytest

from agent.delivery.telegram import MAX_RETRY_AFTER_SECONDS, TelegramClient
from agent.delivery.transport import MockTransport, TransportError, TransportResponse
from agent.util.logging import PROCESS_FILTER, get_logger

FAKE_TOKEN = "1234567890:AAFakeFakeFakeFakeFakeFakeFakeFakeFa"  # not a real token
FAKE_CHANNEL_ID = "-1009999999999"

_ABSENT = object()  # sentinel: no "parameters" key on the 429 body at all


def _ok_response() -> TransportResponse:
    return TransportResponse(status_code=200, body={"ok": True, "result": {"message_id": 1}})


def _error_response(status_code: int, description: str) -> TransportResponse:
    return TransportResponse(status_code=status_code, body={"ok": False, "description": description})


def _counter():
    value = [0.0]

    def _now() -> float:
        value[0] += 1.0
        return value[0]

    return _now


def _client(transport: MockTransport, **kwargs) -> TelegramClient:
    return TelegramClient(
        FAKE_TOKEN, FAKE_CHANNEL_ID, transport,
        sleep=lambda _s: None, now=_counter(), random_fn=lambda: 0.0, **kwargs,
    )


@pytest.mark.parametrize(
    "retry_after_value, expected_sleep",
    [
        (_ABSENT, 1.0),  # no "parameters" field at all -- falls back to normal backoff
        (None, 1.0),  # parameters.retry_after is JSON null -- same fallback
        (-5, 0.0),  # negative clamped up to zero, never reaches time.sleep(-5)
        ("not-a-number", 1.0),  # non-numeric -- falls back to normal backoff
        (999999, MAX_RETRY_AFTER_SECONDS),  # huge value clamped down to the cap
    ],
    ids=["absent", "null", "negative", "non-numeric-string", "larger-than-cap"],
)
def test_429_retry_after_edge_values_never_crash_and_sleep_within_bounds(retry_after_value, expected_sleep):
    body: dict = {"ok": False, "error_code": 429, "description": "Too Many Requests"}
    if retry_after_value is not _ABSENT:
        body["parameters"] = {"retry_after": retry_after_value}
    transport = MockTransport(responses=[TransportResponse(429, body), _ok_response()])
    sleeps: list[float] = []
    client = TelegramClient(
        FAKE_TOKEN, FAKE_CHANNEL_ID, transport,
        sleep=lambda s: sleeps.append(s), now=_counter(), random_fn=lambda: 0.0,
    )
    result = client.send("hello")  # must never raise, regardless of the value's shape
    assert result.ok is True
    assert sleeps == [expected_sleep]
    assert all(0.0 <= s <= MAX_RETRY_AFTER_SECONDS for s in sleeps)


@pytest.mark.parametrize(
    "non_dict_body",
    [[1, 2, 3], "a bare string", 42, None],
    ids=["list", "string", "number", "null"],
)
def test_non_dict_json_body_does_not_raise(non_dict_body):
    transport = MockTransport(responses=[TransportResponse(status_code=200, body=non_dict_body)])
    client = _client(transport)
    result = client.send("hello")  # must not raise AttributeError from body.get(...)
    assert result.ok is False  # "ok" can't be found in a non-dict body -- treated as failure, not a crash


def test_production_logging_wiring_redacts_token_on_a_failed_send():
    """Exercises the REAL PROCESS_FILTER + get_logger wiring from
    agent.util.logging during a failed send -- not a freshly constructed
    local RedactionFilter like test_telegram.py's own redaction test. That
    proves the production plumbing telegram.py actually runs under
    (module-level `logger = get_logger(__name__)`) redacts, which a
    from-scratch filter cannot."""
    logger = get_logger("agent.delivery.telegram")  # the exact call telegram.py makes
    assert PROCESS_FILTER in logger.filters  # sanity: production wiring is in place
    PROCESS_FILTER.register(FAKE_TOKEN)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    original_handlers, original_propagate, original_level = (
        list(logger.handlers), logger.propagate, logger.level,
    )
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        transport = MockTransport(
            responses=[
                TransportError,  # exception path: url (with token) in exc text
                _error_response(400, "Bad Request"),  # non-retried failure path
            ]
        )
        client = _client(transport, max_attempts=2)
        result = client.send("hello")
        assert result.ok is False
    finally:
        logger.handlers = original_handlers
        logger.propagate = original_propagate
        logger.setLevel(original_level)
        PROCESS_FILTER._secrets = [s for s in PROCESS_FILTER._secrets if s != FAKE_TOKEN]
        PROCESS_FILTER._pattern = None  # invalidate the cached regex along with the removal

    output = stream.getvalue()
    assert FAKE_TOKEN not in output
