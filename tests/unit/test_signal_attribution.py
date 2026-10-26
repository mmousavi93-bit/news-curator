"""Shared fixtures + deterministic attribution tests for signal extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from agent.collectors.base import Item
from agent.pipeline.signal_attribution import derive
from agent.pipeline.signal_contract import EvidenceError, validate_candidate
from agent.risk.engine import SignalEvent

NOW = datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc)
QUOTE = "ordered the departure of embassy personnel"
BODY = f"The State Department {QUOTE}."

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


@dataclass(frozen=True)
class _Cred:
    tier: object  # int 1/2/3 or str "lead"
    group: str | None = None


def _make_item(source_id: str, url: str) -> Item:
    return Item(
        source_id=source_id, url=url, title="t", body=BODY,
        published_at=datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc),
        lang="en", raw_hash=f"h{url}",
    )


def _make_raw(signal_id: str = "B1") -> dict:
    return {
        "signal_id": signal_id,
        "claim": "embassy personnel ordered to depart",
        "actor": "US State Department",
        "action": "ordered departure",
        "object": "embassy personnel",
        "location": "regional embassy",
        "occurrence_id": "occ-b1",
        "episode_anchor": "ep-b1",
        "supports": [
            {"item_id": "http://a", "quote": QUOTE},
            {"item_id": "http://b", "quote": QUOTE},
        ],
        "predicates": ["official_ordered_embassy_departure"],
        "status": "current_affirmative",
    }


raw_candidate = _make_raw()


def evidence(signal_id: str = "B1"):
    """(raw candidate, items keyed by url, credibility dict) for a B1 claim
    with two tier-2 sources in two distinct independence groups."""
    items = {"http://a": _make_item("src_a", "http://a"),
             "http://b": _make_item("src_b", "http://b")}
    cred = {"src_a": _Cred(2, "ga"), "src_b": _Cred(2, "gb")}
    return _make_raw(signal_id), items, cred


# --- attribution: the deterministic six-field bridge ------------------------


def test_derive_happy_path():
    raw, items, cred = evidence("B1")
    sig = validate_candidate(raw, items, CATALOG)
    attr = derive(sig, items, cred, now=NOW)
    assert SignalEvent(**attr.fields()) == SignalEvent("B1", NOW.date(), 2, 2, 1.0, None)


def test_derive_prefers_best_tier_among_supporters():
    raw, items, cred = evidence("B1")
    cred["src_a"] = _Cred(1, "ga")  # upgrade one corroborator to tier 1
    sig = validate_candidate(raw, items, CATALOG)
    attr = derive(sig, items, cred, now=NOW)
    assert attr.source_tier == 1


def test_derive_rejects_lead_only_supporters():
    raw, items, cred = evidence("B1")
    cred["src_a"] = _Cred("lead", "ga")
    cred["src_b"] = _Cred("lead", "gb")
    sig = validate_candidate(raw, items, CATALOG)
    with pytest.raises(EvidenceError):
        derive(sig, items, cred, now=NOW)


def test_derive_rejects_missing_credibility_join():
    raw, items, cred = evidence("B1")
    del cred["src_a"]
    sig = validate_candidate(raw, items, CATALOG)
    with pytest.raises(EvidenceError):
        derive(sig, items, cred, now=NOW)


def test_derive_rejects_foreign_item_id():
    raw, items, cred = evidence("B1")
    raw["supports"][0]["item_id"] = "http://ghost"
    with pytest.raises(EvidenceError):
        validate_candidate(raw, items, CATALOG)


def test_derive_rejects_nonmatching_quote():
    raw, items, cred = evidence("B1")
    raw["supports"][0]["quote"] = "not in any body"
    with pytest.raises(EvidenceError):
        validate_candidate(raw, items, CATALOG)
