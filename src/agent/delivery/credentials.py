"""Credential shape validation and redaction registration for the two
Telegram env vars. Split out of telegram.py to keep that file under the
~200-line limit (CLAUDE.md constraint #12) -- this is pure validation/
registration, no HTTP.
"""

from __future__ import annotations

import re

from agent.util.logging import PROCESS_FILTER, RedactionFilter

_CHAT_ID_RE = re.compile(r"^-?\d+$")
_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{5,32}$")


class TelegramConfigError(Exception):
    """TELEGRAM_CHANNEL_ID is present but shaped like neither a numeric
    chat id nor an @channelusername. Never includes the value -- public repo."""


def validate_channel_id(value: str) -> None:
    """Accepts a numeric chat id (typically negative, e.g. -1001234567890)
    or an @channelusername. Raises loudly on neither shape -- an
    unattended run should not discover a malformed id at send time."""
    if not (_CHAT_ID_RE.match(value) or _USERNAME_RE.match(value)):
        raise TelegramConfigError(
            "TELEGRAM_CHANNEL_ID has an unrecognized shape (expected a numeric "
            "chat id like -1001234567890, or an @channelusername)"
        )


def register_credentials(
    token: str | None, channel_id: str | None, *, redaction: RedactionFilter = PROCESS_FILTER
) -> None:
    """Register both credential values with the redaction filter directly,
    bypassing register_env_secrets' name/length heuristics: the env var is
    TELEGRAM_CHANNEL_ID, not *_CHAT_ID, so its name doesn't match that
    function's pattern, and a numeric channel id can be under its 16-char
    minimum."""
    if token:
        redaction.register(token)
    if channel_id:
        redaction.register(channel_id)
