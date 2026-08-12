"""Gate criterion 3: `agent.run --send-test` with no credentials set exits 0,
sends nothing real, and reports what it would have sent. Not named in the
Phase 2 file table, but required to verify the gate the brief itself defines.
"""

from __future__ import annotations

import logging

from agent.run import main


def test_send_test_with_no_credentials_exits_zero_and_logs_mock_send(caplog, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)

    with caplog.at_level(logging.INFO, logger="agent.run"):
        exit_code = main(["--send-test"])

    assert exit_code == 0
    messages = [r.getMessage() for r in caplog.records if r.name == "agent.run"]
    assert any("send-test" in m and "mock mode" in m for m in messages)


def test_send_test_does_not_run_the_pipeline_stages(caplog, monkeypatch):
    """--send-test is a standalone diagnostic -- it must not touch config
    loading or emit the normal 'run summary' line."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)

    with caplog.at_level(logging.INFO, logger="agent.run"):
        main(["--send-test"])

    messages = [r.getMessage() for r in caplog.records if r.name == "agent.run"]
    assert not any(m.startswith("run summary:") for m in messages)
