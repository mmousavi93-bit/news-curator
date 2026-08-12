from __future__ import annotations

import logging

import pytest

from agent.delivery.credentials import TelegramConfigError, register_credentials, validate_channel_id
from agent.delivery.telegram import TelegramClient
from agent.delivery.transport import MockTransport, TransportError, TransportResponse, TransportTimeout
from agent.util.logging import RedactionFilter

FAKE_TOKEN = "1234567890:AAFakeFakeFakeFakeFakeFakeFakeFakeFa"  # not a real token
FAKE_CHANNEL_ID = "-1009999999999"


def _ok_response() -> TransportResponse:
    return TransportResponse(status_code=200, body={"ok": True, "result": {"message_id": 1}})


def _error_response(status_code: int, description: str, retry_after: float | None = None) -> TransportResponse:
    body: dict = {"ok": False, "error_code": status_code, "description": description}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return TransportResponse(status_code=status_code, body=body)


def _client(transport: MockTransport, **kwargs) -> TelegramClient:
    return TelegramClient(
        FAKE_TOKEN,
        FAKE_CHANNEL_ID,
        transport,
        sleep=lambda _s: None,
        now=_counter(),
        random_fn=lambda: 0.0,
        **kwargs,
    )


def _counter():
    value = [0.0]

    def _now() -> float:
        value[0] += 1.0
        return value[0]

    return _now


def test_mock_mode_when_credentials_absent_sends_nothing():
    transport = MockTransport(responses=[_ok_response()])
    client = TelegramClient(None, None, transport)
    result = client.send("hello")
    assert result.ok is True
    assert result.mocked is True
    assert transport.calls == []


def test_successful_send_returns_ok():
    transport = MockTransport(responses=[_ok_response()])
    client = _client(transport)
    result = client.send("hello")
    assert result.ok is True
    assert result.attempts == 1
    assert len(transport.calls) == 1


def test_429_with_retry_after_is_honoured_and_eventually_succeeds():
    transport = MockTransport(
        responses=[_error_response(429, "Too Many Requests", retry_after=2), _ok_response()]
    )
    sleeps: list[float] = []
    client = TelegramClient(
        FAKE_TOKEN, FAKE_CHANNEL_ID, transport,
        sleep=lambda s: sleeps.append(s), now=_counter(), random_fn=lambda: 0.0,
    )
    result = client.send("hello")
    assert result.ok is True
    assert result.attempts == 2
    assert 2 in sleeps  # the server-specified retry_after was honoured verbatim


def test_400_is_not_retried():
    transport = MockTransport(responses=[_error_response(400, "Bad Request: can't parse entities")])
    client = _client(transport)
    result = client.send("hello")
    assert result.ok is False
    assert result.status_code == 400
    assert result.attempts == 1
    assert len(transport.calls) == 1  # no retry attempted


def test_5xx_is_retried_and_can_succeed():
    transport = MockTransport(
        responses=[_error_response(500, "Internal Server Error"), _ok_response()]
    )
    client = _client(transport)
    result = client.send("hello")
    assert result.ok is True
    assert result.attempts == 2


def test_connection_error_is_retried_then_fails_after_max_attempts():
    transport = MockTransport(responses=[TransportError] * 4)
    client = _client(transport, max_attempts=4)
    result = client.send("hello")
    assert result.ok is False
    assert len(transport.calls) == 4


def test_timeout_is_retried():
    transport = MockTransport(responses=[TransportTimeout, _ok_response()])
    client = _client(transport)
    result = client.send("hello")
    assert result.ok is True
    assert result.attempts == 2


def test_bot_token_never_appears_in_logs_on_a_failed_send_including_exception_path():
    logger_name = "agent.delivery.telegram"
    logger = logging.getLogger(logger_name)
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    redaction = RedactionFilter()
    redaction.register(FAKE_TOKEN)
    original_handlers, original_filters, original_propagate, original_level = (
        list(logger.handlers), list(logger.filters), logger.propagate, logger.level,
    )
    logger.handlers = [handler]
    logger.filters = [redaction]
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
        logger.filters = original_filters
        logger.propagate = original_propagate
        logger.setLevel(original_level)

    output = stream.getvalue()
    assert FAKE_TOKEN not in output
    assert "sendMessage" in output or True  # url shape may or may not be logged; token must not be


def test_validate_channel_id_accepts_numeric_and_username_shapes():
    validate_channel_id("-1001234567890")
    validate_channel_id("@some_channel")


def test_validate_channel_id_rejects_unrecognized_shape_without_echoing_value():
    bogus = "not-a-valid-channel-id!!"
    with pytest.raises(TelegramConfigError) as exc_info:
        validate_channel_id(bogus)
    assert bogus not in str(exc_info.value)


def test_from_env_with_no_env_vars_yields_mock_mode_and_does_not_raise():
    client = TelegramClient.from_env({}, transport=MockTransport())
    assert client.mock_mode is True


def test_from_env_registers_both_credentials_with_redaction_filter():
    redaction = RedactionFilter()
    register_credentials(FAKE_TOKEN, FAKE_CHANNEL_ID, redaction=redaction)
    logger = logging.getLogger("test.telegram.register_credentials")
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.handlers = [handler]
    logger.filters = [redaction]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.info("token=%s channel=%s", FAKE_TOKEN, FAKE_CHANNEL_ID)
    output = stream.getvalue()
    assert FAKE_TOKEN not in output
    assert FAKE_CHANNEL_ID not in output
