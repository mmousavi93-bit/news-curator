"""Unit tests for delivery/feedback.py: the getUpdates client and its
reaction/DM parsing. Deterministic, no network (MockTransport), no LLM.
Mirrors test_telegram.py's conventions: canned responses in order, calls
recorded, mock mode when the token is absent."""

from __future__ import annotations

import logging

from agent.delivery.feedback import FeedbackClient, parse_updates
from agent.delivery.transport import MockTransport, TransportError, TransportResponse
from agent.util.logging import RedactionFilter

FAKE_TOKEN = "1234567890:***"  # not a real token


def _updates_response(updates: list) -> TransportResponse:
    return TransportResponse(status_code=200, body={"ok": True, "result": updates})


def _reaction(update_id: int, chat_id: int, message_id: int, *emojis: str) -> dict:
    return {
        "update_id": update_id,
        "message_reaction": {
            "chat": {"id": chat_id, "type": "channel", "title": "News Digest"},
            "message_id": message_id,
            "user": {"id": 700, "first_name": "Owner", "last_name": "PM"},
            "date": 1700000000,
            "old_reaction": [],
            "new_reaction": [{"type": "emoji", "emoji": e} for e in emojis],
        },
    }


def _dm(update_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": chat_id, "type": "private", "first_name": "Owner"},
            "from": {"id": 700, "first_name": "Owner"},
            "text": text,
            "date": 1700000001,
        },
    }


def test_mock_mode_when_token_absent_fetches_nothing():
    client = FeedbackClient(None, MockTransport())
    assert client.mock_mode is True
    records, offset = client.fetch()
    assert records == []
    assert offset is None


def test_fetch_parses_reaction_and_dm_and_advances_offset():
    updates = [
        _reaction(10, -100111, 55, "👍"),
        _dm(11, 700, "این خبر خوب بود"),
    ]
    client = FeedbackClient(FAKE_TOKEN, MockTransport(responses=[_updates_response(updates)]))
    records, next_offset = client.fetch()
    assert next_offset == 12  # max update_id + 1
    assert len(records) == 2
    assert records[0].kind == "reaction"
    assert records[0].reactions == "👍"
    assert records[1].kind == "dm"
    assert records[1].text == "این خبر خوب بود"


def test_fetch_passes_offset_when_provided():
    client = FeedbackClient(FAKE_TOKEN, MockTransport(responses=[_updates_response([])]))
    client.fetch(offset=41)
    payload = client._transport.calls[0]["json"]  # type: ignore[attr-defined]
    assert payload["offset"] == 41
    assert payload["timeout"] == 0
    assert payload["allowed_updates"] == ["message_reaction", "message"]


def test_reaction_record_carries_chat_and_user_fields():
    records, _ = parse_updates([_reaction(5, -100222, 77, "👍", "👎")])
    assert len(records) == 1
    record = records[0]
    assert record.chat_id == -100222
    assert record.chat_title == "News Digest"
    assert record.message_id == 77
    assert record.user_name == "Owner PM"
    assert record.reactions == "👍,👎"


def test_reaction_removal_yields_empty_reactions():
    update = _reaction(6, -100111, 55)
    update["message_reaction"]["new_reaction"] = []
    records, _ = parse_updates([update])
    assert records[0].reactions == ""


def test_non_emoji_reaction_types_are_skipped():
    update = _reaction(7, -100111, 55)
    update["message_reaction"]["new_reaction"] = [
        {"type": "custom_emoji", "custom_emoji_id": "123"},
        {"type": "emoji", "emoji": "🔥"},
    ]
    records, _ = parse_updates([update])
    assert records[0].reactions == "🔥"


def test_dm_parsed_and_group_message_ignored():
    group_msg = {
        "update_id": 8,
        "message": {
            "message_id": 8,
            "chat": {"id": -100999, "type": "supergroup"},
            "from": {"id": 1, "first_name": "Rando"},
            "text": "not owner feedback",
        },
    }
    records, _ = parse_updates([_dm(9, 700, "hello"), group_msg])
    assert len(records) == 1
    assert records[0].kind == "dm"
    assert records[0].chat_id == 700


def test_dm_with_caption_falls_back_to_text():
    update = _dm(12, 700, "text")
    del update["message"]["text"]
    update["message"]["caption"] = "از عکس"
    records, _ = parse_updates([update])
    assert records[0].text == "از عکس"


def test_malformed_updates_skipped_but_offset_still_advances():
    # The unsupported/malformed update (no message_reaction/message) must not
    # produce a record, but its update_id must still advance the offset so the
    # loop never re-fetches it forever.
    updates = [
        {"update_id": 20, "edited_message": {}},
        _reaction(21, -100111, 55, "❤️"),
    ]
    records, max_id = parse_updates(updates)
    assert len(records) == 1
    assert max_id == 21


def test_non_ok_response_returns_empty_and_keeps_offset():
    client = FeedbackClient(
        FAKE_TOKEN,
        MockTransport(responses=[
            TransportResponse(status_code=200, body={"ok": False, "description": "conflict"})
        ]),
    )
    records, offset = client.fetch(offset=9)
    assert records == []
    assert offset == 9


def test_network_error_returns_empty_and_keeps_offset():
    client = FeedbackClient(FAKE_TOKEN, MockTransport(responses=[TransportError]))
    records, offset = client.fetch(offset=9)
    assert records == []
    assert offset == 9


def test_result_not_a_list_returns_empty():
    client = FeedbackClient(
        FAKE_TOKEN,
        MockTransport(responses=[TransportResponse(status_code=200, body={"ok": True, "result": {}})]),
    )
    records, offset = client.fetch()
    assert records == []
    assert offset is None


def test_bot_token_never_appears_in_logs_on_failed_fetch():
    logger = logging.getLogger("agent.delivery.feedback")
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    redaction = RedactionFilter()
    redaction.register(FAKE_TOKEN)
    original_handlers = list(logger.handlers)
    original_filters = list(logger.filters)
    original_propagate = logger.propagate
    original_level = logger.level
    logger.handlers = [handler]
    logger.filters = [redaction]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        client = FeedbackClient(FAKE_TOKEN, MockTransport(responses=[TransportError]))
        client.fetch()
    finally:
        logger.handlers = original_handlers
        logger.filters = original_filters
        logger.propagate = original_propagate
        logger.setLevel(original_level)

    assert FAKE_TOKEN not in stream.getvalue()


def test_from_env_no_token_yields_mock_mode():
    client = FeedbackClient.from_env({}, transport=MockTransport())
    assert client.mock_mode is True
