"""Unit tests for report_csv.py: the per-run observability CSVs (owner
request 2026-08-30 -- input/output qualification for digest tuning).
Deterministic, no LLM, no network: synthetic ctx, parse-back assertions."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.report_csv import write_run_reports
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _config() -> Config:
    settings = Settings.from_dict(yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")))
    return Config(settings=settings, credibility={
        "t1": SourceCredibility(tier=1, group="g1"),
        "t2": SourceCredibility(tier=2, group="g2"),
        "t3": SourceCredibility(tier=3, group="g3"),
    })


def _item(source_id: str, url: str) -> Item:
    return Item(source_id=source_id, url=url, title=f"title {url}",
                body="body text " * 10, published_at=NOW, lang="en",
                raw_hash="h" * 8)


def _cluster(key_seed: str, source_id: str, url: str) -> Cluster:
    cluster = Cluster(key="")
    cluster.add(_item(source_id, url), [1.0])
    return cluster


class _Ctx:
    def __init__(self, tmp_path: Path):
        self.config = _config()
        self.now = NOW
        self.daily_digest = True
        self.counters = {"collect": 12, "cluster": 5, "compose_lang_drops": 1,
                         "deliver": 1}
        self.items = [
            _item("t1", "https://x/1"),
            _item("t2", "https://x/2"),
        ]
        sent = _cluster("s", "t2", "https://x/sent")
        rank = _cluster("r", "t3", "https://x/rank")
        lang = _cluster("l", "t2", "https://x/lang")
        click = _cluster("c", "t2", "https://x/click")
        repeat = _cluster("p", "t2", "https://x/repeat")
        lead = _cluster("d", "t2", "https://x/lead")
        self.clusters = [sent, rank, lang, click, repeat, lead]
        self.events = [
            Event(event_key=sent.key, headline="تیتر اصلی", summary="خلاصه نظامی.",
                  category="military", claim_status="likely",
                  independent_count=2, source_count=2,
                  first_seen_at=NOW, last_updated_at=NOW),
            Event(event_key=rank.key, summary="شایعه سیاسی.", category="politics",
                  claim_status="rumour", independent_count=0, source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
            Event(event_key=lang.key, headline="", summary="يك خلاصة.",
                  category="military", claim_status="unconfirmed",
                  independent_count=1, source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
            Event(event_key=repeat.key, summary="تکرار خبر دیروز.",
                  category="security", claim_status="unconfirmed",
                  independent_count=1, source_count=1,
                  first_seen_at=NOW, last_updated_at=NOW),
        ]
        self.compose_kept_keys = [sent.key]
        self.rank_dropped = [self.events[1]]
        self.lang_dropped = [self.events[2]]
        self.repeat_dropped = [self.events[3]]
        self.lead_events = [
            Event(event_key=lead.key, summary="سرنخ نظامی.", category="military",
                  source_count=1, first_seen_at=NOW, last_updated_at=NOW),
        ]
        self.cluster_fates = [(click.key, "clickbait")]
        self.llm_failed = False
        self.messages = ["<b>⚔️ تیتر اصلی</b>"]


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_read_csv_one_row_per_item(tmp_path):
    ctx = _Ctx(tmp_path)
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    rows = _rows(written["read"])
    assert len(rows) == 2
    assert rows[0]["source_id"] == "t1"
    assert rows[0]["url"] == "https://x/1"
    assert len(rows[0]["body"]) <= 400 + 3  # cap + ellipsis


def test_chosen_csv_records_every_fate(tmp_path):
    ctx = _Ctx(tmp_path)
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    by_key = {r["cluster_key"]: r for r in _rows(written["chosen"])}
    assert len(by_key) == 6
    assert by_key[ctx.clusters[0].key]["fate"] == "sent"
    assert by_key[ctx.clusters[1].key]["fate"] == "rank_dropped"
    assert by_key[ctx.clusters[2].key]["fate"] == "lang_dropped"
    assert by_key[ctx.clusters[3].key]["fate"] == "clickbait"
    assert by_key[ctx.clusters[4].key]["fate"] == "repeat_dropped"
    assert by_key[ctx.clusters[5].key]["fate"] == "lead_only"
    # The sent row carries the deterministic rank score.
    assert float(by_key[ctx.clusters[0].key]["score"]) >= 8


def test_summaries_csv_contains_only_sent_events(tmp_path):
    ctx = _Ctx(tmp_path)
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    rows = _rows(written["summaries"])
    assert len(rows) == 1  # only the sent event
    assert rows[0]["event_key"] == ctx.clusters[0].key
    assert rows[0]["headline"] == "تیتر اصلی"
    assert rows[0]["summary"] == "خلاصه نظامی."
    assert rows[0]["rank"] == "0"
    assert float(rows[0]["score"]) >= 8


def test_summaries_csv_rank_follows_digest_score_order(tmp_path):
    # rank 0 must be the digest's first item (highest score), not the first
    # event created -- the two disagree in any run whose events are created
    # out of score order (the 2026-08-30 run: rank column meant nothing).
    ctx = _Ctx(tmp_path)
    first = _cluster("hi", "t1", "https://x/hi")   # tier 1 military -> top
    second = _cluster("lo", "t3", "https://x/lo")  # tier 3 other -> below
    ctx.clusters = [first, second] + ctx.clusters
    ctx.events = [
        Event(event_key=second.key, headline="پایین", summary="متن پایین.",
              category="other", claim_status="unconfirmed",
              independent_count=1, source_count=1,
              first_seen_at=NOW, last_updated_at=NOW),
        Event(event_key=first.key, headline="بالا", summary="متن بالا.",
              category="military", claim_status="unconfirmed",
              independent_count=1, source_count=1,
              first_seen_at=NOW, last_updated_at=NOW),
    ]
    ctx.compose_kept_keys = [first.key, second.key]
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    rows = _rows(written["summaries"])
    assert [r["event_key"] for r in rows] == [first.key, second.key]
    assert [r["rank"] for r in rows] == ["0", "1"]


def test_run_csv_records_counters_and_digest_flag(tmp_path):
    ctx = _Ctx(tmp_path)
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    rows = _rows(written["run"])
    assert len(rows) == 1
    row = rows[0]
    assert row["daily_digest"] == "1"
    assert row["items"] == "12"
    assert row["lang_drops"] == "1"
    assert row["sent"] == "1"
    assert row["repeat_dropped"] == "1"


def test_write_run_reports_returns_four_paths(tmp_path):
    ctx = _Ctx(tmp_path)
    written = write_run_reports(ctx, tmp_path)
    assert len(written) == 4
    assert all(p.exists() for p in written)


def test_run_pipeline_writes_reports_when_dir_set(tmp_path):
    # The run.py hook: after the stages, CSVs land in the configured dir.
    import logging

    from agent.run import RunContext, run_pipeline
    ctx = RunContext(config=_config(), dry_run=True, now=NOW,
                     report_dir=tmp_path)
    run_pipeline(ctx, [], logging.getLogger("t"))
    assert len(list(tmp_path.glob("*.csv"))) == 4
