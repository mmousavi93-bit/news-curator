"""Unit tests for pipeline/deliver.py: the Phase 2 client behind the stage
-- mock path with no credentials, dry-run no-send, and failure handling."""

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
    message: str = ""
    dry_run: bool = False
    counters: dict = field(default_factory=dict)


def _stage(log=None):
    from agent.pipeline.deliver import DeliverStage
    return DeliverStage(env={}, logger=log or _Log())


def test_dry_run_logs_and_sends_nothing():
    ctx = _Ctx(message="hello", dry_run=True)
    log = _Log()
    _stage(log).run(ctx)
    assert ctx.counters["deliver"] == 0
    assert any("would have sent" in m for m in log.messages)


def test_no_credentials_takes_mock_path_without_crashing():
    # from_env with no credentials builds the mock client (Phase 2); the
    # stage must never raise here.
    ctx = _Ctx(message="hello")
    stage = _stage()
    stage.run(ctx)
    assert ctx.counters["deliver"] == 1  # a send (mocked) did happen


def test_no_message_is_a_no_op():
    ctx = _Ctx(message="")
    stage = _stage()
    stage.run(ctx)
    assert ctx.counters["deliver"] == 0
