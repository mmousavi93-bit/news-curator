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


def test_run_csv_carries_per_provider_stats(tmp_path):
    # 2026-08-30: who carried the run and who failed it, in the artifact.
    class _Stats:
        def as_dict(self):
            return {"bai": {"calls": 7, "failed": 2},
                    "gemini": {"calls": 3, "failed": 3}}

    ctx = _Ctx(tmp_path)
    ctx.router = type("R", (), {"stats": _Stats()})()
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    rows = _rows(written["run"])
    assert rows[0]["calls_bai"] == "7"
    assert rows[0]["fails_bai"] == "2"
    assert rows[0]["calls_gemini"] == "3"
    assert rows[0]["fails_gemini"] == "3"


def test_run_csv_without_router_has_no_stat_columns(tmp_path):
    ctx = _Ctx(tmp_path)  # no router attribute
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    rows = _rows(written["run"])
    assert not any(k.startswith("calls_") for k in rows[0])


def test_chosen_csv_carries_text_for_event_rows(tmp_path):
    # The Masafer Yatta lesson: a drop is unjudgeable without the words
    # the gate saw. Event-bearing rows carry headline/summary; pre-event
    # fates (clickbait) carry empty strings.
    ctx = _Ctx(tmp_path)
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    by_key = {r["cluster_key"]: r for r in _rows(written["chosen"])}
    assert by_key[ctx.clusters[0].key]["headline"] == "تیتر اصلی"
    assert by_key[ctx.clusters[0].key]["summary"] == "خلاصه نظامی."
    assert by_key[ctx.clusters[2].key]["summary"] == "يك خلاصة."
    assert by_key[ctx.clusters[3].key]["headline"] == ""  # clickbait: no event
    assert by_key[ctx.clusters[3].key]["summary"] == ""


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


def test_provider_provenance_columns_record_who_answered(tmp_path):
    # Owner 2026-08-31: the labeled last rung -- every event carries the
    # provider that produced it, so a deepseek answer is traceable.
    ctx = _Ctx(tmp_path)
    sent_key = ctx.clusters[0].key
    ctx.cluster_provider = {sent_key: "deepseek"}
    ctx.event_provider = {sent_key: "deepseek"}
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    chosen = {r["cluster_key"]: r for r in _rows(written["chosen"])}
    assert chosen[sent_key]["provider"] == "deepseek"
    summaries = _rows(written["summaries"])
    assert summaries[0]["provider"] == "deepseek"


def test_repeat_dropped_reason_carries_calibration_fields(tmp_path):
    # Fix E, 2026-09-06 review: every OTHER fate still writes "" in the
    # reason column, but repeat_dropped must carry similarity, the new
    # event's score and which conjunction term failed -- the owner's only
    # way to calibrate event_match_threshold / repeat_bypass_score without
    # re-deriving the drop from repeats.py.
    ctx = _Ctx(tmp_path)
    repeat_key = ctx.clusters[4].key
    ctx.repeat_drop_reasons = {
        repeat_key: "sim=0.65 score=11.74 blocked=no_development",
    }
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    by_key = {r["cluster_key"]: r for r in _rows(written["chosen"])}
    assert by_key[repeat_key]["fate"] == "repeat_dropped"
    assert by_key[repeat_key]["reason"] == "sim=0.65 score=11.74 blocked=no_development"
    # Every other fate is untouched -- reason stays "".
    assert by_key[ctx.clusters[1].key]["reason"] == ""


def test_repeat_dropped_reason_defaults_to_empty_when_unset(tmp_path):
    # No ctx.repeat_drop_reasons at all (older/mocked ctx) must not crash
    # _fate_for and must fall back to the pre-fix "" behaviour.
    ctx = _Ctx(tmp_path)
    assert not hasattr(ctx, "repeat_drop_reasons")
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    by_key = {r["cluster_key"]: r for r in _rows(written["chosen"])}
    assert by_key[ctx.clusters[4].key]["fate"] == "repeat_dropped"
    assert by_key[ctx.clusters[4].key]["reason"] == ""


def test_sent_event_carries_repeat_gate_reason_when_matched(tmp_path):
    # Fix 5, round-2 review: a SURVIVING event that matched the repeat gate
    # (bypassed, HIGH or MID band) must carry the same band/sim/score
    # reason a dropped one does -- the owner needs both sides of the gate
    # to calibrate event_repeat_threshold / repeat_bypass_score, not just
    # the drops.
    ctx = _Ctx(tmp_path)
    sent_key = ctx.clusters[0].key
    ctx.repeat_drop_reasons = {
        sent_key: "band=mid sim=0.65 score=11.74 kept=above_floor",
    }
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    by_key = {r["cluster_key"]: r for r in _rows(written["chosen"])}
    assert by_key[sent_key]["fate"] == "sent"
    assert by_key[sent_key]["reason"] == "band=mid sim=0.65 score=11.74 kept=above_floor"


def test_truncated_event_has_its_own_fate_and_is_not_sent(tmp_path):
    # Fix 3/5, round-2 review: an event cut by the character budget must not
    # appear as "sent" (compose_kept_keys excludes it by construction, since
    # it never rendered) and must not fall through to the "event_unresolved"
    # anomaly -- it gets its own "truncated" fate, with headline/summary
    # preserved (the Masafer Yatta lesson: a drop is unjudgeable without
    # the text the gate saw).
    ctx = _Ctx(tmp_path)
    truncated_cluster = _cluster("u", "t2", "https://x/truncated")
    ctx.clusters.append(truncated_cluster)
    truncated_event = Event(
        event_key=truncated_cluster.key, headline="بریده‌شده", summary="خلاصه بریده.",
        category="military", claim_status="unconfirmed",
        independent_count=1, source_count=1,
        first_seen_at=NOW, last_updated_at=NOW,
    )
    ctx.compose_truncated = [truncated_event]
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    by_key = {r["cluster_key"]: r for r in _rows(written["chosen"])}
    assert by_key[truncated_cluster.key]["fate"] == "truncated"
    assert (by_key[truncated_cluster.key]["reason"]
            == "cut by the character budget; not marked delivered")
    assert by_key[truncated_cluster.key]["headline"] == "بریده‌شده"
    assert by_key[truncated_cluster.key]["summary"] == "خلاصه بریده."
    assert truncated_cluster.key not in set(ctx.compose_kept_keys)


def test_cap_dropped_reason_carries_on_mission_and_corroborating_count(tmp_path):
    # Fix 5, round-4 review: priority.py's actual cap sort key ranks on
    # (on_mission, tier_weight, corroborating_count, recency, size) --
    # NOT the independent_count column (a different, wider, every-tier
    # count kept for its own stable meaning). Before this fix the
    # cap_dropped reason carried neither term, so a cap_dropped row could
    # not be checked against the sort that actually produced it.
    import dataclasses

    from agent.pipeline.relevance import validate_relevance

    ctx = _Ctx(tmp_path)
    ctx.config = dataclasses.replace(ctx.config, relevance=validate_relevance({
        "weights": {"iran_direct": 8},
        "keywords": {"iran_direct": ["ایران"]},
    }))
    on_mission_cluster = Cluster(key="")
    on_mission_cluster.add(
        Item(source_id="t1", url="https://x/cap1", title="ایران در آستانه توافق",
             body="", published_at=NOW, lang="fa", raw_hash="a" * 8),
        [1.0],
    )
    on_mission_cluster.add(_item("t2", "https://x/cap1b"), [1.0])
    off_mission_cluster = _cluster("cap2", "t3", "https://x/cap2")
    ctx.clusters_cap_dropped = [on_mission_cluster, off_mission_cluster]
    written = {p.name.split("_")[0]: p for p in write_run_reports(ctx, tmp_path)}
    by_key = {r["cluster_key"]: r for r in _rows(written["chosen"])}

    on_row = by_key[on_mission_cluster.key]
    assert on_row["fate"] == "cap_dropped"
    assert "on_mission=1" in on_row["reason"]
    assert "corroborating_count=2" in on_row["reason"]  # t1 + t2 groups

    off_row = by_key[off_mission_cluster.key]
    assert "on_mission=0" in off_row["reason"]
    assert "corroborating_count=0" in off_row["reason"]  # t3 never corroborates


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


def test_write_run_reports_returns_five_paths(tmp_path):
    # Five since session 9s: pairs_<ts>.csv joined the four.
    ctx = _Ctx(tmp_path)
    written = write_run_reports(ctx, tmp_path)
    assert len(written) == 5
    assert all(p.exists() for p in written)
    assert any(p.name.startswith("pairs_") for p in written)


def test_run_pipeline_writes_reports_when_dir_set(tmp_path):
    # The run.py hook: after the stages, CSVs land in the configured dir.
    import logging

    from agent.run import RunContext, run_pipeline
    ctx = RunContext(config=_config(), dry_run=True, now=NOW,
                     report_dir=tmp_path)
    run_pipeline(ctx, [], logging.getLogger("t"))
    assert len(list(tmp_path.glob("*.csv"))) == 5
