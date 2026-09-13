"""Unit tests for feedback.py: offset persistence in the state DB's meta KV
table, the feedback_<ts>.csv writer, and the harvest() orchestration.
Deterministic, no network, no LLM."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent.delivery.feedback import FeedbackClient
from agent.delivery.transport import MockTransport, TransportResponse
from agent.feedback import (
    _OFFSET_KEY,
    HarvestResult,
    harvest,
    load_offset,
    save_offset,
    write_feedback_csv,
)

FAKE_TOKEN = "1234567890:***"
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return conn


def _reaction(update_id: int) -> dict:
    return {
        "update_id": update_id,
        "message_reaction": {
            "chat": {"id": -100111, "type": "channel", "title": "Digest"},
            "message_id": 55,
            "user": {"id": 700, "first_name": "Owner"},
            "date": 1700000000,
            "old_reaction": [],
            "new_reaction": [{"type": "emoji", "emoji": "👎"}],
        },
    }


def _client(updates: list) -> FeedbackClient:
    return FeedbackClient(
        FAKE_TOKEN,
        MockTransport(responses=[
            TransportResponse(status_code=200, body={"ok": True, "result": updates})
        ]),
    )


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_load_offset_none_when_unset():
    assert load_offset(_conn()) is None


def test_save_and_load_offset_roundtrip():
    conn = _conn()
    save_offset(conn, 42)
    assert load_offset(conn) == 42


def test_save_offset_overwrites_previous_value():
    conn = _conn()
    save_offset(conn, 1)
    save_offset(conn, 9)
    assert load_offset(conn) == 9
    assert conn.execute("SELECT COUNT(*) FROM meta WHERE key = ?", (_OFFSET_KEY,)).fetchone()[0] == 1


def test_load_offset_tolerates_non_integer_value():
    conn = _conn()
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (_OFFSET_KEY, "garbage"))
    assert load_offset(conn) is None


def test_write_feedback_csv_columns_and_rows(tmp_path):
    records, _ = _client([_reaction(10)]).fetch()
    path = write_feedback_csv(records, tmp_path, NOW)
    assert path.name.startswith("feedback_")
    rows = _rows(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "reaction"
    assert row["chat_id"] == "-100111"
    assert row["chat_title"] == "Digest"
    assert row["message_id"] == "55"
    assert row["reactions"] == "👎"
    assert row["text"] == ""
    assert row["date_utc"].endswith("+00:00")
    assert row["run_at_utc"] == NOW.astimezone(timezone.utc).isoformat()


def test_write_feedback_csv_handles_zero_date(tmp_path):
    update = _reaction(11)
    update["message_reaction"]["date"] = 0
    records, _ = _client([update]).fetch()
    path = write_feedback_csv(records, tmp_path, NOW)
    assert _rows(path)[0]["date_utc"] == ""


def test_harvest_persists_offset_and_writes_csv(tmp_path):
    conn = _conn()
    client = _client([_reaction(10), _reaction(11)])
    result = harvest(client, conn, tmp_path, NOW)
    assert result.offset_before is None
    assert result.offset_after == 12
    assert result.wrote_csv is not None and result.wrote_csv.exists()
    assert load_offset(conn) == 12


def test_harvest_no_records_keeps_offset_and_writes_nothing(tmp_path):
    conn = _conn()
    save_offset(conn, 30)
    client = _client([])
    result = harvest(client, conn, tmp_path, NOW)
    assert result.records == []
    assert result.wrote_csv is None
    assert result.offset_after == 30
    assert load_offset(conn) == 30


def test_harvest_resumes_from_stored_offset():
    conn = _conn()
    save_offset(conn, 10)
    client = _client([_reaction(11)])
    result = harvest(client, conn, None, NOW)
    # The fetch must have been issued with the stored offset, not from scratch.
    assert client._transport.calls[0]["json"]["offset"] == 10  # type: ignore[attr-defined]
    assert result.offset_after == 12


def test_harvest_without_conn_writes_csv_but_persists_nothing(tmp_path):
    client = _client([_reaction(10)])
    result = harvest(client, None, tmp_path, NOW)
    assert result.wrote_csv is not None and result.wrote_csv.exists()
    assert result.offset_before is None
    assert result.offset_after == 11


def test_harvest_mock_mode_returns_empty_result(tmp_path):
    client = FeedbackClient(None, MockTransport())
    conn = _conn()
    save_offset(conn, 5)
    result = harvest(client, conn, tmp_path, NOW)
    assert result.mock is True
    assert result.records == []
    assert result.offset_after == 5
    assert load_offset(conn) == 5


def test_harvest_returns_harvest_result_type(tmp_path):
    result = harvest(_client([]), _conn(), tmp_path, NOW)
    assert isinstance(result, HarvestResult)
