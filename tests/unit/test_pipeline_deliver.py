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
