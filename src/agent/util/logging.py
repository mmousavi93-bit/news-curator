"""Logger factory and value-based redaction filter.

This repo is PUBLIC (CLAUDE.md constraint #9): every log line is world-readable.
Redaction is by VALUE, not by key name. Name-based redaction only catches
`log.info("key=%s", os.environ["GEMINI_API_KEY"])`; value-based also catches the
case that actually happens -- a secret embedded in a URL, a traceback, or a
provider error string echoed back verbatim.

`get_logger()` is the ONLY sanctioned way to obtain a logger anywhere in this
codebase, because it is what guarantees the redaction filter is attached.
"""

from __future__ import annotations

import logging
import re
from typing import Mapping

_ENV_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSPHRASE|PASSWORD|CHAT_ID)", re.IGNORECASE)
# 16, not 8: every real secret in this pipeline (Gemini/Groq keys, Telegram bot
# tokens, age passphrases) is 30+ chars, while short human-chosen strings like
# "password" (8 chars) must never be registered -- doing so would redact every
# unrelated occurrence of that common word across the whole log.
_MIN_SECRET_LEN = 16
_REDACTED = "***REDACTED***"


class RedactionFilter(logging.Filter):
    """Redacts by VALUE, not by key name."""

    def __init__(self) -> None:
        super().__init__()
        self._secrets: list[str] = []
        self._pattern: re.Pattern[str] | None = None

    def register(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)
            self._pattern = None  # invalidate cache; rebuilt longest-first below

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        # Fold msg + args into one formatted string, then redact and clear args
        # so logging doesn't try to re-apply %-formatting to the redacted text.
        record.msg = self._redact(record.getMessage())
        record.args = ()
        if record.exc_info and not record.exc_text:
            # Render the traceback now, while we can still redact it, instead of
            # leaving it for the formatter to render (and leak) later.
            record.exc_text = self._redact(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        return True

    def _compiled_pattern(self) -> re.Pattern[str] | None:
        """Single alternation regex, secrets ordered longest-first.

        A plain per-secret `str.replace()` loop is order-dependent: if a
        shorter secret is a literal prefix of a longer one and runs first,
        the longer secret's unique suffix survives verbatim in the log. A
        regex alternation tries branches in listed order at each position, so
        ordering the branches longest-first guarantees the longer secret
        always wins the match when both could apply at the same position.
        """
        if not self._secrets:
            return None
        if self._pattern is None:
            ordered = sorted(self._secrets, key=len, reverse=True)
            self._pattern = re.compile("|".join(re.escape(s) for s in ordered))
        return self._pattern

    def _redact(self, text: str) -> str:
        pattern = self._compiled_pattern()
        if pattern is None:
            return text
        return pattern.sub(_REDACTED, text)


# Process-wide filter. Shared so a secret registered once (e.g. at startup from
# the environment) is redacted everywhere, regardless of which module logs it.
PROCESS_FILTER = RedactionFilter()


def register_env_secrets(f: RedactionFilter, env: Mapping[str, str]) -> int:
    """Register the VALUE of every env var whose NAME matches
    (KEY|TOKEN|SECRET|PASSPHRASE|PASSWORD|CHAT_ID). Skip values under
    _MIN_SECRET_LEN chars -- registering a short common word would redact
    that word everywhere in the log, not just the secret. Returns count
    registered."""
    count = 0
    for name, value in env.items():
        if not _ENV_SECRET_NAME_RE.search(name):
            continue
        if len(value) < _MIN_SECRET_LEN:
            continue
        f.register(value)
        count += 1
    return count


_configured = False


def get_logger(name: str) -> logging.Logger:
    """Returns a logger with the process-wide RedactionFilter attached.
    This is the ONLY sanctioned way to obtain a logger anywhere in the codebase."""
    global _configured
    if not _configured:
        root = logging.getLogger()
        if not root.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(handler)
        if root.level == logging.WARNING:  # untouched default
            root.setLevel(logging.INFO)
        _configured = True

    logger = logging.getLogger(name)
    if PROCESS_FILTER not in logger.filters:
        logger.addFilter(PROCESS_FILTER)
    return logger
