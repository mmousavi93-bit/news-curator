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
from agent.pipeline.relevance import validate_relevance
from agent.settings import Settings

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
_REPO_ROOT = Path(__file__).parent.parent.parent
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
    # The REAL relevance.yaml, like production: the digest-ranker tests must
    # exercise the shipped keyword tiers, not a test-only copy.
    relevance = validate_relevance(
        yaml.safe_load((_REPO_ROOT / "config" / "relevance.yaml").read_text(encoding="utf-8")))
    return Config(settings=settings, relevance=relevance, credibility={
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
    # "Acme" deliberately: entities now feed the relevance scorer
    # (2026-08-30), and the old default ("Iran") would silently give every
    # test event the iran_direct +8 bonus.
    return Event(event_key="k" * 16, summary=summary, entities=("Acme",),
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


def _with_flash_age(value):
    """os.environ set/restore -- the shim has no monkeypatch fixture and the
    watchdog reads the workflow-exported age from the environment."""
    import os
    from agent.pipeline.flash_watchdog import ENV_AGE
    previous = os.environ.get(ENV_AGE)
    os.environ[ENV_AGE] = value
    def restore():
        if previous is None:
            os.environ.pop(ENV_AGE, None)
        else:
            os.environ[ENV_AGE] = previous
    return restore


def test_flash_watchdog_warning_rides_above_the_digest_header():
    # 9r: the flash monitor's liveness line must reach the delivered message,
    # inside the character budget (constraint 8), above the header -- it is a
    # statement about the system, not the news.
    restore = _with_flash_age("400")
    try:
        ctx = _Ctx(config=_config())
        ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی اسرائیل."))]
        ComposeStage(_Log()).run(ctx)
    finally:
        restore()
    assert "پایش هشدار فوری" in ctx.messages[0]
    assert ctx.messages[0].index("پایش هشدار فوری") < ctx.messages[0].index("مرور اخبار")
    assert len(ctx.messages[0]) <= 4096


def test_flash_watchdog_warning_rides_the_honest_one_liner_too():
    # The case that matters most: a quiet run says "nothing new", which is
    # indistinguishable from a dead system unless the warning is on it.
    restore = _with_flash_age("400")
    try:
        ctx = _Ctx(config=_config())
        ComposeStage(_Log()).run(ctx)
    finally:
        restore()
    assert "پایش هشدار فوری" in ctx.messages[0]
    assert NOTHING_NEW_FA in ctx.messages[0]


def test_healthy_flash_monitor_adds_nothing_to_the_digest():
    restore = _with_flash_age("40")
    try:
        ctx = _Ctx(config=_config())
        ComposeStage(_Log()).run(ctx)
    finally:
        restore()
    assert ctx.messages == [NOTHING_NEW_FA]


def test_header_is_persian_with_jalali_date_and_tehran():
    ctx = _Ctx(config=_config())
    ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی اسرائیل."))]
    ComposeStage(_Log()).run(ctx)
    assert "مرور اخبار" in ctx.messages[0]
    assert "تهران" in ctx.messages[0]
    assert "۱۴۰۵" in ctx.messages[0]  # Jalali year


def test_digest_marker_only_when_flagged():
    ctx = _Ctx(config=_config(), daily_digest=True)
    ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی اسرائیل."))]
    ComposeStage(_Log()).run(ctx)
    assert "مرور روزانه" in ctx.messages[0]
    ctx2 = _Ctx(config=_config(), daily_digest=False)
    ctx2.events = [_with_cluster(ctx2, _event("خلاصه نظامی اسرائیل."))]
    ComposeStage(_Log()).run(ctx2)
    assert "مرور روزانه" not in ctx2.messages[0]


# -- raw fallback (move 1, 2026-08-31): the product survives LLM loss --


def _raw_cluster(ctx: _Ctx, title: str) -> str:
    cluster = Cluster(key="")
    cluster.add(Item(source_id="t3", url=f"https://x/raw/{len(ctx.clusters)}",
                     title=title, body="b", published_at=NOW, lang="en",
                     raw_hash="h" * 8), [1.0])
    ctx.clusters.append(cluster)
    return cluster.key


def test_llm_failed_run_includes_raw_fallback_count_not_titles():
    # Session 9s: the raw-title section is gone -- the 22:56Z escalation
    # run dumped untranslated English and Arabic headlines into both
    # Persian messages. The honest replacement is a COUNT of uncovered
    # stories, which is a fact (constraint 11). Raw titles never reach
    # output.
    ctx = _Ctx(config=_config())
    ctx.llm_failed = True
    key1 = _raw_cluster(ctx, "انفجار در تهران & کرج گزارش شد")
    key2 = _raw_cluster(ctx, "Iranian vessels repositioned near Hormuz")
    ctx.cluster_fates = [(key1, "unavailable"), (key2, "unavailable")]
    ComposeStage(_Log()).run(ctx)
    message = ctx.messages[0]
    assert "هوش مصنوعی" in message
    assert "۲ خبر بدون خلاصه ماند" in message  # Persian-digit count
    assert "Hormuz" not in message
    assert "انفجار در تهران" not in message


def test_fallback_counts_uncovered_not_judged_clusters():
    ctx = _Ctx(config=_config())
    ctx.llm_failed = True
    key_clickbait = _raw_cluster(ctx, "کلیک‌بیت خالص")
    key_irrelevant = _raw_cluster(ctx, "فستیوال مو قرمز هلند")
    key_uncovered = _raw_cluster(ctx, "حمله آمریکا به لارک")
    ctx.cluster_fates = [(key_clickbait, "clickbait"),
                         (key_irrelevant, "irrelevant"),
                         (key_uncovered, "unavailable")]
    ComposeStage(_Log()).run(ctx)
    message = ctx.messages[0]
    assert "۱ خبر بدون خلاصه ماند" in message  # only the uncovered one
    assert "حمله آمریکا به لارک" not in message
    assert "کلیک" not in message
    assert "مو قرمز" not in message


def test_fallback_counts_cap_refused_and_fatal_fates():
    # 2026-09-05 review: _UNCOVERED_FATES listed "refused_cap"/"lang_dropped"
    # but understand.py writes "cap_refused"/"fatal" -- cap-exhausted and
    # fatal clusters therefore never surfaced in the fallback. Pinned.
    # Session 9s: "surfaced" now means COUNTED, not title-listed.
    ctx = _Ctx(config=_config())
    ctx.llm_failed = True
    key_cap = _raw_cluster(ctx, "خبر نپوشیده با اتمام سهمیه")
    key_fatal = _raw_cluster(ctx, "خبر نپوشیده با خطای مرگبار")
    ctx.cluster_fates = [(key_cap, "cap_refused"), (key_fatal, "fatal")]
    ComposeStage(_Log()).run(ctx)
    message = ctx.messages[0]
    assert "۲ خبر بدون خلاصه ماند" in message


def test_fallback_absent_when_everything_covered():
    ctx = _Ctx(config=_config())
    ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی اسرائیل."))]
    ComposeStage(_Log()).run(ctx)
    assert "بدون خلاصه" not in ctx.messages[0]


def test_kept_path_appends_fallback_count_as_footer():
    ctx = _Ctx(config=_config())
    ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی اسرائیل."))]
    key = _raw_cluster(ctx, "پدافند در تنگه هرمز فعال شد")
    ctx.cluster_fates = [(key, "unavailable")]
    ComposeStage(_Log()).run(ctx)
    assert "۱ خبر بدون خلاصه ماند" in ctx.messages[0]
    assert "پدافند در تنگه هرمز" not in ctx.messages[0]


def test_pezeshkian_sco_trip_passes_relevance_gate():
    # Regression for the 2026-08-31 over-cut: the Iranian president's
    # summit trip gated out because neither «ایران» nor his name was in
    # the relevance keywords (the Masafer Yatta disease). His name now
    # sits in iran_direct.
    ctx = _Ctx(config=_config())
    event = _with_cluster(ctx, _event(
        "بزشکیان برای شرکت در نشست‌های سازمان شانگهای به قرقیزستان سفر کرد",
        category="politics"))
    ctx.events = [event]
    ComposeStage(_Log()).run(ctx)
    assert event.event_key not in {e.event_key for e in ctx.relevance_dropped}


def test_generic_military_noun_without_anchor_drops():
    # 2026-09-05 regression: the Greek F-4 airshow crash ("جنگنده") and the
    # Pentagon polygraph story ("تسلیحات") LED the digest on the generic
    # military nouns alone. Strategic is anchor-only now: an unplaced
    # "fighter jet"/"arms" is off-mission and must not pass the gate.
    ctx = _Ctx(config=_config())
    event = _with_cluster(ctx, _event(
        "سقوط یک جنگنده در نمایش هوایی.", category="military"))
    ctx.events = [event]
    ComposeStage(_Log()).run(ctx)
    assert event.event_key in {e.event_key for e in ctx.relevance_dropped}


def test_regional_anchor_military_event_passes_relevance_gate():
    ctx = _Ctx(config=_config())
    event = _with_cluster(ctx, _event(
        "حمله اسرائیل به مواضعی در لبنان.", category="military"))
    ctx.events = [event]
    ComposeStage(_Log()).run(ctx)
    assert event.event_key not in {e.event_key for e in ctx.relevance_dropped}


def test_fallback_counts_untitled_cluster_too():
    # Session 9s: the raw-title section (and its empty-bullet defect for
    # untitled Telegram posts, owner 2026-08-31) is gone; the count line
    # has no titles to be empty. An untitled cluster still COUNTS.
    ctx = _Ctx(config=_config())
    ctx.llm_failed = True
    ctx.cluster_fates = []
    cluster = Cluster(key="")
    cluster.add(Item(source_id="tg_x", url="https://x/raw/0", title="",
                     body="پست تلگرامی بدون عنوان درباره انفجار تهران",
                     published_at=NOW, lang="fa", raw_hash="h" * 8), [1.0])
    ctx.clusters.append(cluster)
    ctx.cluster_fates.append((cluster.key, "unavailable"))
    ComposeStage(_Log()).run(ctx)
    assert "۱ خبر بدون خلاصه ماند" in ctx.messages[0]
    assert "•" not in ctx.messages[0]


def test_fallback_counts_every_uncovered_cluster_no_cap():
    # Session 9s: the old raw-title section capped at fallback_max_items
    # (5 bullets). A count needs no cap -- and truncating a count would
    # be a lie about how many stories went uncovered.
    ctx = _Ctx(config=_config())
    ctx.llm_failed = True
    ctx.cluster_fates = []
    for i in range(6):
        key = _raw_cluster(ctx, f"خبر پوشش‌داده‌نشده شماره {i}")
        ctx.cluster_fates.append((key, "unavailable"))
    ComposeStage(_Log()).run(ctx)
    assert "۶ خبر بدون خلاصه ماند" in ctx.messages[0]
    assert "•" not in ctx.messages[0]


def test_importance_order_military_before_economy():
    ctx = _Ctx(config=_config())
    economy = _event("خلاصه اقتصادی نفت.", category="economy")
    military = _event("خلاصه نظامی اسرائیل.", category="military")
    ctx.events = [
        _with_cluster(ctx, economy),
        _with_cluster(ctx, military),
    ]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert text.index("خلاصه نظامی") < text.index("خلاصه اقتصادی")


def test_military_rumour_survives_threshold_with_shaye_label():
    # Strategic military rumour from a tier-3 channel: the relevance tier
    # (regional anchor -> strategic 4) leads, so 4+2+0+3 = 9 >= min_score. A military
    # rumour WITHOUT a strategic keyword now drops -- the owner's 2026-08-30
    # relevance-first decision demoted category from 6 to 2.
    # Softer-category rumours (politics/security) score below and drop --
    # also asserted below.
    ctx = _Ctx(config=_config())
    event = _event("خلاصه نظامی اسرائیل تئییدنشده.", category="military",
                   independent=0, claim_status="rumour")
    ctx.events = [_with_cluster(ctx, event, source_id="t3")]
    ComposeStage(_Log()).run(ctx)
    assert "شایعه" in ctx.messages[0]


def test_single_source_event_renders_unconfirmed_marker():
    # 2026-09-05 fix 2: a kept single-source (unconfirmed) event is marked
    # «تک‌منبع» (single source), NOT «شایعه» -- the grade means "fewer than
    # 2 independent sources", and the label states the fact without inflating
    # confidence (constraint 10). Owner picked «تک‌منبع» over «تأییدنشده» /
    # «نسبتا تایید شده» 2026-09-05.
    from agent.pipeline.labels import labels_for
    ctx = _Ctx(config=_config())
    event = _event("خلاصه نظامی اسرائیل.", category="military",
                   independent=1, claim_status="unconfirmed")
    ctx.events = [_with_cluster(ctx, event, source_id="t2")]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert labels_for("fa")["unconfirmed"] in text
    assert labels_for("fa")["rumour"] not in text


def test_soft_category_rumour_drops_below_threshold():
    ctx = _Ctx(config=_config())
    event = _event("شایعه سیاسی.", category="politics",
                   independent=0, claim_status="rumour")
    ctx.events = [_with_cluster(ctx, event, source_id="t3")]
    ComposeStage(_Log()).run(ctx)
    assert ctx.messages == [NOTHING_NEW_FA]  # 3+0+0+3 = 6 < 8


def test_date_only_cluster_says_time_not_stated():
    ctx = _Ctx(config=_config())
    event = _event("خلاصه نظامی اسرائیل.", category="security")
    ctx.events = [_with_cluster(ctx, event, date_only=True)]
    ComposeStage(_Log()).run(ctx)
    assert "زمان اعلام نشده" in ctx.messages[0]
    assert "۰۳:۳۰" not in ctx.messages[0]  # midnight placeholder never rendered


def test_llm_headline_is_the_title_summary_is_detail():
    ctx = _Ctx(config=_config())
    event = _event("جزئیات تکمیلی نظامی ماجرا.", category="security")
    event = Event(event_key="h" * 16, summary=event.summary,
                  headline="تیتر اطلاع‌رسان اصلی", category="security",
                  independent_count=1, source_count=2,
                  first_seen_at=NOW, last_updated_at=NOW)
    ctx.events = [_with_cluster(ctx, event)]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert "تیتر اطلاع‌رسان اصلی" in text  # the LLM headline is the title
    assert "جزئیات تکمیلی نظامی ماجرا" in text   # the summary is the detail


def test_category_icons_render():
    ctx = _Ctx(config=_config())
    ctx.events = [
        _with_cluster(ctx, _event("خلاصه نظامی اسرائیل.", category="military")),
        _with_cluster(ctx, _event("خلاصه سیاسی اسرائیل.", category="politics")),
    ]
    ComposeStage(_Log()).run(ctx)
    assert "⚔️" in ctx.messages[0]
    assert "🏛️" in ctx.messages[0]


def test_multi_source_event_renders_corroboration_count():
    # 9v: an event corroborated by >=2 independent sources shows a factual
    # «تأیید از N منبع» count. The reader weighing rumour-vs-fact needs the
    # strength signal, not just the binary claim marker (constraint 11).
    from agent.pipeline.labels import labels_for
    ctx = _Ctx(config=_config())
    event = _event("خلاصه نظامی اسرائیل تأییدشده.", category="military",
                   independent=3, claim_status="likely")
    ctx.events = [_with_cluster(ctx, event)]
    ComposeStage(_Log()).run(ctx)
    assert labels_for("fa")["sources_count"].format(count="۳") in ctx.messages[0]


def test_single_source_event_omits_corroboration_count():
    # 9v: independent_count == 1 means the event is already marked «تک‌منبع»;
    # a "confirmed by 1 source" count would contradict that. No count line.
    ctx = _Ctx(config=_config())
    event = _event("خلاصه نظامی اسرائیل.", category="military",
                   independent=1, claim_status="unconfirmed")
    ctx.events = [_with_cluster(ctx, event)]
    ComposeStage(_Log()).run(ctx)
    assert "تأیید از" not in ctx.messages[0]


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
        event = _event(f"خبر شماره {i}. نظامی اسرائیل " + "جزئیات " * 40, category="security")
        ctx.events.append(_with_cluster(ctx, event))
    ComposeStage(_Log()).run(ctx)
    assert 1 < len(ctx.messages) <= 6  # max_messages is the safety valve (2026-08-30)
    for text in ctx.messages:
        assert len(text.encode("utf-16-le")) // 2 <= 4096


def test_non_persian_event_dropped_others_kept():
    # Live-sample regression (2026-08-30): an Arabic-source cluster came
    # back fully Arabic. The gate drops it; the Persian event ships.
    ctx = _Ctx(config=_config())
    persian = _event("خلاصه نظامی اسرائیل.", category="military")
    arabic = _event("يك خلاصة جنگ.", category="military")
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
    # 2026-09-05 fix 1: the lang-dropped event's raw title is ALSO shown
    # (the fallback footer), so the owner sees what was collected even
    # though none of it rendered Persian -- it previously vanished.
    from agent.pipeline.labels import labels_for
    ctx = _Ctx(config=_config())
    arabic = _event("يك خلاصة جنگ.", category="military")
    ctx.events = [_with_cluster(ctx, arabic, source_id="t1")]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert text.startswith(labels_for("fa")["lang_dropped"])
    # Session 9s: the fallback is a count line, not a raw-title list.
    assert "۱ خبر بدون خلاصه ماند" in text
    assert ctx.counters["compose_lang_drops"] == 1
    assert ctx.compose_kept_keys == []


def test_kept_events_recorded_for_delivery():
    # Received-marker keys: compose records kept keys after the rank cut;
    # the DELIVER stage writes the markers, only after real sends succeed
    # (review finding 2026-08-30 -- marking here would re-create ghost
    # suppression on send failure).
    ctx = _Ctx(config=_config())
    ctx.events = [_with_cluster(ctx, _event("خلاصه نظامی اسرائیل.", category="military"))]
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


def test_follow_up_event_renders_as_compact_line_not_full_entry():
    # Fix 1, 2026-09-06: a repeat-gate bypass (validate.py's follow_up flag)
    # ships as ONE compact "پیگیری · headline" line -- no category icon, no
    # claim-status label, no detail/summary -- never a full entry ("a
    # change that produces more output is probably wrong").
    from dataclasses import replace

    from agent.pipeline.labels import labels_for

    ctx = _Ctx(config=_config())
    normal = _with_cluster(ctx, _event("خلاصه نظامی اسرائیل.", category="military"))
    # Summary carries "اسرائیل" so it clears the real relevance gate like
    # every other event in this file -- the point under test is compact
    # rendering, not relevance scoring.
    base = _event(
        "این خلاصه کامل پیگیری اسرائیل است که نباید به عنوان جزئیات نمایش داده شود.",
        category="military",
    )
    base = Event(event_key=base.event_key, summary=base.summary,
                headline="تیتر پیگیری کوتاه اسرائیل", entities=base.entities,
                category=base.category, independent_count=base.independent_count,
                claim_status=base.claim_status, source_count=base.source_count,
                first_seen_at=base.first_seen_at, last_updated_at=base.last_updated_at)
    # source_id="t1" (distinct from the normal event's default "t2") so the
    # two single-member clusters get distinct keys -- no url collision.
    followup_event = _with_cluster(ctx, base, source_id="t1")
    # HIGH band (same story retold): compact line. round-4 review, fix 1 --
    # follow_up alone no longer implies compact rendering, only
    # follow_up_high does (see test_mid_band_follow_up_renders_as_full_entry
    # for the MID-band contrast).
    followup_event = replace(followup_event, follow_up=True, follow_up_high=True)
    ctx.events = [normal, followup_event]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert labels_for("fa")["follow_up"] in text
    assert "تیتر پیگیری کوتاه اسرائیل" in text
    assert "این خلاصه کامل پیگیری اسرائیل است که نباید" not in text


def test_mid_band_follow_up_renders_as_full_entry_above_lower_scoring_normal():
    # Round-4 review, fix 1: a MID-band survivor (follow_up=True,
    # follow_up_high=False) is a related but DIFFERENT story
    # (repeat_decision.py's own docstring), not a retelling -- it must
    # render as a FULL entry, sorted by its OWN importance score like any
    # other event, never pinned below every normal entry. Score
    # construction: military/tier1/indep3 = 6+6+3+3+0 = 18 for the MID
    # event vs politics/tier2/indep1 = 3+2+2+3+0 = 10 for the normal one --
    # the MID event outscores the normal one and must render FIRST, detail
    # line intact. Before this fix, EVERY follow_up (both bands) was
    # pinned at worst_normal_priority with no detail -- this exact
    # scenario would have put the day's highest-scoring story at the
    # bottom as a headline-only line.
    from dataclasses import replace

    ctx = _Ctx(config=_config())
    normal_summary = "این خلاصه سیاسی عادی است اسرائیل."
    normal = _with_cluster(
        ctx, _event(normal_summary, category="politics", independent=1),
        source_id="t2",
    )
    mid_base = _event(
        "این خلاصه میانباند باید کامل نمایش داده شود اسرائیل.",
        category="military", independent=3, claim_status="likely",
    )
    mid_base = Event(
        event_key=mid_base.event_key, summary=mid_base.summary,
        headline="تیتر داستان میانباند اسرائیل", entities=mid_base.entities,
        category=mid_base.category, independent_count=mid_base.independent_count,
        claim_status=mid_base.claim_status, source_count=mid_base.source_count,
        first_seen_at=mid_base.first_seen_at, last_updated_at=mid_base.last_updated_at,
    )
    mid = _with_cluster(ctx, mid_base, source_id="t1")
    mid = replace(mid, follow_up=True, follow_up_high=False)
    ctx.events = [normal, mid]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert "این خلاصه میانباند باید کامل نمایش داده شود" in text  # detail survives
    assert text.index("تیتر داستان میانباند اسرائیل") < text.index("این خلاصه سیاسی عادی است")


def test_high_band_follow_up_renders_below_every_normal_entry_regardless_of_score():
    # Round-4 review, fix 1: the HIGH band is the compact one-liner, pinned
    # below every normal entry (worst priority) even when its own
    # importance score would otherwise sort it first -- the "DO NOT FIX"
    # contract (repeat_decision.py: HIGH band is the SAME story retold,
    # the reader is not owed a second full entry for it). Same score
    # construction as the MID-band test above (18 vs 10) to prove the
    # pin-to-bottom survives even when the HIGH-band item would otherwise
    # rank first.
    from dataclasses import replace

    ctx = _Ctx(config=_config())
    normal_summary = "این خلاصه سیاسی عادی است اسرائیل."
    normal = _with_cluster(
        ctx, _event(normal_summary, category="politics", independent=1),
        source_id="t2",
    )
    high_base = _event(
        "این خلاصه هرگز نباید کامل نمایش داده شود اسرائیل.",
        category="military", independent=3, claim_status="likely",
    )
    high_base = Event(
        event_key=high_base.event_key, summary=high_base.summary,
        headline="تیتر باند بالا اسرائیل", entities=high_base.entities,
        category=high_base.category, independent_count=high_base.independent_count,
        claim_status=high_base.claim_status, source_count=high_base.source_count,
        first_seen_at=high_base.first_seen_at, last_updated_at=high_base.last_updated_at,
    )
    high = _with_cluster(ctx, high_base, source_id="t1")
    high = replace(high, follow_up=True, follow_up_high=True)
    ctx.events = [normal, high]
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert "تیتر باند بالا اسرائیل" in text
    assert "این خلاصه هرگز نباید کامل نمایش داده شود" not in text  # detail dropped
    assert text.index("این خلاصه سیاسی عادی است") < text.index("تیتر باند بالا اسرائیل")


def test_full_entry_survives_tight_budget_over_a_lower_priority_followup():
    # Fix C, 2026-09-06 review: the old follow-up priority was
    # `max(normal_count - 1, 0)` -- the SAME priority as the last normal
    # entry -- so budget.py's (priority, order) tie-break let a follow-up
    # that scores MORE important than a normal entry (and therefore sits
    # earlier in `kept`, with a lower `order`) win the greedy-pack race and
    # starve a lower-ranked, never-before-delivered normal story of its
    # budget. Fix: follow-up priority = normal_count, strictly worse than
    # every normal entry (0..normal_count-1) regardless of importance order.
    #
    # The normal event's summary is TWO sentences on purpose: `_headline()`
    # only takes the first, so the second sentence is proof the FULL body
    # (not a headline-only degrade) made it into the message.
    from dataclasses import replace as dc_replace

    from agent.delivery.budget import utf16_len

    normal_summary = (
        "این خلاصه کوتاه است. "
        "این بخش اضافی فقط در نسخه کامل دیده میشود اسرائیل."
    )
    DETAIL_ONLY_TEXT = "این بخش اضافی فقط در نسخه کامل دیده میشود"

    # Calibration: render with ONLY the normal event at a generous budget to
    # measure its exact rendered size -- the tight budget below is set from
    # real output, not a hand-counted guess.
    baseline_ctx = _Ctx(config=_config())
    normal_baseline = _with_cluster(
        baseline_ctx, _event(normal_summary, category="politics", independent=1),
        source_id="t2",
    )
    baseline_ctx.events = [normal_baseline]
    ComposeStage(_Log()).run(baseline_ctx)
    baseline_units = utf16_len(baseline_ctx.messages[0])

    # score: politics(3) + corrob 2*min(1,3)=2 + tier2_bonus(2) + recency(3,
    # fresh) + size(0) = 10 -- clears min_score(8), so it reaches `kept`.
    ctx = _Ctx(config=_config())
    normal = _with_cluster(
        ctx, _event(normal_summary, category="politics", independent=1),
        source_id="t2",
    )
    followup_base = _event(
        "این متن پیگیری هرگز نباید در بودجه فشرده دیده شود اسرائیل.",
        category="military", independent=3,
    )
    followup_base = Event(
        event_key=followup_base.event_key, summary=followup_base.summary,
        headline="تیتر پیگیری که باید حذف شود اسرائیل",
        entities=followup_base.entities, category=followup_base.category,
        independent_count=followup_base.independent_count,
        claim_status=followup_base.claim_status,
        source_count=followup_base.source_count,
        first_seen_at=followup_base.first_seen_at,
        last_updated_at=followup_base.last_updated_at,
    )
    # score: military(6) + corrob 2*min(3,3)=6 + tier1_bonus(3) + recency(3)
    # + size(0) = 18 -- HIGHER than the normal event's 10, so it sorts
    # BEFORE the normal event in `kept` (lower `order`) -- exactly the
    # ordering that let the old tied-priority bug win the tie-break.
    followup = _with_cluster(ctx, followup_base, source_id="t1")
    # HIGH band: this test is specifically about the compact-line /
    # worst-priority behavior, which round-4 review fix 1 scoped to
    # follow_up_high only.
    followup = dc_replace(followup, follow_up=True, follow_up_high=True)
    ctx.events = [normal, followup]

    # Tight budget: enough for the normal entry's full text plus the
    # dropped-item overflow marker room budget.py always reserves, but far
    # short of also fitting the follow-up's own compact line.
    marker_reserve = utf16_len("\n\n… +2 more")
    tight_units = baseline_units + marker_reserve + 5
    ctx.config = dc_replace(
        ctx.config,
        settings=dc_replace(
            ctx.config.settings,
            delivery=dc_replace(
                ctx.config.settings.delivery, telegram_max_chars=tight_units,
            ),
            digest_rank=dc_replace(
                ctx.config.settings.digest_rank, max_messages=1,
            ),
        ),
    )
    ComposeStage(_Log()).run(ctx)
    text = ctx.messages[0]
    assert DETAIL_ONLY_TEXT in text
    assert "تیتر پیگیری که باید حذف شود" not in text


def test_escalation_run_character_budget_holds_for_full_mid_band_entries():
    # Round-4 review, fix 1: MID-band survivors now render as FULL entries
    # (detail line included) instead of the old one-liner -- a busier day
    # spends more characters per follow-up than before. Confirm the budget
    # math rather than assert it: an escalation run with ~18 full entries
    # (15 normal + 3 MID-band survivors, ~300 chars each including icon/
    # category/detail) must fit well inside max_messages(6) * telegram_max_
    # chars(4096) = 24,576 units and not need the truncation path at all.
    from dataclasses import replace

    from agent.delivery.budget import utf16_len

    ctx = _Ctx(config=_config())
    for i in range(15):
        event = _with_cluster(
            ctx,
            _event(f"خبر شماره {i} اسرائیل. " + "جزئیات رویداد امنیتی منطقه‌ای " * 6,
                  category="security"),
            source_id=f"n{i}",
        )
        ctx.events.append(event)
    for i in range(3):
        mid_base = _event(
            f"این خلاصه میانباند شماره {i} اسرائیل. " + "توسعه تازه در میدان نبرد " * 6,
            category="military", independent=3, claim_status="likely",
        )
        mid_base = Event(
            event_key=mid_base.event_key, summary=mid_base.summary,
            headline=f"تیتر میانباند {i} اسرائیل", entities=mid_base.entities,
            category=mid_base.category, independent_count=mid_base.independent_count,
            claim_status=mid_base.claim_status, source_count=mid_base.source_count,
            first_seen_at=mid_base.first_seen_at, last_updated_at=mid_base.last_updated_at,
        )
        mid = _with_cluster(ctx, mid_base, source_id=f"m{i}")
        mid = replace(mid, follow_up=True, follow_up_high=False)
        ctx.events.append(mid)

    ComposeStage(_Log()).run(ctx)

    total_units = sum(utf16_len(text) for text in ctx.messages)
    max_budget = 6 * 4096  # settings_minimal.yaml: max_messages=6, telegram_max_chars=4096
    # The brief's own estimate (~14-20 full entries at ~300 chars each =
    # ~4,200-6,000 chars) -- 18 entries here, so the measured total must
    # land inside a generously wide band around that estimate and, more
    # importantly, well under the 24,576-unit ceiling with room to spare.
    assert 3000 <= total_units <= 12000
    assert total_units < max_budget
    assert len(ctx.messages) <= 6
    assert getattr(ctx, "compose_truncated", []) == []  # nothing cut


def test_truncated_followup_is_absent_from_the_delivered_key_set():
    # Fix 3, round-2 review: the OLD unconditional
    # `ctx.compose_kept_keys = [e.event_key for e in kept]` marked every
    # RANKED event delivered regardless of whether the character budget
    # actually rendered it -- a follow-up cut entirely by a tight budget
    # (exactly the scenario in test_full_entry_survives_tight_budget_over_a_
    # lower_priority_followup above) would have been marked delivered unseen
    # AND permanently raised its story's high-water mark, silently
    # suppressing a real development for the rest of the 72h repeat window.
    # Same calibration pattern as that test: measure the normal entry's real
    # rendered size, then set a budget that fits it plus the drop-marker
    # room but not the follow-up's own compact line.
    from dataclasses import replace as dc_replace

    from agent.delivery.budget import utf16_len

    normal_summary = (
        "این خلاصه کوتاه است. "
        "این بخش اضافی فقط در نسخه کامل دیده میشود اسرائیل."
    )

    baseline_ctx = _Ctx(config=_config())
    normal_baseline = _with_cluster(
        baseline_ctx, _event(normal_summary, category="politics", independent=1),
        source_id="t2",
    )
    baseline_ctx.events = [normal_baseline]
    ComposeStage(_Log()).run(baseline_ctx)
    baseline_units = utf16_len(baseline_ctx.messages[0])

    ctx = _Ctx(config=_config())
    normal = _with_cluster(
        ctx, _event(normal_summary, category="politics", independent=1),
        source_id="t2",
    )
    followup_base = _event(
        "این متن پیگیری هرگز نباید در بودجه فشرده دیده شود اسرائیل.",
        category="military", independent=3,
    )
    followup_base = Event(
        event_key=followup_base.event_key, summary=followup_base.summary,
        headline="تیتر پیگیری که باید حذف شود اسرائیل",
        entities=followup_base.entities, category=followup_base.category,
        independent_count=followup_base.independent_count,
        claim_status=followup_base.claim_status,
        source_count=followup_base.source_count,
        first_seen_at=followup_base.first_seen_at,
        last_updated_at=followup_base.last_updated_at,
    )
    followup = _with_cluster(ctx, followup_base, source_id="t1")
    followup = dc_replace(followup, follow_up=True, follow_up_high=True)
    ctx.events = [normal, followup]

    marker_reserve = utf16_len("\n\n… +2 more")
    tight_units = baseline_units + marker_reserve + 5
    ctx.config = dc_replace(
        ctx.config,
        settings=dc_replace(
            ctx.config.settings,
            delivery=dc_replace(
                ctx.config.settings.delivery, telegram_max_chars=tight_units,
            ),
            digest_rank=dc_replace(
                ctx.config.settings.digest_rank, max_messages=1,
            ),
        ),
    )
    ComposeStage(_Log()).run(ctx)
    # Sanity: reproduces the same cut as the sibling test above.
    assert "تیتر پیگیری که باید حذف شود" not in ctx.messages[0]
    assert followup.event_key not in ctx.compose_kept_keys
    assert normal.event_key in ctx.compose_kept_keys
    assert [e.event_key for e in ctx.compose_truncated] == [followup.event_key]
