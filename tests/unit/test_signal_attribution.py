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

# Controlled contract fixtures: semantic entailment still needs reviewed history.
from dataclasses import replace
from datetime import date, timedelta
import math
from pathlib import Path
import importlib.util
import yaml
from agent.pipeline.signal_contract import PREDICATES, STATE_ENDS, validate_envelope
from agent.pipeline.signal_history import SignalHistory, occurrence_key


def fixture(sid="B1", *, text="affirmative source evidence", day=NOW, identity="incident", **updates):
    raw, items, cred = evidence()
    raw.update(signal_id=sid, claim="grounded claim", actor="authority", action="action",
               object="asset", location="theater", occurrence_id=identity,
               episode_anchor=identity, predicates=PREDICATES[sid].split())
    raw.update(updates)
    items = {k: replace(v, body=text, published_at=day) for k, v in items.items()}
    raw["supports"] = [{"item_id": k, "quote": text} for k in items]
    candidate = validate_candidate(raw, items, CATALOG | {sid},
                                   calendar=True if sid == "C1" else sid if sid in {"soothing", "schedule"} else False)
    return candidate, items, cred


@pytest.mark.parametrize("sid", sorted(CATALOG))
@pytest.mark.parametrize("text", ["Current affirmative evidence", "شواهد جاری تأیید شده", "أدلة حالية مؤكدة"])
def test_each_mapping_requires_all_predicates(sid, text):
    candidate, items, _ = fixture(sid, text=text)
    assert candidate.signal_id == sid
    raw = vars(candidate).copy()
    raw["predicates"] = []
    with pytest.raises(EvidenceError, match="mapping predicate"):
        validate_candidate(raw, items, CATALOG)


@pytest.mark.parametrize("sid", ["C1", "G1", "G2", "ZZ9"])
def test_news_rejects_foreign_catalog_ids(sid):
    raw, items, _ = evidence()
    raw["signal_id"] = sid
    with pytest.raises(EvidenceError, match="signal ID"):
        validate_candidate(raw, items, CATALOG | {sid})


@pytest.mark.parametrize("status", ["historical", "hypothetical", "denied", "quoted_as_false"])
def test_nonaffirmative_claims_quarantined(status):
    raw, items, _ = evidence()
    raw["status"] = status
    with pytest.raises(EvidenceError, match="noncurrent"):
        validate_candidate(raw, items, CATALOG)


@pytest.mark.parametrize("updates", [{"numbers": [math.nan]}, {"numbers": [math.inf]},
    {"numbers": [True]}, {"numbers": [10]}, {"occurrence_date": "2026-06-11"},
    {"claim": "Date 2026-11-06"}, {"event_date": "2026-06-11"}])
def test_unsupported_numbers_dates_and_fields(updates):
    raw, items, _ = evidence()
    raw.update(updates)
    with pytest.raises(EvidenceError):
        validate_candidate(raw, items, CATALOG)


def test_date_literal_must_match_whole_date_not_reordered_digits():
    raw, items, _ = evidence()
    text = "Report dated 2026-06-11"
    items = {k: replace(v, body=text) for k, v in items.items()}
    raw["supports"] = [{"item_id": k, "quote": text} for k in items]
    raw["claim"] = "Report dated 2026-11-06"
    with pytest.raises(EvidenceError, match="unsupported date"):
        validate_candidate(raw, items, CATALOG)


@pytest.mark.parametrize("channel", ["signals", "countdowns", "soothing_statements", "scheduled_events"])
def test_none_contradiction_all_channels(channel):
    with pytest.raises(EvidenceError, match="contradiction"):
        validate_envelope({"none": True, channel: [{}]})
    validate_envelope({"none": True})


def test_tehran_midnight_date_only_and_claim_subset():
    candidate, items, cred = fixture(day=datetime(2026, 6, 10, 21, tzinfo=timezone.utc))
    assert derive(candidate, items, cred, now=NOW).event_date == date(2026, 6, 11)
    items = {k: replace(v, date_only=True) for k, v in items.items()}
    assert derive(candidate, items, cred, now=NOW).event_date == date(2026, 6, 10)
    items["unrelated"] = replace(items["http://a"], published_at=NOW-timedelta(days=90))
    assert derive(candidate, items, cred, now=NOW).event_date == date(2026, 6, 10)


@pytest.mark.parametrize("published", [None, NOW + timedelta(seconds=1), NOW.replace(tzinfo=None)])
def test_missing_future_ambiguous_dates(published):
    candidate, items, cred = fixture()
    items = {k: replace(v, published_at=published) for k, v in items.items()}
    with pytest.raises(EvidenceError, match="publication dates"):
        derive(candidate, items, cred, now=NOW)


@pytest.mark.parametrize("tier", [True, 2.0, "2", 0, 4])
def test_strict_tiers(tier):
    candidate, items, cred = fixture()
    cred["src_a"] = _Cred(tier, "ga")
    with pytest.raises(EvidenceError, match="strict integer"):
        derive(candidate, items, cred, now=NOW)


@pytest.mark.parametrize("groups,count", [(("bbc", "bbc"), 1), (("reuters", "ap"), 2),
                                        ((None, "src_a"), 2), ((None, ""), 2)])
def test_independence_groups(groups, count):
    candidate, items, cred = fixture()
    cred = {"src_a": _Cred(2, groups[0]), "src_b": _Cred(2, groups[1])}
    assert derive(candidate, items, cred, now=NOW).independent_source_count == count


def test_relays_do_not_corroborate_and_tier3_only_nomination():
    candidate, items, cred = fixture()
    relayed = replace(candidate, supports=(candidate.supports[0], replace(candidate.supports[1], relay_of="src_a")))
    assert derive(relayed, items, cred, now=NOW).independent_source_count == 1
    uncertain = replace(candidate, supports=(replace(candidate.supports[0], uncertain=True),))
    with pytest.raises(EvidenceError, match="uncertain provenance"):
        derive(uncertain, items, cred, now=NOW)
    cred = {k: _Cred(3, k) for k in cred}
    event = derive(candidate, items, cred, now=NOW)
    assert (event.source_tier, event.independent_source_count) == (3, 0)


@pytest.mark.parametrize("sid", ["A2", "E2", "D2", "D3", "B3", "B5", "C3"])
def test_required_history_missing(sid):
    candidate, items, cred = fixture(sid)
    with pytest.raises(EvidenceError, match="insufficient_history"):
        derive(candidate, items, cred, now=NOW)


@pytest.mark.parametrize("count,accepted", [(9, False), (10, True), (12, True)])
def test_tanker_count_threshold(count, accepted):
    candidate, items, cred = fixture("A5", text=f"Surge of {count} tanker aircraft", numbers=[count])
    if accepted:
        assert derive(candidate, items, cred, now=NOW).signal_id == "A5"
    else:
        with pytest.raises(EvidenceError, match="ten aircraft"):
            derive(candidate, items, cred, now=NOW)


@pytest.mark.parametrize("later,accepted", [(115, False), (117, False), (120, True)])
def test_rial_depreciation_calculated_in_python(later, accepted):
    text = f"IRR per USD 100 on 2026-06-05; {later} on 2026-06-11"
    candidate, items, cred = fixture("H3", text=text, numbers=[100, later],
        dates=["2026-06-05", "2026-06-11"], predicates=["comparable_bazaar_rial", "irr_per_usd"])
    if accepted:
        derive(candidate, items, cred, now=NOW)
    else:
        with pytest.raises(EvidenceError, match="15 percent"):
            derive(candidate, items, cred, now=NOW)


def test_five_frozen_calibration_scenarios_exact_parity():
    # Import fixtures only. Never execute the CSV-writing main.
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("frozen_backtest", root / "analysis/backtest_weights.py")
    backtest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backtest)
    from agent.risk.engine import RiskEngine
    weights = yaml.safe_load((root / "config/risk_weights.yaml").read_text(encoding="utf-8"))
    engine = RiskEngine(weights)
    actual = []
    for sc in backtest.SCENARIOS:
        events = [SignalEvent(signal_id=e["id"], event_date=e["date"], source_tier=e["tier"],
                              independent_source_count=2, novelty=e.get("novelty", 1.0),
                              state_end_date=e.get("until")) for e in sc["events"]]
        kwargs = {"deadline": sc.get("deadline"), "deception": sc.get("deception", False)}
        result = engine.evaluate(events, sc["at"], **kwargs)
        score = getattr(result, sc["which"].lower()).capped
        reference = backtest.score(sc["events"], sc["at"], sc["which"], "state", **kwargs)
        assert score == round(reference, 1)
        assert sc["lo"] <= score <= sc["hi"]
        if sc["name"].startswith("Quiet"):
            assert score < 15
        actual.append(score)
    assert actual == [100.0, 63.9, 100.0, 87.3, 8.1]
