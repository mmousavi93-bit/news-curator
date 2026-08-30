"""Unit tests for pipeline/deliver.py: multi-message sends through the
Phase 2 client -- mock path with no credentials, dry-run no-send, and
failure handling."""

from __future__ import annotations

from dataclasses import dataclass, field


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


@dataclass
class _Ctx:
    messages: list = field(default_factory=list)
    dry_run: bool = False
    counters: dict = field(default_factory=dict)


def _stage(log=None):
    from agent.pipeline.deliver import DeliverStage
    return DeliverStage(env={}, logger=log or _Log())


def test_dry_run_logs_every_message_and_sends_nothing():
    ctx = _Ctx(messages=["first", "second"], dry_run=True)
    log = _Log()
    _stage(log).run(ctx)
    assert ctx.counters["deliver"] == 0
    assert sum(1 for m in log.messages if "would have sent" in m) == 2


def test_no_credentials_takes_mock_path_without_crashing():
    ctx = _Ctx(messages=["hello", "world"])
    _stage().run(ctx)
    assert ctx.counters["deliver"] == 2  # both (mocked) sends succeeded


def test_no_messages_is_a_no_op():
    ctx = _Ctx(messages=[])
    _stage().run(ctx)
    assert ctx.counters["deliver"] == 0


class _FakeResult:
    def __init__(self, ok, mocked=False):
        self.ok = ok
        self.mocked = mocked
        self.attempts = 1
        self.status_code = None
        self.description = ""


class _FakeClient:
    def __init__(self, ok=True):
        self._ok = ok
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return _FakeResult(self._ok)


def _ctx_with_db(tmp_path, keys):
    from datetime import datetime, timezone
    from agent.memory import db as memory_db
    ctx = _Ctx(messages=["msg"])
    ctx.db = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    ctx.compose_kept_keys = keys
    ctx.now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    return ctx


def test_all_real_sends_ok_writes_received_markers(tmp_path, monkeypatch):
    # Received-markers are written by DELIVER, only after real sends
    # succeeded (review finding 2026-08-30: marking in compose would
    # re-create ghost suppression when a send fails).
    from agent.memory.event_models import Event, insert_events, read_recent_events
    key = "k" * 16
    ctx = _ctx_with_db(tmp_path, [key])
    insert_events(ctx.db, [Event(event_key=key, summary="s", source_count=1,
                                  first_seen_at=ctx.now, last_updated_at=ctx.now)])
    try:
        stage = _stage()
        monkeypatch.setattr(stage, "_client", lambda: _FakeClient(ok=True))
        stage.run(ctx)
        delivered = read_recent_events(
            ctx.db, hours=72, now=ctx.now, delivered_only=True
        )
        assert [e.event_key for e in delivered] == [key]
    finally:
        ctx.db.close()


def test_failed_send_writes_no_received_markers(tmp_path, monkeypatch):
    from agent.memory.event_models import Event, insert_events, read_recent_events
    key = "k" * 16
    ctx = _ctx_with_db(tmp_path, [key])
    insert_events(ctx.db, [Event(event_key=key, summary="s", source_count=1,
                                  first_seen_at=ctx.now, last_updated_at=ctx.now)])
    try:
        stage = _stage()
        monkeypatch.setattr(stage, "_client", lambda: _FakeClient(ok=False))
        stage.run(ctx)
        delivered = read_recent_events(
            ctx.db, hours=72, now=ctx.now, delivered_only=True
        )
        assert delivered == []  # owner never saw them -- follow-ups must flow
    finally:
        ctx.db.close()


def test_mocked_sends_write_no_received_markers(tmp_path):
    # The no-credentials mock path "succeeds" without sending: markers
    # would pollute a local --db run. Mocked results never mark.
    from agent.memory.event_models import read_recent_events
    ctx = _ctx_with_db(tmp_path, ["k" * 16])
    try:
        _stage().run(ctx)  # env={} -> mock client, results mocked
        delivered = read_recent_events(
            ctx.db, hours=72, now=ctx.now, delivered_only=True
        )
        assert delivered == []
    finally:
        ctx.db.close()
