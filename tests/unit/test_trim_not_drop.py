"""Trim-not-drop for over-long summaries (Session 22).

The prompt already asks for "2 to 60 words" (config/prompts/understand.txt
line 18) yet 4-24 clusters/run were fated `oversized` for a 61-90-word
mini-brief -- a formatting miss, not a ramble. `within_bounds` now ADMITS
that band as a trimmable over-run and the shared `build_event` cuts it to
SUMMARY_WORD_BOUNDS[1], so the cluster survives with the first two
sentences (65-90-word summaries are cut at the longest whole-sentence
prefix inside the bound).

Unchanged fates pinned here: past SUMMARY_SOFT_CEILING_WORDS (90) or past
MAX_RESPONSE_CHARS (2000) the answer is still the ramble class and still
drops; a summary without a usable sentence boundary inside the bound
still drops; headline handling is untouched.

No network, no LLM: the router is a stub and both prompt templates are the
real repo files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent.collectors.base import Item
from agent.llm.errors import LlmResult
from agent.pipeline.batch import build_event
from agent.pipeline.cluster import cluster_items
from agent.pipeline.contract import (
    MAX_RESPONSE_CHARS,
    SUMMARY_SOFT_CEILING_WORDS,
    SUMMARY_WORD_BOUNDS,
    trim_summary,
    within_bounds,
)
from agent.pipeline.understand import UnderstandStage

_REPO_ROOT = Path(__file__).parent.parent.parent
_TEMPLATE = (_REPO_ROOT / "config" / "prompts" / "understand.txt").read_text(encoding="utf-8")
_BATCH_TEMPLATE = (_REPO_ROOT / "config" / "prompts" / "understand_batch.txt").read_text(encoding="utf-8")

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
_HEADLINE = "حمله به یک کشتی"
_SHORT = "جزئیات حادثه."


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sentence(words: int) -> str:
    """A terminated Persian sentence of exactly `words` words."""
    return ("کلمه " * words).strip() + "."


def _summary(*sentence_words: int) -> str:
    """`sentence_words` -> a multi-sentence summary with that many words."""
    return " ".join(_sentence(n) for n in sentence_words)


# 75 words / 3 sentences: over the 60-word bound, inside the soft ceiling.
BAND_75 = _summary(30, 25, 20)
# The cut the band expects: the longest whole-sentence prefix inside 60 words.
TRIM_75 = f"{_sentence(30)} {_sentence(25)}"
# 65 words / 3 sentences: also in the band (keeps the whole-batch raw cap
# comfortable when two Persian elements ride one batch response).
BAND_65 = _summary(25, 25, 15)
TRIM_65 = f"{_sentence(25)} {_sentence(25)}"
# 55 words: compliant, must travel byte-identical.
FITS_55 = _summary(25, 20, 10)
# 500 words: the ramble class.
RAMBLE_500 = _summary(250, 250)


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
    payload = {"key": key, "headline": _HEADLINE, "summary": _SHORT,
               "entities": ["Iran"], "clickbait": False, "irrelevant": False}
    payload.update(overrides)
    return payload


def _item(url: str) -> Item:
    return Item(source_id="src", url=url, title=f"title {url}", body="body text",
                published_at=T0, lang="en", raw_hash="h" * 8)


def _cluster(url: str):
    return cluster_items([_item(url)], [(1.0, 0.0, 0.0)], 0.62)[0]


@dataclass
class _Ctx:
    clusters: list = field(default_factory=list)
    events: list = field(default_factory=list)
    router: object = None
    db: object = None
    counters: dict = field(default_factory=dict)
    now: datetime = T0


def _stage(batch_size: int = 1, batch_template: str | None = None):
    log = _Log()
    stage = UnderstandStage(_TEMPLATE, 600, log, batch_template=batch_template,
                            batch_size=batch_size)
    return stage, log


def _words(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# the contract itself (within_bounds + trim_summary)
# ---------------------------------------------------------------------------


def test_band_summary_is_admitted_and_trimmed_to_the_bound():
    # The fix, at the source: a 75-word summary is inside the soft ceiling
    # and trimmable, so it is NOT a contract violation any more.
    assert _words(BAND_75) == 75
    assert _words(BAND_75) <= SUMMARY_SOFT_CEILING_WORDS
    ok, reason = within_bounds({"headline": _HEADLINE, "summary": BAND_75})
    assert ok is True, reason
    trimmed = trim_summary(BAND_75)
    assert trimmed is not None
    assert _words(trimmed) <= SUMMARY_WORD_BOUNDS[1]
    assert _words(trimmed) >= SUMMARY_WORD_BOUNDS[0]
    # Cut at a sentence boundary: the first two sentences, not a mid-word slice.
    assert trimmed == TRIM_75


def test_compliant_summary_is_untouched():
    # 55 words is in bounds: the trim must be a no-op, byte-identical.
    assert _words(FITS_55) == 55
    ok, reason = within_bounds({"headline": _HEADLINE, "summary": FITS_55})
    assert ok is True, reason
    assert trim_summary(FITS_55) == FITS_55
    cluster = _cluster("https://x/fits")
    assert build_event(cluster, _element("c1", summary=FITS_55), T0).summary == FITS_55


def test_summary_without_a_sentence_boundary_still_drops():
    # No terminator to cut on: trimming would slice mid-thought, so the
    # old fate stands (a monolithic run-on is the ramble class).
    run_on = "خلاصه " * 80
    assert len(run_on.split()) == 80
    assert trim_summary(run_on) is None
    ok, reason = within_bounds({"headline": _HEADLINE, "summary": run_on})
    assert ok is False
    assert "summary 80 words" in reason


def test_over_soft_ceiling_summary_still_drops():
    # 500 words: past the soft ceiling, no trim rescues it. raw_len=0 so the
    # assertion is the SUMMARY bound, not the raw-length cap.
    assert _words(RAMBLE_500) == 500
    assert trim_summary(RAMBLE_500) is None
    ok, reason = within_bounds({"headline": _HEADLINE, "summary": RAMBLE_500}, 0)
    assert ok is False
    assert "summary 500 words" in reason and "bounds" in reason


def test_raw_response_cap_still_drops_even_in_the_band():
    # A >MAX_RESPONSE_CHARS response is a ramble wherever it hid -- the
    # trimmable band must not become a bypass for it.
    ok, reason = within_bounds({"headline": _HEADLINE, "summary": _SHORT},
                               MAX_RESPONSE_CHARS + 1)
    assert ok is False
    assert "too long" in reason
    ok2, reason2 = within_bounds({"headline": _HEADLINE, "summary": BAND_75},
                                 MAX_RESPONSE_CHARS + 1)
    assert ok2 is False
    assert "too long" in reason2


def test_headline_violations_are_untouched_by_the_band():
    # Only the summary band changed: a one-word headline still drops.
    ok, reason = within_bounds({"headline": "یک", "summary": BAND_75})
    assert ok is False
    assert "headline 1 words" in reason


# ---------------------------------------------------------------------------
# the callers: the event must survive, trimmed, on BOTH paths
# ---------------------------------------------------------------------------


def test_single_path_ships_the_trimmed_event():
    stage, log = _stage(batch_size=1)
    cluster = _cluster("https://x/single")
    ctx = _Ctx(clusters=[cluster],
               router=_StubRouter([_ok(json.dumps(_element("c1", summary=BAND_65)))]))
    stage.run(ctx)
    assert not any("out of contract" in m for m in log.messages)
    assert len(ctx.events) == 1
    event = ctx.events[0]
    assert event.event_key == cluster.key
    assert _words(event.summary) <= SUMMARY_WORD_BOUNDS[1]
    assert event.summary == TRIM_65
    # Shipped, not fated: a fate entry is only written for a lost cluster.
    assert cluster.key not in dict(ctx.cluster_fates)


def test_batch_path_ships_the_trimmed_event():
    # The batched path passes raw_len=0 per element (the whole-batch cap
    # guards the response), so the band is reached and trimmed here too.
    stage, log = _stage(batch_size=2, batch_template=_BATCH_TEMPLATE)
    a, b = _cluster("https://x/ta"), _cluster("https://x/tb")
    response = _ok(json.dumps([_element("c1"),
                               _element("c2", summary=BAND_65)]))
    ctx = _Ctx(clusters=[a, b], router=_StubRouter([response]))
    stage.run(ctx)
    assert not any("out of contract" in m for m in log.messages)
    assert len(ctx.events) == 2
    kept = {event.event_key: event for event in ctx.events}
    assert _words(kept[b.key].summary) <= SUMMARY_WORD_BOUNDS[1]
    assert kept[b.key].summary == TRIM_65
    assert _words(kept[a.key].summary) == _words(_SHORT)  # mate untouched
    assert b.key not in dict(ctx.cluster_fates)  # shipped, not fated


def test_single_path_still_drops_a_500_word_summary():
    stage, log = _stage(batch_size=1)
    cluster = _cluster("https://x/ramble")
    ctx = _Ctx(clusters=[cluster],
               router=_StubRouter([_ok(json.dumps(_element("c1", summary=RAMBLE_500)))]))
    stage.run(ctx)
    assert ctx.events == []
    assert dict(ctx.cluster_fates)[cluster.key] == "oversized"
    assert any("out of contract" in m for m in log.messages)


def test_raw_length_cap_still_drops_end_to_end():
    # Compliant headline/summary, the bulk of the answer in another field:
    # the raw-length cap (2026-08-30 bai class) still sinks the cluster.
    stage, log = _stage(batch_size=1)
    cluster = _cluster("https://x/raw")
    padding = "بله " * 500  # ~2.5K chars once JSON-escaped
    payload = _element("c1", why_matters=padding)
    ctx = _Ctx(clusters=[cluster], router=_StubRouter([_ok(json.dumps(payload))]))
    assert len(json.dumps(payload)) > MAX_RESPONSE_CHARS
    stage.run(ctx)
    assert ctx.events == []
    assert dict(ctx.cluster_fates)[cluster.key] == "oversized"
    assert any("response too long" in m for m in log.messages)


# ---------------------------------------------------------------------------
# 3a/3b: the keep_min floor and the build_event over-bound backstop
# ---------------------------------------------------------------------------


def test_stub_trim_is_rejected_by_the_keep_min_floor():
    # A 2-word first sentence + a 65-word second: the only cut inside the
    # bound is the 2-word stub. keep_min (max_words // 2) rejects it as a
    # truncation artifact -- None, so no 2-word "summary" is ever shipped.
    stub = _summary(2, 65)
    assert _words(stub) == 67
    assert trim_summary(stub) is None


def test_build_event_never_ships_an_over_bound_summary():
    # Defensive backstop (3b): a summary trim_summary cannot rescue (no
    # whole-sentence cut >= keep_min inside the bound) must not ship over the
    # bound -- build_event falls back to the bounded headline instead of the
    # raw 67 words.
    stub = _summary(2, 65)
    cluster = _cluster("https://x/backstop")
    event = build_event(cluster, _element("c1", summary=stub), T0)
    assert event.summary == _HEADLINE
