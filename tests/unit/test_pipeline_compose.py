"""Unit tests for pipeline/compose.py: ranked, Persian, multi-message
digests (owner output contract 2026-08-29), the honest one-liners, the
شایعه label, date_only handling and the char ceiling.

Every event carries its cluster (as in production): without one, the
ranker has no tier/recency signal and most test events would fall below
min_score -- which is itself correct behaviour, tested separately."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.pipeline.compose import ComposeStage
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

NOTHING_NEW_FA = "چیز تازهای نسبت به اجرای قبلی نیامده."


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


def _config() -> Config:
    settings = Settings.from_dict(yaml.safe_load(_FIXTURE.read_text(encoding="utf-8")))
    return Config(settings=settings, credibility={
        "t1": SourceCredibility(tier=1, group="g1"),
        "t2": SourceCredibility(tier=2, group="g2"),
        "t3": SourceCredibility(tier=3, group="g3"),
    })


@dataclass
class _Ctx:
    config: Config
    events: list = field(default_factory=list)
    clusters: list = field(default_factory=list)
    now: datetime = NOW
    daily_digest: bool = False
    counters: dict = field(default_factory=dict)


def _member(source_id: str, date_only: bool = False) -> Item:
    return Item(source_id=source_id, url=f"https://x/{source_id}/{date_only}",
                title="t", body="b", published_at=NOW, lang="en",
                raw_hash="h" * 8, date_only=date_only)


def _with_cluster(ctx: _Ctx, event: Event, source_id: str = "t2",
                  date_only: bool = False) -> Event:
    """Attach a cluster (like production always has) and return the event."""
    cluster = Cluster(key="")
    cluster.add(_member(source_id, date_only=date_only), [1.0])
    ctx.clusters.append(cluster)
    return Event(event_key=cluster.key, summary=event.summary,
                 headline=event.headline, entities=event.entities,
                 category=event.category, claim_status=event.claim_status,
                 independent_count=event.independent_count,
                 source_count=event.source_count,
                 first_seen_at=event.first_seen_at,
                 last_updated_at=event.last_updated_at)


def _event(summary: str, category: str = "military", independent: int = 1,
           claim_status: str = "unconfirmed") -> Event:
    return Event(event_key="k" * 16, summary=summary, entities=("Iran",),
                 category=category, independent_count=independent,
                 claim_status=claim_status, source_count=2,
                 first_seen_at=NOW, last_updated_at=NOW)


def test_no_events_produces_honest_persian_one_liner():
    ctx = _Ctx(config=_config())
    ComposeStage(_Log()).run(ctx)
    assert ctx.messages == [NOTHING_NEW_FA]
    assert ctx.counters["compose"] == 0


def test_llm_failed_flag_swaps_one_liner_for_ai_unavailable():
    ctx = _Ctx(config=_config())
    ctx.llm_failed = True
    ComposeStage(_Log()).run(ctx)
    assert "هوش مصنوعی" in ctx.messages[0]
    assert NOTHING_NEW_FA not in ctx.messages[0]


def test_header_is_persian_with_jalali_date_and_tehran():
    ctx = _Ctx(config=_config())
    ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی."))]
    ComposeStage(_Log()).run(ctx)
    assert "مرور اخبار" in ctx.messages[0]
    assert "تهران" in ctx.messages[0]
    assert "۱۴۰۵" in ctx.messages[0]  # Jalali year


def test_digest_marker_only_when_flagged():
    ctx = _Ctx(config=_config(), daily_digest=True)
    ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی."))]
    ComposeStage(_Log()).run(ctx)
    assert "مرور روزانه" in ctx.messages[0]
    ctx2 = _Ctx(config=_config(), daily_digest=False)
    ctx2.events = [_with_cluster(ctx2, _event("خلاصه نظامی."))]
    ComposeStage(_Log()).run(ctx2)
    assert "مرور روزانه" not in ctx2.messages[0]


def test_importance_order_military_before_economy():
    ctx = _Ctx(config=_config())
    economy = _event("خلاصه اقتصادی.", category="economy")
    military = _event("خلاصه نظامی.", category="military")
    ctx.events = [
        _with_cluster(ctx, economy),
        _with_cluster(ctx, military),
    ]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert text.index("خلاصه نظامی") < text.index("خلاصه اقتصادی")


def test_military_rumour_survives_threshold_with_shaye_label():
    # Military rumour from a tier-3 channel: 5+0+0+3 = 8 >= min_score.
    # Softer-category rumours (politics/security) score below and drop --
    # also asserted below.
    ctx = _Ctx(config=_config())
    event = _event("خلاصه نظامی تئییدنشده.", category="military",
                   independent=0, claim_status="rumour")
    ctx.events = [_with_cluster(ctx, event, source_id="t3")]
    ComposeStage(_Log()).run(ctx)
    assert "شایعه" in ctx.messages[0]


def test_soft_category_rumour_drops_below_threshold():
    ctx = _Ctx(config=_config())
    event = _event("شایعه سیاسی.", category="politics",
                   independent=0, claim_status="rumour")
    ctx.events = [_with_cluster(ctx, event, source_id="t3")]
    ComposeStage(_Log()).run(ctx)
    assert ctx.messages == [NOTHING_NEW_FA]  # 3+0+0+3 = 6 < 8


def test_date_only_cluster_says_time_not_stated():
    ctx = _Ctx(config=_config())
    event = _event("خلاصه نظامی.", category="security")
    ctx.events = [_with_cluster(ctx, event, date_only=True)]
    ComposeStage(_Log()).run(ctx)
    assert "زمان اعلام نشده" in ctx.messages[0]
    assert "۰۳:۳۰" not in ctx.messages[0]  # midnight placeholder never rendered


def test_llm_headline_is_the_title_summary_is_detail():
    ctx = _Ctx(config=_config())
    event = _event("جزئیات تکمیلی ماجرا.", category="security")
    event = Event(event_key="h" * 16, summary=event.summary,
                  headline="تیتر اطلاع‌رسان اصلی", category="security",
                  independent_count=1, source_count=2,
                  first_seen_at=NOW, last_updated_at=NOW)
    ctx.events = [_with_cluster(ctx, event)]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert "تیتر اطلاع‌رسان اصلی" in text  # the LLM headline is the title
    assert "جزئیات تکمیلی ماجرا" in text   # the summary is the detail


def test_category_icons_render():
    ctx = _Ctx(config=_config())
    ctx.events = [
        _with_cluster(ctx, _event("خلاصه نظامی.", category="military")),
        _with_cluster(ctx, _event("خلاصه سیاسی.", category="politics")),
    ]
    ComposeStage(_Log()).run(ctx)
    assert "⚔️" in ctx.messages[0]
    assert "🏛️" in ctx.messages[0]


def test_below_threshold_events_never_reach_the_message():
    ctx = _Ctx(config=_config())
    event = _event("مطلب غیرمرتبط.", category="other", independent=0)
    ctx.events = [_with_cluster(ctx, event, source_id="t3")]
    ComposeStage(_Log()).run(ctx)
    assert "مطلب غیرمرتبط" not in ctx.messages[0]
    assert ctx.messages == [NOTHING_NEW_FA]  # everything dropped -> honest line


def test_busy_day_splits_into_multiple_messages_within_char_cap():
    ctx = _Ctx(config=_config())
    for i in range(25):
        event = _event(f"خبر شماره {i}. " + "جزئیات " * 40, category="security")
        ctx.events.append(_with_cluster(ctx, event))
    ComposeStage(_Log()).run(ctx)
    assert 1 < len(ctx.messages) <= 3  # owner: more than one message allowed
    for text in ctx.messages:
        assert len(text.encode("utf-16-le")) // 2 <= 4096


def test_non_persian_event_dropped_others_kept():
    # Live-sample regression (2026-08-30): an Arabic-source cluster came
    # back fully Arabic. The gate drops it; the Persian event ships.
    ctx = _Ctx(config=_config())
    persian = _event("خلاصه نظامی.", category="military")
    arabic = _event("يك خلاصة.", category="military")
    ctx.events = [
        _with_cluster(ctx, persian, source_id="t1"),
        _with_cluster(ctx, arabic, source_id="t2"),
    ]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert "خلاصه نظامی" in text
    assert "يك" not in text
    assert ctx.counters["compose_lang_drops"] == 1


def test_all_non_persian_events_produce_lang_dropped_one_liner():
    # Constraint 11: when events existed but none rendered Persian, the
    # message says so -- "nothing new" would be a lie about the world.
    from agent.pipeline.labels import labels_for
    ctx = _Ctx(config=_config())
    arabic = _event("يك خلاصة.", category="military")
    ctx.events = [_with_cluster(ctx, arabic, source_id="t1")]
    ComposeStage(_Log()).run(ctx)
    assert ctx.messages == [labels_for("fa")["lang_dropped"]]
    assert ctx.counters["compose_lang_drops"] == 1
    assert ctx.compose_kept_keys == []


def test_kept_events_recorded_for_delivery():
    # Received-marker keys: compose records kept keys after the rank cut;
    # the DELIVER stage writes the markers, only after real sends succeed
    # (review finding 2026-08-30 -- marking here would re-create ghost
    # suppression on send failure).
    ctx = _Ctx(config=_config())
    ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی.", category="military"))]
    ComposeStage(_Log()).run(ctx)
    assert ctx.compose_kept_keys == [ctx.events[0].event_key]


def test_below_threshold_events_are_never_recorded_for_delivery():
    # The ghost-suppression fix at the compose boundary: an event the owner
    # never saw (below min_score) must not block its own follow-ups, so it
    # must never enter the received-marker keys.
    ctx = _Ctx(config=_config())
    event = _event("شایعه سیاسی.", category="politics",
                   independent=0, claim_status="rumour")
    ctx.events = [_with_cluster(ctx, event, source_id="t3")]
    ComposeStage(_Log()).run(ctx)
    assert ctx.messages == [NOTHING_NEW_FA]  # 3+0+0+3 = 6 < 8
    assert ctx.compose_kept_keys == []


def test_lead_events_never_enter_the_received_marker_keys():
    # schema.sql note: a corroborated confirmation of a lead must reach the
    # main feed, so leads are excluded from the received-marker keys.
    ctx = _Ctx(config=_config())
    lead = Event(event_key="l" * 16, summary="سرنخ نظامی.",
                 category="military", source_count=1,
                 first_seen_at=NOW, last_updated_at=NOW)
    ctx.lead_events = [lead]
    ctx.leads_channel_id = "leads"
    ComposeStage(_Log()).run(ctx)
    assert getattr(ctx, "lead_message", None) is not None
    assert ctx.compose_kept_keys == []


def test_lead_only_run_delivers_lead_message():
    # Fix pinned by name (2026-08-30): a lead-only run -- main events empty
    # because nothing was corroborated -- must still deliver the lead
    # message; that is exactly the scenario the leads channel exists for.
    ctx = _Ctx(config=_config())
    lead = Event(event_key="l" * 16, summary="سرنخ نظامی.",
                 category="military", source_count=1,
                 first_seen_at=NOW, last_updated_at=NOW)
    ctx.lead_events = [lead]
    ctx.leads_channel_id = "leads"
    ComposeStage(_Log()).run(ctx)
    assert ctx.messages == [NOTHING_NEW_FA]  # main feed stays honest
    assert getattr(ctx, "lead_message", None) is not None
