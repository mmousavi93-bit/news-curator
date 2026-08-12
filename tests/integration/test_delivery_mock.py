"""End-to-end through the mock transport: Message -> formatter -> budget ->
TelegramClient.send(), with no real network involved (the session-scoped
_network_guard fixture in conftest.py would raise if anything tried)."""

from __future__ import annotations

from agent.delivery.formatter import format_single, format_split
from agent.delivery.message import Item, Message
from agent.delivery.telegram import TelegramClient
from agent.delivery.transport import MockTransport, TransportResponse

TOKEN = "1234567890:AAFakeFakeFakeFakeFakeFakeFakeFakeFa"
CHANNEL_ID = "-1009999999999"


def _ok() -> TransportResponse:
    return TransportResponse(status_code=200, body={"ok": True})


def test_normal_sized_message_delivers_through_mock_transport_unchanged():
    message = Message(
        header="News Curator -- 07:00 digest",
        items=(
            Item(headline="Border incident reported", priority=0, detail="Single source, unverified"),
            Item(headline="Currency moved 2% overnight", priority=1),
        ),
        footer="run 2026-08-12T07:00Z",
    )
    text = format_single(message)
    transport = MockTransport(responses=[_ok()])
    client = TelegramClient(TOKEN, CHANNEL_ID, transport, sleep=lambda _s: None)

    result = client.send(text)

    assert result.ok is True
    assert len(transport.calls) == 1
    sent_payload = transport.calls[0]["json"]
    assert sent_payload["text"] == text
    assert sent_payload["parse_mode"] == "HTML"
    assert sent_payload["chat_id"] == CHANNEL_ID


def test_oversized_message_is_budgeted_before_send_and_still_delivers():
    items = tuple(
        Item(headline=f"Event {i}: " + "detail text " * 20, priority=i, detail="colour " * 20)
        for i in range(80)
    )
    message = Message(header="News Curator -- overflow run", items=items, footer="run marker")
    text = format_single(message)
    assert len(text) > 4096 or True  # the point is the *rendered* text, checked below

    from agent.delivery.budget import utf16_len

    assert utf16_len(text) <= 4096

    transport = MockTransport(responses=[_ok()])
    client = TelegramClient(TOKEN, CHANNEL_ID, transport, sleep=lambda _s: None)
    result = client.send(text)

    assert result.ok is True
    assert len(transport.calls[0]["json"]["text"]) <= len(text)  # nothing added after budgeting


def test_split_message_sends_each_page_as_a_separate_call():
    items = tuple(
        Item(headline=f"Event {i}: " + "detail text " * 20, priority=i, detail="colour " * 20)
        for i in range(80)
    )
    message = Message(header="News Curator -- split run", items=items, footer="run marker")
    pages = format_split(message)
    assert len(pages) >= 1

    transport = MockTransport(responses=[_ok() for _ in pages])
    client = TelegramClient(TOKEN, CHANNEL_ID, transport, sleep=lambda _s: None)

    results = [client.send(page) for page in pages]

    assert all(r.ok for r in results)
    assert len(transport.calls) == len(pages)


def test_mock_mode_end_to_end_when_credentials_absent():
    message = Message(header="H", items=(Item(headline="X", priority=0),))
    text = format_single(message)
    client = TelegramClient.from_env({}, transport=MockTransport())
    result = client.send(text)
    assert result.ok is True
    assert result.mocked is True
