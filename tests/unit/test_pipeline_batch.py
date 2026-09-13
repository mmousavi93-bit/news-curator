"""Unit tests for session-9s batching: pipeline/batch.py (chunk, payload,
key-echo mapping -- the constraint-11 fence), pipeline/batch_run.py (the
batched loop, per-element failure isolation) and understand.py's dispatch.

The single path's behaviour is pinned by test_pipeline_understand.py
(unchanged); this file pins the BATCH path and the batch_size=1 rollback.
The router is a stub; both prompt templates are the real repo files (no
fixture drift). No network, no MiniLM (mock mode, brief requirement 8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent.collectors.base import Item
from agent.llm.errors import LlmResult, REFUSED_CAP, UNAVAILABLE
from agent.pipeline.batch import build_payload, chunk, map_results, render_prompt
from agent.pipeline.cluster import cluster_items
from agent.pipeline.contract import MAX_RESPONSE_CHARS
from agent.pipeline.understand import UnderstandStage

_REPO_ROOT = Path(__file__).parent.parent.parent
_TEMPLATE = (_REPO_ROOT / "config" / "prompts" / "understand.txt").read_text(encoding="utf-8")
_BATCH_TEMPLATE = (_REPO_ROOT / "config" / "prompts" / "understand_batch.txt").read_text(encoding="utf-8")

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
    """Canned responses in order; the last repeats. Records prompts."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt, *, stage="understand", use_reservation=None):
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self._responses) - 1)
        return self._responses[index]


def _ok(text: str) -> LlmResult:
    return LlmResult(ok=True, status="ok", text=text, provider="groq",
                     model="qwen/qwen3.8-27b", prompt_hash="p" * 16, call_index=1)


def _element(key: str, **overrides) -> dict:
    payload = {"key": key, "headline": "Headline here", "summary": "Summary text.",
               "entities": ["Iran"], "clickbait": False, "irrelevant": False}
    payload.update(overrides)
    return payload


def _batch_result(elements) -> LlmResult:
    return _ok(json.dumps(elements))


def _item(url: str, published_at=None, body: str = "body text") -> Item:
    # The url rides in the title so payload assertions can see which
    # item went where (the prompt renders titles, never urls).
    return Item(source_id="src", url=url, title=f"title {url}", body=body,
                published_at=published_at, lang="en", raw_hash="h" * 8)


def _cluster(url: str, vector=(1.0, 0.0, 0.0)):
    return cluster_items([_item(url, T0)], [vector], 0.62)[0]


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    events: list = field(default_factory=list)
    router: object = None
    db: object = None
    counters: dict = field(default_factory=dict)
    now: datetime = T0


def _stage(batch_size: int = 5, batch_template: str | None = _BATCH_TEMPLATE):
    log = _Log()
    stage = UnderstandStage(_TEMPLATE, 600, log,
                            batch_template=batch_template, batch_size=batch_size)
    return stage, log


# ---------------------------------------------------------------------------
# chunk / build_payload / map_results (the constraint-11 fence)
# ---------------------------------------------------------------------------


def test_chunk_splits_and_preserves_order():
    clusters = [_cluster(f"https://x/c/{i}") for i in range(11)]
    batches = chunk(clusters, 5)
    assert [len(b) for b in batches] == [5, 5, 1]
    flat = [c.key for b in batches for c in b]
    assert flat == [c.key for c in clusters]  # order preserved


def test_build_payload_carries_key_headers_and_array_contract():
    a, b = _cluster("https://x/a"), _cluster("https://x/b")
    payload = build_payload([a, b], _BATCH_TEMPLATE, 600)
    assert "## cluster c1" in payload
    assert "## cluster c2" in payload
    assert "[{key" in payload or '"key"' in payload  # array contract present
    assert "https://x/a" in payload and "https://x/b" in payload


def test_map_results_maps_by_echoed_key_not_position():
    # THE constraint-11 pin: the response REORDERS the elements. Position
    # mapping would attach A's summary to B and publish a fabrication.
    a, b = _cluster("https://x/a"), _cluster("https://x/b")
    log = _Log()
    mapped = map_results([a, b], [_element("c2", headline="B story"),
                                  _element("c1", headline="A story")], log)
    assert mapped[a.key]["headline"] == "A story"
    assert mapped[b.key]["headline"] == "B story"


def test_map_results_omitted_cluster_maps_to_none():
    a, b = _cluster("https://x/a"), _cluster("https://x/b")
    mapped = map_results([a, b], [_element("c1")], _Log())
    assert mapped[a.key] is not None
    assert mapped[b.key] is None  # the model omitted b


def test_map_results_unknown_key_dropped_and_logged():
    a = _cluster("https://x/a")
    log = _Log()
    mapped = map_results([a], [_element("c9")], log)
    assert mapped[a.key] is None  # never trusted
    assert any("unknown key" in m for m in log.messages)


def test_map_results_duplicate_key_first_wins():
    a = _cluster("https://x/a")
    log = _Log()
    mapped = map_results(
        [a], [_element("c1", headline="first"), _element("c1", headline="second")], log)
    assert mapped[a.key]["headline"] == "first"
    assert any("twice" in m for m in log.messages)


def test_map_results_non_dict_element_dropped():
    a, b = _cluster("https://x/a"), _cluster("https://x/b")
    log = _Log()
    mapped = map_results([a, b], ["garbage", _element("c2")], log)
    assert mapped[a.key] is None  # malformed element -> its cluster fated unavailable
    assert mapped[b.key] is not None


def test_render_prompt_single_path_still_works():
    cluster = _cluster("https://x/1")
    prompt = render_prompt(_TEMPLATE, cluster, 100)
    assert "https://x/1" in prompt
    assert "## cluster" not in prompt  # single template, single contract


# ---------------------------------------------------------------------------
# the batched loop
# ---------------------------------------------------------------------------


def test_batched_run_all_good_one_call_per_batch():
    stage, _ = _stage(batch_size=2)
    clusters = [_cluster(f"https://x/g/{i}") for i in range(5)]
    router = _StubRouter([
        _batch_result([_element("c1"), _element("c2")]),
        _batch_result([_element("c1"), _element("c2")]),
        _batch_result([_element("c1")]),
    ])
    ctx = _Ctx(clusters=clusters, router=router)
    stage.run(ctx)
    assert len(router.prompts) == 3  # 5 clusters / batch 2 = 3 calls, not 5
    assert len(ctx.events) == 5
    assert ctx.llm_failed is False


def test_batched_run_malformed_element_costs_one_summary_not_the_batch():
    stage, log = _stage(batch_size=2)
    a, b = _cluster("https://x/ma"), _cluster("https://x/mb")
    ctx = _Ctx(clusters=[a, b],
               router=_StubRouter([_batch_result([_element("c2"), "garbage"])]))
    stage.run(ctx)
    assert len(ctx.events) == 1  # batch-mate shipped
    assert ctx.events[0].event_key == b.key
    fates = dict(ctx.cluster_fates)
    assert fates[a.key] == "unavailable"  # malformed -> individually unavailable
    assert b.key not in fates


def test_batched_run_missing_element_batch_mates_ship():
    stage, _ = _stage(batch_size=2)
    a, b = _cluster("https://x/na"), _cluster("https://x/nb")
    ctx = _Ctx(clusters=[a, b],
               router=_StubRouter([_batch_result([_element("c1")])]))
    stage.run(ctx)
    assert len(ctx.events) == 1
    assert ctx.events[0].event_key == a.key
    assert dict(ctx.cluster_fates)[b.key] == "unavailable"


def test_batched_run_clickbait_element_filters_only_that_cluster():
    stage, _ = _stage(batch_size=2)
    a, b = _cluster("https://x/ca"), _cluster("https://x/cb")
    ctx = _Ctx(clusters=[a, b],
               router=_StubRouter([_batch_result([
                   _element("c1"), _element("c2", clickbait=True)])]))
    stage.run(ctx)
    assert len(ctx.events) == 1
    assert ctx.events[0].event_key == a.key
    assert dict(ctx.cluster_fates)[b.key] == "clickbait"


def test_batched_run_whole_batch_call_failure_marks_all():
    stage, _ = _stage(batch_size=2)
    a, b = _cluster("https://x/ua"), _cluster("https://x/ub")
    ctx = _Ctx(clusters=[a, b],
               router=_StubRouter([LlmResult(ok=False, status=UNAVAILABLE)]))
    stage.run(ctx)
    assert ctx.events == []
    assert ctx.llm_failed is True
    fates = dict(ctx.cluster_fates)
    assert fates[a.key] == "unavailable" and fates[b.key] == "unavailable"


def test_batched_run_cap_refused_stops_and_fates_the_rest():
    stage, log = _stage(batch_size=2)
    clusters = [_cluster(f"https://x/cp/{i}") for i in range(6)]
    router = _StubRouter([
        _batch_result([_element("c1"), _element("c2")]),
        LlmResult(ok=False, status=REFUSED_CAP),
    ])
    ctx = _Ctx(clusters=clusters, router=router)
    stage.run(ctx)
    assert len(ctx.events) == 2  # first batch shipped
    assert len(router.prompts) == 2  # stopped, did not keep calling
    fates = dict(ctx.cluster_fates)
    assert all(fates[c.key] == "cap_refused" for c in clusters[2:])
    caps = [m for m in log.messages if "call cap" in m]
    assert len(caps) == 1


def test_batched_run_non_array_response_fates_all_unparseable():
    stage, _ = _stage(batch_size=2)
    a, b = _cluster("https://x/na2"), _cluster("https://x/nb2")
    ctx = _Ctx(clusters=[a, b],
               router=_StubRouter([_ok(json.dumps(_element("c1")))]))  # object, not array
    stage.run(ctx)
    assert ctx.events == []
    fates = dict(ctx.cluster_fates)
    assert fates[a.key] == "unparseable" and fates[b.key] == "unparseable"


def test_batched_run_oversized_element_fated_individually():
    # Per-element field bounds: b's summary is a ramble, so b alone is
    # fated oversized; the compliant batch-mate ships. English on purpose:
    # Persian text inflates ~6x under json.dumps' \u escaping, which would
    # trip the whole-batch raw cap instead of the element bounds.
    stage, _ = _stage(batch_size=2)
    a, b = _cluster("https://x/ov"), _cluster("https://x/ov2")
    ramble = _batch_result([
        _element("c1"), _element("c2", summary="word " * 200)])
    ctx = _Ctx(clusters=[a, b], router=_StubRouter([ramble]))
    stage.run(ctx)
    assert len(ctx.events) == 1
    assert ctx.events[0].event_key == a.key
    assert dict(ctx.cluster_fates)[b.key] == "oversized"


def test_batched_run_batch_level_rambles_cap_scaled_by_batch_size():
    # contract.py's raw-length cap scaled by batch size: a whole-batch
    # ramble (2026-08-30 class) fates the ENTIRE batch oversized.
    stage, log = _stage(batch_size=2)
    a, b = _cluster("https://x/br"), _cluster("https://x/br2")
    huge = "بله. " * 4000  # ~20K chars per element: way past 2 * MAX_RESPONSE_CHARS
    ramble = _batch_result([
        _element("c1", summary=huge), _element("c2", summary=huge)])
    ctx = _Ctx(clusters=[a, b], router=_StubRouter([ramble]))
    stage.run(ctx)
    fates = dict(ctx.cluster_fates)
    assert fates[a.key] == "oversized" and fates[b.key] == "oversized"
    assert ctx.events == []
    assert any("response too long" in m for m in log.messages)


def test_batched_run_drift_element_retried_with_single_prompt():
    # A drifting batch-mate is recovered with the SINGLE-cluster prompt
    # (the old contract), one extra call, batch-mates untouched.
    stage, log = _stage(batch_size=2)
    a, b = _cluster("https://x/da"), _cluster("https://x/db")
    drift = _element("c2", headline="يك حمله", summary="يك خبر عربي.")
    recovered = json.dumps({"headline": "حمله در تنگه هرمز", "summary": "جزئیات حادثه.",
                            "entities": ["IRGC"], "clickbait": False, "irrelevant": False})
    router = _StubRouter([_batch_result([_element("c1"), drift]), _ok(recovered)])
    ctx = _Ctx(clusters=[a, b], router=router)
    stage.run(ctx)
    assert len(ctx.events) == 2
    assert len(router.prompts) == 2
    # The retry prompt is the SINGLE template with the forced-Persian line.
    assert "## cluster" not in router.prompts[1]
    assert "PERSIAN" in router.prompts[1]
    assert any("retried -- Persian recovered" in m for m in log.messages)


# ---------------------------------------------------------------------------
# batch_size=1 rollback
# ---------------------------------------------------------------------------


def test_batch_size_one_uses_single_path_exactly():
    # Brief requirement 3: 1 must reproduce today's one-call-per-cluster
    # behaviour exactly. With batch_size=1 the batch template must never
    # even be consulted.
    stage, _ = _stage(batch_size=1)
    clusters = [_cluster(f"https://x/r/{i}") for i in range(3)]
    router = _StubRouter([_ok(json.dumps(_element("c1")))])
    ctx = _Ctx(clusters=clusters, router=router)
    stage.run(ctx)
    assert len(router.prompts) == 3  # one call PER cluster, not batched
    for prompt in router.prompts:
        assert "## cluster" not in prompt  # single-object contract
    assert len(ctx.events) == 3


def test_batch_size_one_ignores_batch_template_even_when_present():
    stage, _ = _stage(batch_size=1, batch_template=_BATCH_TEMPLATE)
    cluster = _cluster("https://x/r1")
    router = _StubRouter([_ok(json.dumps(_element("c1")))])
    ctx = _Ctx(clusters=[cluster], router=router)
    stage.run(ctx)
    assert "## cluster" not in router.prompts[0]


def test_no_batch_template_falls_back_to_single_path():
    stage, _ = _stage(batch_size=5, batch_template=None)
    cluster = _cluster("https://x/nt")
    router = _StubRouter([_ok(json.dumps(_element("c1")))])
    ctx = _Ctx(clusters=[cluster], router=router)
    stage.run(ctx)  # must not raise
    assert len(ctx.events) == 1
