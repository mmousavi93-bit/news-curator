"""Offline lifecycle tests using an injected store and evaluation clock."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from agent.pipeline.signal_attribution import TEHRAN, derive
from agent.pipeline.signal_contract import EvidenceError, STATE_ENDS
from agent.pipeline.signal_history import SignalHistory, occurrence_key
from agent.pipeline.signals import attribute_run
from unit.test_signal_attribution import CATALOG, NOW, fixture, _Cred

DAILY = datetime(2026, 6, 11, 9, tzinfo=TEHRAN)
WEIGHTS = {"signals": {sid: {"base": 1} for sid in CATALOG},
           "tier_multipliers": {1: 1.0, 2: .8, 3: .5}}


def accept(history, candidate, items, cred, now=NOW):
    event = derive(candidate, items, cred, now=now, history=history)
    return history.accept(candidate, event, items, cred, now=now)


def quiet(history, start, days, *, complete=True):
    for n in range(days):
        history.advance(now=start + timedelta(days=n), complete=complete)


def test_repost_merges_evidence_freezes_date_and_does_not_fire():
    history = SignalHistory(CATALOG)
    candidate, items, cred = fixture()
    one = replace(candidate, supports=candidate.supports[:1])
    assert not accept(history, one, items, cred)
    original = history.find(one).event.event_date
    later = NOW + timedelta(days=1)
    items = {k: replace(v, published_at=later) for k, v in items.items()}
    other = replace(candidate, supports=candidate.supports[1:])
    assert accept(history, other, items, cred, later)
    assert not accept(history, candidate, items, cred, later)
    assert len(history.materialize()) == 1
    assert history.materialize()[0].event_date == original
    assert history.materialize()[0].independent_source_count == 2
    assert derive(candidate, items, cred, now=later, history=history).event_date == original


def test_quiet_cycle_13_14_15_days_and_no_repeat_decrement():
    history = SignalHistory(CATALOG)
    candidate, items, cred = fixture(day=DAILY)
    accept(history, candidate, items, cred, DAILY)
    history.advance(now=DAILY, complete=True)
    quiet(history, DAILY + timedelta(days=1), 13)
    assert history.factor("B1") == 1
    history.advance(now=DAILY + timedelta(days=14), complete=True)
    assert history.factor("B1") == .6
    quiet(history, DAILY + timedelta(days=15), 30)
    assert history.factor("B1") == .6


def test_full_factor_sequence_requires_fresh_firing_each_cycle():
    history = SignalHistory(CATALOG)
    for cycle, expected in enumerate([.6, .36, .3, .3]):
        now = DAILY + timedelta(days=cycle * 15)
        candidate, items, cred = fixture(day=now, identity=f"episode-{cycle}")
        items = {k + str(cycle): replace(v, url=k + str(cycle)) for k, v in items.items()}
        candidate = replace(candidate, supports=tuple(replace(s, item_id=s.item_id + str(cycle)) for s in candidate.supports))
        assert accept(history, candidate, items, cred, now)
        history.advance(now=now, complete=True)
        quiet(history, now + timedelta(days=1), 14)
        assert history.factor("B1") == expected


def test_incomplete_missing_intraday_and_replayed_days():
    history = SignalHistory(CATALOG)
    candidate, items, cred = fixture(day=DAILY)
    accept(history, candidate, items, cred, DAILY)
    quiet(history, DAILY, 10)
    history.advance(now=DAILY + timedelta(days=10), complete=False)
    assert history.ledger["B1"].quiet_days == 0
    assert history.find(candidate).observation_health == "uncertain"
    quiet(history, DAILY + timedelta(days=11), 13)
    assert history.factor("B1") == 1
    history.advance(now=DAILY + timedelta(days=24, hours=1), complete=True)
    assert history.ledger["B1"].quiet_days == 13
    history.advance(now=DAILY + timedelta(days=25), complete=True)
    assert history.ledger["B1"].quiet_days == 1  # missing canonical day is unknown
    history.advance(now=DAILY + timedelta(days=25), complete=True)
    assert history.ledger["B1"].quiet_days == 1
    restored = deepcopy(history)
    assert restored.materialize() == history.materialize()
    del restored.ledger["D1"]
    with pytest.raises(EvidenceError, match="ledger"):
        restored.materialize()


@pytest.mark.parametrize("sid", sorted(STATE_ENDS))
def test_explicit_state_end_and_reactivation(sid):
    history = SignalHistory(CATALOG)
    candidate, items, cred = fixture(sid, day=DAILY)
    # A2 starts from two independently accepted overlapping carrier postures.
    if sid == "A2":
        keys = []
        for asset in ["carrier-one", "carrier-two"]:
            prior, pitems, pcred = fixture("A1", object=asset, identity=asset, day=DAILY)
            accept(history, prior, pitems, pcred, DAILY)
            keys.append(occurrence_key(prior))
        candidate = replace(candidate, prior_ids=tuple(keys))
    assert accept(history, candidate, items, cred, DAILY)
    quiet(history, DAILY, 20)
    assert history.factor(sid) == 1  # active state is never quiet
    assert history.find(candidate).event.state_end_date is None
    end_now = DAILY + timedelta(days=20)
    end, eitems, ecred = fixture(sid, day=end_now, transition="end",
        predicates=[STATE_ENDS[sid]], text="confirmed theater count 1", numbers=[1] if sid == "A2" else [])
    assert not accept(history, end, eitems, ecred, end_now)
    record = history.find(candidate)
    assert record.event.event_date == DAILY.date()
    assert record.event.state_end_date == end_now.date()
    assert record.end_evidence
    assert not accept(history, end, eitems, ecred, end_now)
    assert len(record.end_evidence) == 1
    # Reopening requires grounded new identity; never reuse the closed episode.
    with pytest.raises(EvidenceError, match="reactivation"):
        accept(history, candidate, items, cred, end_now)
    if sid != "A2":
        reopened, ritems, rcred = fixture(sid, identity="reactivated", day=end_now)
        assert accept(history, reopened, ritems, rcred, end_now)
        assert len(history.records) == 2


def test_uncorroborated_future_end_and_duplicate_active_state():
    history = SignalHistory(CATALOG)
    candidate, items, cred = fixture("A1")
    accept(history, candidate, items, cred)
    end = replace(candidate, transition="end", predicates=frozenset({STATE_ENDS["A1"]}))
    with pytest.raises(EvidenceError, match="uncorroborated"):
        accept(history, replace(end, supports=end.supports[:1]), items, cred)
    with pytest.raises(EvidenceError, match="future withdrawal"):
        accept(history, replace(end, effective_date=NOW.date()+timedelta(days=1)), items, cred)
    with pytest.raises(EvidenceError, match="duplicate open state"):
        accept(history, replace(candidate, occurrence_id="another", episode_anchor="another"), items, cred)
    assert not accept(history, replace(candidate, transition="reaffirm"), items, cred)
    assert len(history.materialize()) == 1


def test_a2_partial_withdrawal_does_not_end_overlap():
    candidate, items, cred = fixture("A2", transition="end", predicates=[STATE_ENDS["A2"]],
                                     numbers=[2], text="2 carriers remain")
    with pytest.raises(EvidenceError, match="below two"):
        derive(candidate, items, cred, now=NOW)


@pytest.mark.parametrize("base,reset", [(18.74, False), (18.75, True)])
def test_reset_threshold_and_materialization_of_historical_events(base, reset):
    history = SignalHistory(CATALOG)
    history.ledger["D4"].factor = .36
    old, items, cred = fixture("D4")
    accept(history, old, items, cred)
    candidate, items, cred = fixture("B1")
    weights = deepcopy(WEIGHTS)
    weights["signals"]["B1"]["base"] = base
    events = attribute_run([candidate], items, cred, now=NOW, history=history, weights=weights)
    assert next(e.novelty for e in events if e.signal_id == "D4") == (1 if reset else .36)


def test_newly_merged_corroboration_resets_and_failed_run_is_atomic():
    history = SignalHistory(CATALOG)
    candidate, items, cred = fixture()
    history.ledger["D4"].factor = .3
    accept(history, replace(candidate, supports=candidate.supports[:1]), items, cred)
    weights = deepcopy(WEIGHTS)
    weights["signals"]["B1"]["base"] = 30
    attribute_run([replace(candidate, supports=candidate.supports[1:])], items, cred,
                  now=NOW, history=history, weights=weights)
    assert history.factor("D4") == 1
    fresh = SignalHistory(CATALOG)
    bad = replace(candidate, signal_id="D2")
    with pytest.raises(EvidenceError):
        attribute_run([candidate, bad], items, cred, now=NOW, history=fresh, weights=weights)
    assert not fresh.records
    with pytest.raises(EvidenceError, match="missing required history"):
        attribute_run([], {}, {}, now=NOW, history=None, weights=weights)


def test_linked_consequence_interrupts_quiet_and_retains_link():
    history = SignalHistory(CATALOG)
    parent, items, cred = fixture("D1", day=DAILY)
    accept(history, parent, items, cred, DAILY)
    quiet(history, DAILY, 10)
    now = DAILY + timedelta(days=10, hours=1)
    attack, aitems, acred = fixture("E1", day=now)
    accept(history, attack, aitems, acred, now)
    links = [(occurrence_key(parent), occurrence_key(attack))]
    history.advance(now=now, complete=True, linked_consequences=links)
    assert history.ledger["D1"].quiet_days == 0
    assert occurrence_key(attack) in history.find(parent).linked_consequences
    with pytest.raises(EvidenceError, match="consequence link"):
        history.advance(now=now, complete=True, linked_consequences=[("missing", "unknown")])


def test_same_article_can_support_distinct_assets_without_corroborating_unrelated_claim():
    history = SignalHistory(CATALOG)
    first, items, cred = fixture("A1", identity="one", object="carrier-one")
    assert accept(history, first, items, cred)
    second = replace(first, occurrence_id="two", episode_anchor="two", object="carrier-two")
    assert accept(history, second, items, cred)
    assert len(history.materialize()) == 2


def test_reset_wins_over_same_day_quiet_cycle_decrement():
    history = SignalHistory(CATALOG)
    history.ledger["D4"].armed = True
    history.ledger["D4"].quiet_days = 13
    history.last_checkpoint = DAILY.date() - timedelta(days=1)
    candidate, items, cred = fixture("B1", day=DAILY)
    weights = deepcopy(WEIGHTS)
    weights["signals"]["B1"]["base"] = 30
    attribute_run([candidate], items, cred, now=DAILY, history=history, weights=weights)
    assert history.factor("D4") == 1


def test_empty_history_with_deleted_ledger_halts_scoring():
    history = SignalHistory(CATALOG)
    history.ledger.clear()
    with pytest.raises(EvidenceError, match="ledger"):
        attribute_run([], {}, {}, now=DAILY, history=history, weights=WEIGHTS)
