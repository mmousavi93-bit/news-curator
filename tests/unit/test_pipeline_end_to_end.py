"""The Phase 6 gate itself, end-to-end: ten articles about one event become
one event, through the REAL stages (filter -> embed -> cluster -> understand)
with a controlled embedder and a stub router. Split out of
test_pipeline_understand.py for the ~200-line convention."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.collectors.base import Item
from agent.config import Config, SourceCredibility
from agent.llm.errors import LlmResult
from agent.pipeline.cluster import ClusterStage, EmbedStage
from agent.pipeline.compose import ComposeStage
from agent.pipeline.deliver import DeliverStage
from agent.pipeline.filter import TopicGateStage
from agent.pipeline.understand import UnderstandStage
from agent.pipeline.validate import ValidateStage
from agent.settings import Settings

_REPO_ROOT = Path(__file__).parent.parent.parent
_TEMPLATE = (_REPO_ROOT / "config" / "prompts" / "understand.txt").read_text(encoding="utf-8")
T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def warning(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)


class _StubRouter:
    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt, *, stage="understand", use_reservation=None):
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self._responses) - 1)
        return self._responses[index]


def _ok_json() -> LlmResult:
    payload = {"headline": "H", "summary": "S.", "entities": ["Iran"],
               "clickbait": False, "irrelevant": False}
    return LlmResult(ok=True, status="ok", text=json.dumps(payload),
                     provider="gemini", model="m", prompt_hash="p" * 16, call_index=1)


def _item(url: str) -> Item:
    return Item(source_id="src", url=url, title="t", body="b",
                published_at=T0, lang="en", raw_hash="h" * 8)


def _unit_vec(*values: float) -> list[float]:
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values] if norm else list(values)


class _VecEmbedder:
    def __init__(self, vectors):
        self._vectors = vectors

    def embed(self, texts):
        return self._vectors


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    events: list = field(default_factory=list)
    router: object = None
    db: object = None
    counters: dict = field(default_factory=dict)
    now: datetime = T0


def test_end_to_end_ten_articles_one_event():
    """Gate 1. 12 items: 10 near-identical about one event, 2 about
    something else -> exactly 2 clusters -> 2 events."""
    fixture = Path(__file__).parent.parent / "fixtures" / "settings_minimal.yaml"
    settings = Settings.from_dict(yaml.safe_load(fixture.read_text(encoding="utf-8")))
    config = Config(
        settings=settings,
        credibility={"src": SourceCredibility(tier=1, group=None)},
    )

    items, vectors = [], []
    for i in range(10):
        items.append(_item(f"https://x/event/{i}"))
        vectors.append(_unit_vec(1.0, 0.01 * (i + 1)))
    for i in range(2):
        items.append(_item(f"https://x/other/{i}"))
        vectors.append(_unit_vec(0.0, 1.0))

    log = _Log()
    ctx = _Ctx(router=_StubRouter([_ok_json(), _ok_json()]))
    ctx.items = items
    ctx.embedder = _VecEmbedder(vectors)
    ctx.config = config
    ctx.dry_run = True  # deliver must not send

    TopicGateStage({}, set(), log).run(ctx)  # nothing gated -> pass-through
    EmbedStage().run(ctx)
    ClusterStage(config, log).run(ctx)
    UnderstandStage(_TEMPLATE, 600, log).run(ctx)
    ValidateStage({"src": SourceCredibility(tier=1, group=None)}, log).run(ctx)
    ComposeStage(log).run(ctx)
    DeliverStage({}, log).run(ctx)

    assert len(ctx.clusters) == 2
    assert len(ctx.events) == 2
    sizes = sorted(e.source_count for e in ctx.events)
    assert sizes == [2, 10]
    # One source family corroborates each event: unconfirmed, not rumour.
    assert all(e.claim_status == "unconfirmed" for e in ctx.events)
    # The full pipe ends in composed, budgeted message(s).
    assert ctx.messages and ctx.messages[0]
    assert ctx.counters["compose"] == 2
    assert ctx.counters["deliver"] == 0  # dry-run: nothing sent
