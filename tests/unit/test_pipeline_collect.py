"""Unit tests for pipeline/collect.py: fetch -> dedup -> store -> ctx.items,
with the collect_fn stubbed (mock discipline: the network is replaced, the
store logic is the real code)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from agent.collectors import registry
from agent.collectors.base import Item, SourceResult
from agent.memory import db as memory_db
from agent.memory.models import count_items
from agent.pipeline.collect import CollectStage
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


def _settings(mock_mode: bool) -> Settings:
    raw = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
    raw["ops"]["mock_mode"] = mock_mode
    return Settings.from_dict(raw)


def _item(url: str) -> Item:
    # Distinct title/body/raw_hash per url: dedup layers 1-3 would
    # otherwise collapse look-alike items before they are stored.
    return Item(source_id="src", url=url, title=f"t {url}", body=f"b {url}",
                published_at=NOW, lang="en", raw_hash=url[-16:])


def _report(items) -> registry.CollectReport:
    result = SourceResult(source_id="src", raw_entries=len(items),
                          parsed=len(items), kept=len(items), items=items)
    return registry.CollectReport(NOW, {"src": result}, len(items), 1, 1)


def _item_at(url: str, published_at, date_only: bool = False) -> Item:
    return Item(source_id="src", url=url, title=f"t {url}", body=f"b {url}",
                published_at=published_at, lang="en", raw_hash=url[-16:],
                date_only=date_only)


@dataclass
class _Ctx:
    db: object = None
    dry_run: bool = False
    now: datetime = NOW
    counters: dict = field(default_factory=dict)
    items: list = field(default_factory=list)


def test_collect_stores_new_items_and_sets_ctx_items(tmp_path):
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    items = [_item(f"https://x/{i}") for i in range(5)]
    ctx = _Ctx(db=conn)
    stage = CollectStage([], _settings(mock_mode=False), _Log(),
                         collect_fn=lambda s, st, now: _report(items))
    try:
        stage.run(ctx)
    finally:
        conn.close()
    assert len(ctx.items) == 5
    assert ctx.counters["collect"] == 5
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    try:
        assert count_items(conn) == 5
    finally:
        conn.close()


def test_collect_second_run_stores_only_new_items(tmp_path):
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    items = [_item(f"https://x/{i}") for i in range(5)]
    stage = CollectStage([], _settings(mock_mode=False), _Log(),
                         collect_fn=lambda s, st, now: _report(items))
    try:
        stage.run(_Ctx(db=conn))
    finally:
        conn.close()
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=False)
    try:
        # Same report again: every url already seen -> zero new.
        ctx = _Ctx(db=conn)
        stage.run(ctx)
        assert ctx.counters["collect"] == 0
        assert ctx.items == []
    finally:
        conn.close()


def test_collect_skips_in_dry_run(tmp_path):
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    log = _Log()
    ctx = _Ctx(db=conn, dry_run=True)
    called = []
    stage = CollectStage([], _settings(mock_mode=False), log,
                         collect_fn=lambda s, st, now: called.append(1) or _report([]))
    try:
        stage.run(ctx)
    finally:
        conn.close()
    assert called == []  # never fetched
    assert ctx.counters["collect"] == 0
    assert any("skipped" in m for m in log.messages)


def test_collect_skips_in_mock_mode():
    ctx = _Ctx()  # no db at all
    log = _Log()
    called = []
    stage = CollectStage([], _settings(mock_mode=True), log,
                         collect_fn=lambda s, st, now: called.append(1) or _report([]))
    stage.run(ctx)
    assert called == []


def test_collect_without_db_in_real_run_raises():
    stage = CollectStage([], _settings(mock_mode=False), _Log(),
                         collect_fn=lambda s, st, now: _report([]))
    with pytest.raises(ValueError, match="no database"):
        stage.run(_Ctx(db=None))


def test_collect_drops_stale_date_only_items(tmp_path):
    # 2026-08-30 defect: a 9-day-old date-only advisory re-surfaced as new
    # because the URL hash pruned at 7 days while the feed still listed it.
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    stale = _item_at("https://x/stale", NOW - timedelta(days=9), date_only=True)
    fresh = _item_at("https://x/fresh", NOW - timedelta(hours=10), date_only=True)
    old_full = _item_at("https://x/oldfull", NOW - timedelta(days=9))  # has a clock time
    log = _Log()
    ctx = _Ctx(db=conn)
    stage = CollectStage([], _settings(mock_mode=False), log,
                         collect_fn=lambda s, st, now: _report([stale, fresh, old_full]))
    try:
        stage.run(ctx)
    finally:
        conn.close()
    assert ctx.counters["collect"] == 2  # stale dropped, the others stored
    assert {i.url for i in ctx.items} == {"https://x/fresh", "https://x/oldfull"}
    assert any("stale date-only" in m for m in log.messages)


def test_collect_stale_drop_handles_naive_datetime(tmp_path):
    # A collector emitting naive datetimes must not crash the run; the
    # contract is UTC, so the item is treated as UTC and dropped as stale.
    conn = memory_db.open_db(tmp_path / "state.db", create_if_absent=True)
    naive = _item_at(
        "https://x/naive",
        NOW.replace(tzinfo=None) - timedelta(days=9),
        date_only=True,
    )
    ctx = _Ctx(db=conn)
    stage = CollectStage([], _settings(mock_mode=False), _Log(),
                         collect_fn=lambda s, st, now: _report([naive]))
    try:
        stage.run(ctx)
    finally:
        conn.close()
    assert ctx.counters["collect"] == 0
