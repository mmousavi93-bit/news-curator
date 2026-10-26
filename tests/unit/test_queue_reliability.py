"""Offline queue lifecycle regressions: real SQLite, no model/network."""
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.memory import db as memory_db
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.pipeline.queue import load_pending, select_digest
from agent.settings import Settings

NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


def _Log():
    return logging.getLogger(__name__)


def _config():
    fixture = Path(__file__).parents[1] / "fixtures" / "settings_minimal.yaml"
    settings = Settings.from_dict(yaml.safe_load(fixture.read_text(encoding="utf-8")))
    return Config(settings=settings, credibility={
        "t1": SourceCredibility(tier=1, group="g1"),
    })


def _fresh(ctx, seeds):
    events = []
    for seed in seeds:
        cluster = Cluster(key="")
        cluster.add(Item(source_id="t1", url=f"https://example.test/{seed}",
                         title="خبر ایران", body="گزارش تازه", published_at=NOW,
                         lang="fa", raw_hash=seed), [1.0])
        ctx.clusters.append(cluster)
        events.append(Event(event_key=cluster.key, headline=f"خبر ایران {seed}",
                            summary="خلاصه گزارش تازه درباره ایران.",
                            category="military", significance="escalation",
                            claim_status="likely", independent_count=2, source_count=2,
                            first_seen_at=NOW, last_updated_at=NOW))
    return events


def _ctx(path, **overrides):
    values = dict(config=_config(), now=NOW, counters={}, events=[], clusters=[],
                  dry_run=False, db=memory_db.open_db(path, create_if_absent=True))
    values.update(overrides)
    return SimpleNamespace(**values)


def test_selected_and_deferred_admissions_survive_restart_before_any_send(tmp_path):
    path = tmp_path / "state.db"
    ctx = _ctx(path)
    fresh = _fresh(ctx, [f"retained-{i}" for i in range(8)])
    selected = select_digest(ctx, fresh, ctx.config.settings, _Log())
    assert len(selected) == 5
    ctx.db.close()
    restarted = _ctx(path)
    try:
        assert {e.event_key for e, _ in load_pending(restarted, NOW)} == {
            e.event_key for e in fresh
        }
    finally:
        restarted.db.close()


import pytest
from dataclasses import replace
from datetime import timedelta
from agent.pipeline.compose import ComposeStage


@pytest.mark.parametrize("mode", ["quiet", "below_floor", "llm_failed", "language_failed"])
def test_pending_is_composed_even_without_fresh_eligible_events(tmp_path, mode):
    path = tmp_path / "quiet.db"
    ctx = _ctx(path)
    queued = _fresh(ctx, ["queued"])[0]
    select_digest(ctx, [queued], ctx.config.settings, _Log())
    ctx.db.close()
    nxt = _ctx(path, now=NOW + timedelta(hours=1))
    try:
        if mode in {"below_floor", "language_failed"}:
            fresh = _fresh(nxt, ["new"])[0]
            nxt.events = [replace(fresh, significance="none") if mode == "below_floor"
                          else replace(fresh, headline="خلاصة عربية", summary="الخلاصة العربية للحادثة")]
        nxt.llm_failed = mode == "llm_failed"
        ComposeStage(_Log()).run(nxt)
        assert nxt.compose_kept_keys == [queued.event_key]
        assert nxt.counters["compose"] == 1
        if mode == "llm_failed":
            assert "هوش مصنوعی" in "\n".join(nxt.messages)
        if mode == "language_failed":
            assert "فارسی" in "\n".join(nxt.messages)
    finally:
        nxt.db.close()


def test_same_key_refresh_preserves_first_admission_and_cannot_revive_expired_snapshot(tmp_path):
    ctx = _ctx(tmp_path / "expiry.db")
    try:
        event = _fresh(ctx, ["expiry"])[0]
        select_digest(ctx, [event], ctx.config.settings, _Log())
        admitted = ctx.db.execute("SELECT ranked_at FROM pending").fetchone()[0]
        ctx.now = NOW + timedelta(hours=23, minutes=59)
        assert select_digest(ctx, [event], ctx.config.settings, _Log()) == [event]
        assert ctx.db.execute("SELECT ranked_at FROM pending").fetchone()[0] == admitted
        ctx.now = NOW + timedelta(hours=24)
        assert select_digest(ctx, [event], ctx.config.settings, _Log()) == []
        assert load_pending(ctx, ctx.now) == []
        ctx.now += timedelta(hours=1)
        assert select_digest(ctx, [event], ctx.config.settings, _Log()) == []
    finally:
        ctx.db.close()


def test_partial_send_acknowledges_only_successful_page_with_identical_headlines(tmp_path):
    from agent.pipeline.deliver import DeliverStage
    ctx = _ctx(tmp_path / "partial.db")
    ctx.config = replace(ctx.config, settings=replace(
        ctx.config.settings, delivery=replace(ctx.config.settings.delivery, telegram_max_chars=220)))
    events = _fresh(ctx, ["page-a", "page-b"])
    ctx.events = [replace(e, headline="گزارش ایران " + "بررسی رویداد " * 8,
                          summary="شرح گزارش درباره تحولات تازه ایران. " * 20) for e in events]
    try:
        ComposeStage(_Log()).run(ctx)
        assert len(ctx.messages) == 2
        first_key, second_key = ctx.compose_kept_keys
        outcomes = iter([True, False])
        client = SimpleNamespace(send=lambda text: SimpleNamespace(
            ok=next(outcomes), mocked=False, attempts=1, status_code=None, description="test"))
        stage = DeliverStage({}, _Log())
        stage._client = lambda: client
        stage.run(ctx)
        assert {r[0] for r in ctx.db.execute("SELECT event_key FROM delivered")} == {first_key}
        ctx.db.close()
        ctx = _ctx(tmp_path / "partial.db", now=NOW + timedelta(minutes=1))
        assert {e.event_key for e, _ in load_pending(ctx, ctx.now)} == {second_key}
        ComposeStage(_Log()).run(ctx)
        assert ctx.compose_kept_keys == [second_key]
    finally:
        ctx.db.close()


def _deliver(ctx, *, ok=False, mocked=False, unavailable=False):
    from agent.pipeline.deliver import DeliverStage
    stage = DeliverStage({}, _Log())
    client = SimpleNamespace(send=lambda text: SimpleNamespace(
        ok=ok, mocked=mocked, attempts=1, status_code=None, description="offline test"))
    stage._client = lambda: None if unavailable else client
    stage.run(ctx)


def _report_rows(ctx, path, name):
    import csv
    from agent.report_csv import write_run_reports
    paths = write_run_reports(ctx, path)
    target = next(p for p in paths if p.name.startswith(name + "_"))
    with target.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_report_includes_pending_winner_without_current_events_or_clusters(tmp_path):
    path = tmp_path / "pending.db"
    ctx = _ctx(path)
    event = _fresh(ctx, ["pending-report"])[0]
    select_digest(ctx, [event], ctx.config.settings, _Log())
    ctx.db.close()
    nxt = _ctx(path, now=NOW + timedelta(minutes=30))
    try:
        ComposeStage(_Log()).run(nxt)
        _deliver(nxt)
        rows = _report_rows(nxt, tmp_path / "reports", "summaries")
        assert len(rows) == 1
        assert rows[0]["event_key"] == event.event_key
        assert rows[0]["delivery_state"] == "failed_or_unknown"
        assert rows[0]["origin"] == "pending"
        assert rows[0]["sources"] == ""  # no invented deferred provenance
        assert rows[0]["score"] != ""
    finally:
        nxt.db.close()


def test_report_distinguishes_deferred_candidates_from_delivery_failures(tmp_path):
    ctx = _ctx(tmp_path / "deferred.db")
    ctx.events = _fresh(ctx, [f"report-{i}" for i in range(8)])
    try:
        ComposeStage(_Log()).run(ctx)
        _deliver(ctx)
        chosen = _report_rows(ctx, tmp_path / "reports", "chosen")
        assert len(chosen) == 8
        assert sum(r["fate"] == "deferred" for r in chosen) == 3
        assert all(r["fate"] != "event_unresolved" for r in chosen)
        summaries = _report_rows(ctx, tmp_path / "reports", "summaries")
        assert len(summaries) == 5
        assert {r["delivery_state"] for r in summaries} == {"failed_or_unknown"}
        run = _report_rows(ctx, tmp_path / "reports", "run")[0]
        assert run["delivered_events"] == "0"
        assert run["selected"] == "5"
    finally:
        ctx.db.close()


def test_report_records_budget_exclusion_and_preserves_retry_candidates(tmp_path):
    ctx = _ctx(tmp_path / "budget.db")
    ctx.config = replace(ctx.config, settings=replace(
        ctx.config.settings, delivery=replace(ctx.config.settings.delivery, telegram_max_chars=1)))
    ctx.events = _fresh(ctx, ["budget-a", "budget-b"])
    try:
        ComposeStage(_Log()).run(ctx)
        _deliver(ctx, ok=True)
        assert ctx.compose_kept_keys == []
        assert len(load_pending(ctx, ctx.now)) == 2
        assert list(ctx.db.execute("SELECT event_key FROM delivered")) == []
        summaries = _report_rows(ctx, tmp_path / "reports", "summaries")
        assert len(summaries) == 2
        assert {r["composition_state"] for r in summaries} == {"budget_excluded"}
        assert {r["delivery_state"] for r in summaries} == {"not_attempted"}
    finally:
        ctx.db.close()


@pytest.mark.parametrize("mode", ["failure", "mocked", "dry_run", "unavailable"])
def test_unacknowledged_candidates_remain_eligible_after_restart(tmp_path, mode):
    path = tmp_path / "retry.db"
    ctx = _ctx(path)
    ctx.events = _fresh(ctx, ["retry"])
    key = ctx.events[0].event_key
    ComposeStage(_Log()).run(ctx)
    ctx.dry_run = mode == "dry_run"
    _deliver(ctx, ok=mode == "mocked", mocked=mode == "mocked", unavailable=mode == "unavailable")
    assert ctx.delivered_keys == []
    assert list(ctx.db.execute("SELECT event_key FROM delivered")) == []
    ctx.db.close()
    nxt = _ctx(path, now=NOW + timedelta(hours=1))
    try:
        ComposeStage(_Log()).run(nxt)
        assert nxt.compose_kept_keys == [key]
    finally:
        nxt.db.close()


@pytest.mark.parametrize("publish_age_hours", [26, 71])
def test_old_publication_can_enter_queue_now_and_get_full_admission_lifetime(tmp_path, publish_age_hours):
    ctx = _ctx(tmp_path / "old-publication.db")
    ctx.config = replace(ctx.config, settings=replace(
        ctx.config.settings, digest_rank=replace(ctx.config.settings.digest_rank, min_score=15)))
    event = _fresh(ctx, [f"old-publish-{publish_age_hours}"])[0]
    published = NOW - timedelta(hours=publish_age_hours)
    ctx.events = [replace(event, first_seen_at=published, last_updated_at=published)]
    for cluster in ctx.clusters:
        cluster.members[:] = [replace(m, published_at=published, date_only=True) for m in cluster.members]
    try:
        ComposeStage(_Log()).run(ctx)
        assert ctx.compose_kept_keys == [event.event_key]
        assert ctx.db.execute("SELECT ranked_at FROM pending").fetchone()[0] == NOW.isoformat()
        chosen = _report_rows(ctx, tmp_path / "reports", "chosen")
        assert chosen[0]["fate"] != "event_unresolved"
        assert chosen[0]["origin"] == "fresh"
        original = ctx.events[0]
        ctx.now = NOW + timedelta(hours=23, minutes=59)
        ctx.events, ctx.clusters = [], []
        ComposeStage(_Log()).run(ctx)
        assert ctx.compose_kept_keys == [event.event_key]
        ctx.now = NOW + timedelta(hours=24)
        assert select_digest(ctx, [original], ctx.config.settings, _Log()) == []
    finally:
        ctx.db.close()


def test_expired_key_without_publication_time_cannot_revive_after_restart(tmp_path):
    path = tmp_path / "no-publication.db"
    ctx = _ctx(path)
    event = replace(_fresh(ctx, ["no-publication"])[0], first_seen_at=None, last_updated_at=None)
    select_digest(ctx, [event], ctx.config.settings, _Log())
    ctx.now = NOW + timedelta(hours=24)
    assert select_digest(ctx, [], ctx.config.settings, _Log()) == []
    ctx.db.close()
    nxt = _ctx(path, now=NOW + timedelta(hours=25))
    try:
        assert select_digest(nxt, [event], nxt.config.settings, _Log()) == []
        assert {e.event_key for e in nxt.queue_expired} == {event.event_key}
        assert list(nxt.db.execute("SELECT event_key FROM pending")) == []
        from agent.pipeline.queue import store_pending
        marker_key = "digest_queue_expired:" + event.event_key
        marker_time = (NOW + timedelta(hours=24)).isoformat()
        assert nxt.db.execute("SELECT value FROM meta WHERE key=?", (marker_key,)).fetchone()[0] == marker_time
        # Direct persistence and repeated replay must not bypass or renew expiry.
        store_pending(nxt, [(event, 99.0)], nxt.now)
        assert list(nxt.db.execute("SELECT event_key FROM pending")) == []
        nxt.now += timedelta(hours=1)
        assert select_digest(nxt, [event], nxt.config.settings, _Log()) == []
        assert nxt.db.execute("SELECT value FROM meta WHERE key=?", (marker_key,)).fetchone()[0] == marker_time
        # Bound terminal memory by the existing retention setting; spare other meta.
        nxt.db.execute("INSERT INTO meta (key,value) VALUES (?,?)", ("unrelated", marker_time))
        nxt.now = NOW + timedelta(hours=24, days=nxt.config.settings.retention.events_days)
        assert select_digest(nxt, [], nxt.config.settings, _Log()) == []
        assert nxt.db.execute("SELECT 1 FROM meta WHERE key=?", (marker_key,)).fetchone() is None
        assert nxt.db.execute("SELECT value FROM meta WHERE key='unrelated'").fetchone()[0] == marker_time
    finally:
        nxt.db.close()


@pytest.mark.parametrize("queue_state", ["selected", "deferred", "expired"])
@pytest.mark.parametrize("current_fate", ["lang_rejected", "unavailable"])
def test_chosen_uses_authoritative_queue_snapshot_and_fate(tmp_path, queue_state, current_fate):
    from agent.pipeline.queue import store_pending
    ctx = _ctx(tmp_path / "snapshot-precedence.db")
    original = _fresh(ctx, ["same-key-report"])[0]
    store_pending(ctx, [(original, 20.0)], ctx.now)
    if queue_state == "deferred":
        other = _fresh(ctx, ["higher-priority"])[0]
        store_pending(ctx, [(other, 99.0)], ctx.now)
        ctx.config = replace(ctx.config, settings=replace(ctx.config.settings,
            digest_rank=replace(ctx.config.settings.digest_rank, max_items=1)))
    if queue_state == "expired":
        ctx.now += timedelta(hours=24)
    if current_fate == "lang_rejected":
        ctx.events = [replace(original, headline="CURRENT REJECTED \u0629", summary="Rejected current version.")]
    else:
        ctx.events = []
        ctx.cluster_fates = [(original.event_key, "unavailable")]
        ctx.llm_failed = True
    ctx.cluster_provider = {original.event_key: "current-only-provider"}
    ctx.event_provider = dict(ctx.cluster_provider)
    ctx.prellm_scores = {original.event_key: 0.9}
    try:
        ComposeStage(_Log()).run(ctx)
        row = next(r for r in _report_rows(ctx, tmp_path / "reports", "chosen")
                   if r["cluster_key"] == original.event_key)
        assert row["headline"] == original.headline
        assert row["summary"] == original.summary
        assert row["fate"] == ("sent" if queue_state == "selected" else queue_state)
        assert row["origin"] == "pending"
        assert row["composition_state"] in ({"full", "headline_only"} if queue_state == "selected" else {queue_state})
        for field in ("provider", "best_tier", "n_members", "sources", "prellm_score", "on_mission"):
            assert row[field] == "", (field, row[field])
        if queue_state == "selected":
            summary = next(r for r in _report_rows(ctx, tmp_path / "reports", "summaries")
                           if r["event_key"] == original.event_key)
            assert summary["headline"] == original.headline
            assert summary["summary"] == original.summary
            for field in ("provider", "best_tier", "n_members", "sources", "latest_utc"):
                assert summary[field] == "", (field, summary[field])
    finally:
        ctx.db.close()


