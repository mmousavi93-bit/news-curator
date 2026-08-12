from __future__ import annotations

import io
import logging

from agent.util.logging import RedactionFilter, register_env_secrets

SECRET = "sk-live-supersecretvalue12345"


def _make_logger(name: str) -> tuple[logging.Logger, io.StringIO, RedactionFilter]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    redaction = RedactionFilter()
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.filters = [redaction]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream, redaction


def test_secret_redacted_as_plain_message():
    logger, stream, redaction = _make_logger("test.redaction.plain")
    redaction.register(SECRET)
    logger.info(SECRET)
    assert SECRET not in stream.getvalue()


def test_secret_redacted_as_percent_s_arg():
    logger, stream, redaction = _make_logger("test.redaction.arg")
    redaction.register(SECRET)
    logger.info("token=%s", SECRET)
    output = stream.getvalue()
    assert SECRET not in output
    assert "token=" in output


def test_secret_redacted_mid_url():
    logger, stream, redaction = _make_logger("test.redaction.url")
    redaction.register(SECRET)
    logger.info("GET https://api.example.com/v1?key=%s&x=1 failed" % SECRET)
    output = stream.getvalue()
    assert SECRET not in output
    assert "api.example.com" in output


def test_secret_redacted_in_exception_traceback():
    logger, stream, redaction = _make_logger("test.redaction.exc")
    redaction.register(SECRET)
    try:
        raise ValueError(f"provider rejected token {SECRET}")
    except ValueError:
        logger.exception("call failed")
    output = stream.getvalue()
    assert SECRET not in output
    assert "call failed" in output


def test_short_env_value_is_not_registered():
    redaction = RedactionFilter()
    count = register_env_secrets(redaction, {"SOME_TOKEN": "abcd"})
    assert count == 0
    logger, stream, _ = _make_logger("test.redaction.short")
    logger.filters = [redaction]
    logger.info("value was abcd")
    assert "abcd" in stream.getvalue()


def test_longer_secret_with_shorter_secret_as_prefix_is_fully_redacted():
    """Regression for the defect where registration order determined outcome:
    a shorter secret that is a literal prefix of a longer one used to run
    first in a plain str.replace() loop, leaving the longer secret's unique
    suffix leaking verbatim (e.g. '***REDACTED***-with-extra-tail-data')."""
    short = "sk-shortsecret12" * 1  # 16 chars, a literal prefix of `long` below
    long = short + "-with-extra-tail-data"
    assert len(short) >= 16  # both must clear _MIN_SECRET_LEN to be realistic

    logger, stream, redaction = _make_logger("test.redaction.prefix_order")
    redaction.register(short)   # shorter secret registered FIRST
    redaction.register(long)    # longer secret registered SECOND
    logger.info("token in use: %s", long)

    output = stream.getvalue()
    assert long not in output
    assert short not in output
    assert "-with-extra-tail-data" not in output
    assert output.count("***REDACTED***") == 1


def test_short_common_word_is_never_registered_as_a_secret():
    """A password-like 8-char value must not be registered -- that would
    redact every unrelated occurrence of a common word in the log."""
    redaction = RedactionFilter()
    count = register_env_secrets(redaction, {"DB_PASSWORD": "password"})
    assert count == 0
    logger, stream, _ = _make_logger("test.redaction.common_word")
    logger.filters = [redaction]
    logger.info("the password field was blank")
    assert "password" in stream.getvalue()


def test_register_env_secrets_matches_expected_name_patterns():
    redaction = RedactionFilter()
    env = {
        "GEMINI_API_KEY": "longenoughsecretvalue1",
        "TELEGRAM_BOT_TOKEN": "longenoughsecretvalue2",
        "SOME_SECRET": "longenoughsecretvalue3",
        "AGE_PASSPHRASE": "longenoughsecretvalue4",
        "DB_PASSWORD": "longenoughsecretvalue5",
        "TELEGRAM_CHAT_ID": "longenoughsecretvalue6",
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/owner",
    }
    count = register_env_secrets(redaction, env)
    assert count == 6

    logger, stream, _ = _make_logger("test.redaction.env")
    logger.filters = [redaction]
    logger.info("secret was %s", "longenoughsecretvalue1")
    assert "longenoughsecretvalue1" not in stream.getvalue()
