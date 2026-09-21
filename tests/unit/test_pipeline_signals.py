"""Gate tests for the Phase 11a signal-extraction unit (offline-measurable).

These lock the extraction contract without any live LLM: the router is stubbed,
so every test is deterministic. Covers catalog derivation, JSON validation,
none/unparseable/unavailable paths, event-date parsing, source-tier attribution,
and the bridge to the deterministic scoring engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from agent.collectors.base import Item
from agent.config import ConfigError
from agent.llm.errors import LlmResult, REFUSED_CAP, UNAVAILABLE
from agent.pipeline.cluster import Cluster
from agent.pipeline.signals import (
    ExtractionResult,
    ExtractedSignal,
    attribute_signal_event,
    extract,
    parse_signals,
    signal_catalog,
)
from agent.risk.engine import SignalEvent

T0 = datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc)

CATALOG = frozenset(
    {
        "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9",
        "B1", "B2", "B3", "B4", "B5", "B6",
        "C2", "C3", "C4", "C5", "C6",
        "D1", "D2", "D3", "D4",
        "E1", "E2", "E3", "E4", "E5", "E6",
        "H1", "H2", "H3",
    }
)

TEMPLATE = "SIGNALS\n{items}\nOUTPUT: strict JSON"


@dataclass(frozen=True)
class _Cred:
    tier: object  # int 1/2/3 or str "lead"
    group: str | None = None


class _Log:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str, *args) -> None:
        self.errors.append(msg % args if args else msg)

    def warning(self, msg: str, *args) -> None:
        self.warnings.append(msg % args if args else msg)

    def info(self, msg: str, *args) -> None:  # pragma: no cover - unused
        pass


class _Stub:
    def __init__(self, text: str = "", ok: bool = True, status: str = "ok",
                 provider: str = "stub") -> None:
        self.text = text
        self.ok = ok
        self.status = status
        self.provider = provider
        self.prompts: list[str] = []
        self.stages: list[str] = []

    def complete(self, prompt, *, stage="understand", use_reservation=None):
        self.prompts.append(prompt)
        self.stages.append(stage)
        return LlmResult(ok=self.ok, status=self.status, text=self.text,
                         provider=self.provider, model="m", prompt_hash="ph",
                         call_index=0)


def _item(source_id: str, url: str, published_at=None) -> Item:
    return Item(source_id=source_id, url=url, title="t", body="b",
                published_at=published_at or T0, lang="en", raw_hash=f"h{url}")


def _cluster(*items: Item) -> Cluster:
    return Cluster(key="k", members=list(items), centroid=[1.0, 0.0, 0.0])


def _signal(signal_id: str, event_date: str = "2026-06-11", **overrides) -> dict:
    base = {
        "signal_id": signal_id,
        "claim": "claim",
        "actor": "actor",
        "event_date": event_date,
        "sources": ["Reuters", "AP"],
        "quote": "quote",
        "confidence_notes": None,
        "state_update": False,
    }
    base.update(overrides)
    return base


_PROMPT_FILE = (
    Path(__file__).resolve().parents[2] / "config" / "prompts" / "signal_extraction.txt"
)


def test_prompt_file_migrated_and_wire_ready():
    text = _PROMPT_FILE.read_text(encoding="utf-8")
    assert text.startswith("You are the signal-extraction stage")
    assert "{items}" in text  # render_prompt placeholder
    assert "SIGNAL CATALOG" in text
    assert "A1★" in text
    assert "H3" in text


# --- catalog derivation -----------------------------------------------------

def test_signal_catalog_excludes_countdown_and_market_only():
    weights = {
        "signals": {"A1": {}, "C1": {}, "G1": {}, "H1": {}},
        "covered_by_markets_fetcher": ["G1"],
    }
    assert signal_catalog(weights) == {"A1", "H1"}


def test_signal_catalog_rejects_missing_signals():
    with pytest.raises(ConfigError):
        signal_catalog({})
    with pytest.raises(ConfigError):
        signal_catalog({"signals": {}})


# --- parse_signals ----------------------------------------------------------

def test_parse_signals_keeps_valid_and_drops_unknown():
    payload = {"signals": [_signal("B1"), _signal("NOT_REAL"), _signal("C1"), 42]}
    valid, dropped = parse_signals(payload, CATALOG)
    assert [s["signal_id"] for s in valid] == ["B1"]
    assert dropped == ["NOT_REAL", "C1"]


def test_parse_signals_missing_or_wrong_type():
    assert parse_signals({}, CATALOG) == ([], [])
    assert parse_signals({"signals": "nope"}, CATALOG) == ([], [])


# --- extract happy path -----------------------------------------------------

def test_extract_ok_returns_typed_signals():
    text = (
        '{"signals": ['
        '{"signal_id": "B1", "claim": "US embassy ordered departure.", '
        '"actor": "State Dept", "event_date": "2026-06-11", '
        '"sources": ["Reuters", "AP"], "quote": "ordered departure", '
        '"confidence_notes": null, "state_update": false},'
        '{"signal_id": "E1", "claim": "Vessel hit in Hormuz.", '
        '"actor": "x", "event_date": "2026-07-06T10:00:00", '
        '"sources": ["AP"], "quote": "q", "confidence_notes": null, '
        '"state_update": false}'
        "]}"
    )
    stub = _Stub(text=text)
    cluster = _cluster(_item("ukmto", "http://a"))
    result = extract(stub, cluster, template=TEMPLATE, body_chars=100,
                     catalog=CATALOG, credibility={}, logger=_Log())
    assert result.status == "ok"
    assert result.provider == "stub"
    assert result.none is False
    assert result.dropped_ids == ()
    assert [s.signal_id for s in result.signals] == ["B1", "E1"]
    assert result.signals[0].event_date == date(2026, 6, 11)
    assert result.signals[1].event_date == date(2026, 7, 6)  # ISO datetime
    assert result.signals[0].sources == ("Reuters", "AP")
    assert result.signals[0].state_update is False


def test_extract_passes_stage_signals_and_renders_items():
    stub = _Stub(text='{"none": true}')
    cluster = _cluster(_item("ukmto", "http://a"))
    extract(stub, cluster, template=TEMPLATE, body_chars=100,
            catalog=CATALOG, credibility={}, logger=_Log())
    assert stub.stages == ["signals"]
    assert "http://a" in stub.prompts[0] or "[ukmto" in stub.prompts[0]
    assert "{items}" not in stub.prompts[0]


# --- extract edge paths -----------------------------------------------------

def test_extract_none():
    stub = _Stub(text='{"none": true}')
    result = extract(stub, _cluster(_item("ukmto", "http://a")),
                     template=TEMPLATE, body_chars=100, catalog=CATALOG,
                     credibility={}, logger=_Log())
    assert result.status == "none"
    assert result.none is True
    assert result.signals == ()


def test_extract_drops_unknown_ids_and_logs():
    stub = _Stub(text='{"signals": [' +
                     '{"signal_id": "B1", "event_date": "2026-06-11"},' +
                     '{"signal_id": "ZZ9", "event_date": "2026-06-11"}]}')
    log = _Log()
    result = extract(stub, _cluster(_item("ukmto", "http://a")),
                     template=TEMPLATE, body_chars=100, catalog=CATALOG,
                     credibility={}, logger=log)
    assert [s.signal_id for s in result.signals] == ["B1"]
    assert result.dropped_ids == ("ZZ9",)
    assert any("ZZ9" in w for w in log.warnings)


def test_extract_unparseable():
    stub = _Stub(text="not json at all")
    log = _Log()
    result = extract(stub, _cluster(_item("ukmto", "http://a")),
                     template=TEMPLATE, body_chars=100, catalog=CATALOG,
                     credibility={}, logger=log)
    assert result.status == "unparseable"
    assert result.signals == ()
    assert log.errors


def test_extract_provider_failure_propagates_status():
    for status in (UNAVAILABLE, REFUSED_CAP):
        stub = _Stub(ok=False, status=status)
        result = extract(stub, _cluster(_item("ukmto", "http://a")),
                         template=TEMPLATE, body_chars=100, catalog=CATALOG,
                         credibility={}, logger=_Log())
        assert result.status == status
        assert result.signals == ()


# --- source-tier attribution ------------------------------------------------

def test_cluster_source_tier_picks_best_corroborating_tier():
    cred = {
        "t1": _Cred(1, "g1"),
        "t2": _Cred(2, "g2"),
        "t3": _Cred(3, None),
        "lead": _Cred("lead", None),
    }
    via_attribute = lambda *src: attribute_signal_event(
        _cluster(*[_item(s, f"http://{s}") for s in src]),
        cred,
        ExtractedSignal("B1", "c", "a", date(2026, 6, 11), (), "q", None, False),
    )
    assert via_attribute("t1").source_tier == 1
    assert via_attribute("t2").source_tier == 2
    assert via_attribute("t3").source_tier == 2          # rumour -> moot default
    assert via_attribute("lead").source_tier == 2
    assert via_attribute("t1", "t2").source_tier == 1    # best wins


def test_attribute_bridges_to_signal_event_with_proxies():
    cred = {"ukmto": _Cred(1, "mil"), "reuters": _Cred(1, "wire")}
    cluster = _cluster(_item("ukmto", "http://a"), _item("reuters", "http://b"))
    sig = ExtractedSignal("B1", "c", "a", date(2026, 6, 11), ("Reuters",),
                          "q", None, False)
    event = attribute_signal_event(cluster, cred, sig)
    assert event == SignalEvent(
        signal_id="B1",
        event_date=date(2026, 6, 11),
        source_tier=1,
        independent_source_count=2,   # two distinct tier-1 groups
    )
    assert event.novelty == 1.0
    assert event.state_end_date is None
