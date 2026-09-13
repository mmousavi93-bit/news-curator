"""Inbound Telegram feedback: getUpdates -> FeedbackRecord list.

telegram.py is one-way (sendMessage only). This adds the receive side so the
owner's feedback becomes data: reactions (message_reaction updates) on the
digest messages the bot posts, and private messages (message updates) sent
directly to the bot. A reaction is a coarse quality signal on the digest
MESSAGE it attaches to (a message carries several stories -- not per-story);
a DM is free-text.

Read-only by design (owner decision 2026-09-13): this module only OBSERVES.
It never changes scoring, clustering, or any pipeline threshold -- the CSV it
feeds is read by hand when tuning.

Uses the same Transport (POST) as telegram.py: the Bot API accepts POST for
every method including getUpdates, so no new transport method is needed.
getUpdates is called with timeout=0 (short poll -- return whatever is pending
now) because this runs once per pipeline on a CI runner, not as a long-running
listener. No Telethon/MTProto (constraint 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent.delivery.credentials import register_credentials
from agent.delivery.telegram import API_BASE, CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS
from agent.delivery.transport import RequestsTransport, Transport, TransportError
from agent.util.logging import get_logger

logger = get_logger(__name__)

# The two update kinds this loop reads. Everything else (edited_message,
# callback_query, channel_post, ...) is deliberately ignored.
_REACTION = "reaction"
_DM = "dm"


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    update_id: int
    kind: str          # "reaction" | "dm"
    chat_id: int       # channel id (reaction) or private chat id (dm)
    chat_title: str    # channel title; "" for a dm
    user_id: int | None
    user_name: str
    message_id: int | None  # the digest message reacted to; None for a dm
    reactions: str     # comma-joined emojis from new_reaction; "" = removed
    text: str          # dm text; "" for a reaction
    date: int          # unix seconds; 0 when absent


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _chat_title(chat: Mapping[str, object]) -> str:
    title = chat.get("title")
    return title if isinstance(title, str) else ""


def _user_fields(user: object) -> tuple[int | None, str]:
    if not isinstance(user, Mapping):
        return None, ""
    user_id = user.get("id")
    name = " ".join(
        str(user.get(key)) for key in ("first_name", "last_name") if user.get(key)
    ).strip()
    return (user_id if isinstance(user_id, int) else None), name


def _emojis(reactions: object) -> str:
    """Emoji reaction types only. custom_emoji/paid are not plain text and are
    skipped rather than half-decoded."""
    if not isinstance(reactions, Sequence) or isinstance(reactions, (str, bytes)):
        return ""
    parts: list[str] = []
    for reaction in reactions:
        if isinstance(reaction, Mapping) and reaction.get("type") == "emoji":
            emoji = reaction.get("emoji")
            if isinstance(emoji, str):
                parts.append(emoji)
    return ",".join(parts)


def _parse_reaction(update: object, update_id: int) -> FeedbackRecord | None:
    if not isinstance(update, Mapping):
        return None
    chat = update.get("chat")
    if not isinstance(chat, Mapping):
        return None
    chat_id = chat.get("id")
    message_id = update.get("message_id")
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return None
    user_id, user_name = _user_fields(update.get("user"))
    return FeedbackRecord(
        update_id=update_id,
        kind=_REACTION,
        chat_id=chat_id,
        chat_title=_chat_title(chat),
        user_id=user_id,
        user_name=user_name,
        message_id=message_id,
        reactions=_emojis(update.get("new_reaction")),
        text="",
        date=_as_int(update.get("date")),
    )


def _parse_message(update: object, update_id: int) -> FeedbackRecord | None:
    if not isinstance(update, Mapping):
        return None
    chat = update.get("chat")
    if not isinstance(chat, Mapping):
        return None
    # Only private DMs to the bot are feedback. Group/channel messages (which
    # arrive as `message` with a different chat.type, or as `channel_post`)
    # are not the owner talking to the bot and are ignored.
    if chat.get("type") != "private":
        return None
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None
    text = update.get("text") or update.get("caption") or ""
    if not isinstance(text, str):
        text = ""
    user_id, user_name = _user_fields(update.get("from"))
    message_id = update.get("message_id")
    return FeedbackRecord(
        update_id=update_id,
        kind=_DM,
        chat_id=chat_id,
        chat_title="",
        user_id=user_id,
        user_name=user_name,
        message_id=message_id if isinstance(message_id, int) else None,
        reactions="",
        text=text,
        date=_as_int(update.get("date")),
    )


def parse_updates(updates: Sequence[object]) -> tuple[list[FeedbackRecord], int | None]:
    """Parse a getUpdates `result` list into records plus the highest update_id
    seen. The max id is computed over RAW updates, not just parsed ones, so an
    update this build cannot parse still advances the offset and is never
    re-fetched forever (a stuck getUpdates loop)."""
    records: list[FeedbackRecord] = []
    max_id: int | None = None
    for update in updates:
        if not isinstance(update, Mapping):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_id = update_id if max_id is None else max(max_id, update_id)
        if "message_reaction" in update:
            record = _parse_reaction(update["message_reaction"], update_id)
        elif "message" in update:
            record = _parse_message(update["message"], update_id)
        else:
            record = None
        if record is not None:
            records.append(record)
    return records, max_id


class FeedbackClient:
    """Short-poll getUpdates once per run. Absent token = mock mode (no
    network), mirroring TelegramClient. Never raises: a feedback fetch that
    fails must not break the digest -- the updates stay pending and are
    re-read next run (Telegram keeps them ~24h)."""

    def __init__(self, token: str | None, transport: Transport | None) -> None:
        self._token = token
        self._transport = transport

    @classmethod
    def from_env(
        cls, env: Mapping[str, str], transport: Transport | None = None
    ) -> "FeedbackClient":
        token = env.get("TELEGRAM_BOT_TOKEN") or None
        register_credentials(token, None)
        if transport is None and token:
            transport = RequestsTransport()
        return cls(token, transport)

    @property
    def mock_mode(self) -> bool:
        return not self._token

    def fetch(self, offset: int | None = None) -> tuple[list[FeedbackRecord], int | None]:
        """Return (records, next_offset). next_offset is the offset to pass on
        the next call: last_seen_update_id + 1, or `offset` unchanged when
        nothing new arrived or the fetch failed."""
        if self.mock_mode:
            return [], offset

        url = f"{API_BASE}/bot{self._token}/getUpdates"
        payload: dict[str, object] = {
            "timeout": 0,
            "allowed_updates": ["message_reaction", "message"],
        }
        if offset is not None:
            payload["offset"] = offset

        try:
            response = self._transport.post(
                url, json=payload, timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
            )
        except TransportError:
            logger.warning("feedback: getUpdates network error; retrying next run")
            return [], offset

        body = response.body if isinstance(response.body, Mapping) else {}
        if response.status_code != 200 or not body.get("ok"):
            logger.warning("feedback: getUpdates failed with status=%s", response.status_code)
            return [], offset

        result = body.get("result")
        if not isinstance(result, list):
            return [], offset

        records, max_id = parse_updates(result)
        next_offset = (max_id + 1) if max_id is not None else offset
        return records, next_offset
