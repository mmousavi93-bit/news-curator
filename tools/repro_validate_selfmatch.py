"""One-off reproduction: does validate's anti-repetition drop an event
against ITSELF? Production sequence: understand inserts this run's events
into the events table, THEN validate reads recent events (72h) from that
same table and cosine-matches new summaries against them. If the new
event's own row is among "recent", self-match = 1.0 >= 0.55 -> dropped.

Exit code 0 = BUG NOT REPRODUCED (event kept). Exit 1 = BUG REPRODUCED
(event dropped as its own repeat). Not part of the suite -- delete after
the fix ships.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from agent.collectors.base import Item
from agent.config import Config
from agent.memory import db as memory_db
from agent.memory.event_models import Event, insert_events, read_recent_events
from agent.pipeline.cluster import Cluster
from agent.pipeline.embed import FakeEmbedder
from agent.pipeline.validate import ValidateStage
from agent.settings import Settings

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

settings = Settings.from_dict(
    yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config" / "settings.yaml")
        .read_text(encoding="utf-8")
    )
)
config = Config(settings=settings, credibility={})
cred = {"t2": type("E", (), {"tier": 2, "group": "g"})()}

tmp = tempfile.mkdtemp()
conn = memory_db.open_db(os.path.join(tmp, "state.db"), create_if_absent=True)

cluster = Cluster(key="k" * 16)
cluster.add(
    Item(source_id="t2", url="https://x/1", title="t", body="b",
         published_at=NOW, lang="en", raw_hash="a" * 8),
    [1.0],
)
event = Event(
    event_key=cluster.key, summary="fresh event summary", headline="h",
    entities=(), category="politics", source_count=1,
    first_seen_at=NOW, last_updated_at=NOW,
)
# The understand half of the real sequence: events land in the DB first.
insert_events(conn, [event])
recent = read_recent_events(conn, hours=72, now=NOW)
print("rows in recent (72h) at validate time:", len(recent), recent[0].event_key)

class _Ctx:
    pass

ctx = _Ctx()
ctx.clusters = [cluster]
ctx.events = [event]
ctx.db = conn
ctx.embedder = FakeEmbedder()
ctx.config = config
ctx.now = NOW
ctx.counters = {}

ValidateStage(cred, logging.getLogger("repro")).run(ctx)
print("events kept by validate:", len(ctx.events))
conn.close()

if len(ctx.events) == 1:
    print("BUG NOT REPRODUCED: event survived validate")
    sys.exit(0)
print("BUG REPRODUCED: fresh event dropped as its own repeat")
sys.exit(1)
