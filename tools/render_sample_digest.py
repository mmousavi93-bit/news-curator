"""Render a sample Persian digest through the REAL compose + rank stages
(real settings.yaml, real labels, real Jalali) so the owner can review the
output shape locally before anything ships. Synthetic events only; no
network, no LLM, no DB. Delete after the fix ships."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from agent.collectors.base import Item
from agent.config import Config
from agent.memory.event_models import Event
from agent.pipeline.cluster import Cluster
from agent.pipeline.compose import ComposeStage
from agent.settings import Settings

NOW = datetime(2026, 8, 30, 10, 45, tzinfo=timezone.utc)  # 14:15 Tehran
FA = "summary-text"

settings = Settings.from_dict(
    yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config" / "settings.yaml")
        .read_text(encoding="utf-8")
    )
)
cred = {
    "reuters_gnews": type("E", (), {"tier": 1, "group": "wire_west"})(),
    "haaretz": type("E", (), {"tier": 2, "group": "israeli_press"})(),
    "irna": type("E", (), {"tier": 3, "group": "irna"})(),
    "tasnim": type("E", (), {"tier": 3, "group": "irgc_media"})(),
}


def _item(sid, url, when):
    return Item(source_id=sid, url=url, title="t", body="b", published_at=when,
                lang=FA, raw_hash=url.replace("/", "")[:8])


def _cluster(*members):
    cluster = Cluster(key="")
    for m in members:
        cluster.add(m, [1.0])
    return cluster


# 1) Fresh military event, two independent groups, tier 1+2 -> likely.
c1 = _cluster(
    _item("reuters_gnews", "https://x/a", NOW.replace(minute=20)),
    _item("haaretz", "https://x/b", NOW.replace(minute=22)),
)
e1 = Event(
    event_key=c1.key, category="military", claim_status="likely",
    independent_count=2,
    headline="حمله به یک کاروان نظامی در مرز سوریه و عراق؛ تلفات گزارش شده",
    summary="دو منبع مستقل حمله بامدادی به یک کاروان را تأیید میکنند؛ "
            "شمار تلفات قطعی اعلام نشده است.",
    source_count=2, first_seen_at=NOW, last_updated_at=NOW,
)
# 2) Fresh military RUMOUR from tier-3 only -> labelled شایعه, still above
# min_score (6 + recency 3 = 9 >= 8) per the settings comment.
c2 = _cluster(_item("tasnim", "https://x/c", NOW.replace(minute=50)))
e2 = Event(
    event_key=c2.key, category="military", claim_status="rumour",
    independent_count=0,
    headline="ادعای توقف موقت پروازها در فرودگاه مهرآباد",
    summary="فقط یک منبع داخلی مدعی توقف چند ساعته پروازها شده است؛ "
            "هیچ منبع مستقل دیگری آن را تأیید نکرده.",
    source_count=1, first_seen_at=NOW, last_updated_at=NOW,
)
# 3) Economy rumour, tier 3, fresh -> 2 + 3 = 5 < 8 -> dropped by min_score.
c3 = _cluster(_item("irna", "https://x/d", NOW.replace(minute=10)))
e3 = Event(
    event_key=c3.key, category="economy", claim_status="rumour",
    independent_count=0,
    headline="گزارشی از تغییر نرخ ارز رسمی",
    summary="یک منبع داخلی از تغییر احتمالی نرخ رسمی ارز نوشته است.",
    source_count=1, first_seen_at=NOW, last_updated_at=NOW,
)


class _Ctx:
    pass


ctx = _Ctx()
ctx.config = Config(settings=settings, credibility={})
ctx.events = [e1, e2, e3]
ctx.clusters = [c1, c2, c3]
ctx.now = NOW
ctx.counters = {}
ctx.llm_failed = False
ctx.daily_digest = True
ctx.lead_events = []
ctx.leads_channel_id = None

ComposeStage(logging.getLogger("sample")).run(ctx)
print("---- rendered digest (real stages, synthetic events) ----")
for msg in ctx.messages:
    print(msg)
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
print("kept:", ctx.counters.get("compose"), "of", len(ctx.events), "events")
