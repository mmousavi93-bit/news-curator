"""Phase 11 gate: risk/engine.py must reproduce analysis/backtest_weights.py
(state mode) and SCORING_RULEBOOK.md worked examples EXACTLY -- identical
input -> identical score. The 5 calibration scenarios must land in their bands;
we assert the exact reference values the backtest prints for state mode."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from agent.risk.engine import (
    RiskEngine,
    SignalEvent,
    is_strat_signal,
    is_tact_signal,
    alert_decision,
    next_regime,
    tier,
)

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = yaml.safe_load((ROOT / "config" / "risk_weights.yaml").read_text(encoding="utf-8"))
engine = RiskEngine(WEIGHTS)


def E(sid, y, m, d, tier, until=None, novelty=1.0, sources=2):
    return SignalEvent(
        sid,
        date(y, m, d),
        tier,
        independent_source_count=sources,
        novelty=novelty,
        state_end_date=(date(*until) if until else None),
    )


# ---------------------------------------------------------------------------
# GATE: the 5 calibration scenarios, exact state-mode values.
# ---------------------------------------------------------------------------

SCENARIOS = [
    # name, eval_date, which, expected_capped, kwargs, events
    ("Jun 11 2025 evening", date(2025, 6, 11), "TACT", 100.0,
     dict(deadline=date(2025, 6, 12), deception=True),
     [E("B1", 2025, 6, 11, 1), E("B2", 2025, 6, 11, 1), E("B3", 2025, 6, 11, 1),
      E("B5", 2025, 6, 11, 2), E("D2", 2025, 6, 11, 2), E("G1", 2025, 6, 11, 2),
      E("A6", 2025, 5, 20, 2), E("A8", 2025, 5, 20, 2)]),
    ("Feb 21 2026", date(2026, 2, 21), "STRAT", 63.9, {},
     [E("H1", 2026, 1, 8, 2, until=(2026, 1, 20)), E("H2", 2026, 1, 9, 2),
      E("H3", 2025, 12, 28, 2),
      E("A1", 2026, 1, 25, 2, until=(2026, 4, 8)),
      E("A7", 2026, 1, 29, 2, until=(2026, 4, 8)),
      E("A3", 2026, 2, 21, 2, until=(2026, 4, 8)),
      E("G2", 2026, 2, 18, 1), E("D4", 2026, 2, 19, 2), E("D4", 2026, 1, 29, 2)]),
    ("Feb 27 2026", date(2026, 2, 27), "STRAT", 100.0, {},
     [E("H1", 2026, 1, 8, 2, until=(2026, 1, 20)), E("H2", 2026, 1, 9, 2),
      E("H3", 2025, 12, 28, 2),
      E("A1", 2026, 1, 25, 2, until=(2026, 4, 8)),
      E("A7", 2026, 1, 29, 2, until=(2026, 4, 8)),
      E("A3", 2026, 2, 21, 2, until=(2026, 4, 8)),
      E("G2", 2026, 2, 18, 1), E("G1", 2026, 2, 26, 1),
      E("C2", 2026, 2, 26, 1), E("C3", 2026, 2, 27, 1), E("D2", 2026, 2, 26, 2)]),
    ("Jul 7 2026", date(2026, 7, 7), "TACT", 87.3, {},
     [E("E1", 2026, 7, 6, 1), E("E2", 2026, 7, 7, 1), E("B3", 2026, 7, 7, 1),
      E("D3", 2026, 7, 1, 2), E("G1", 2026, 7, 7, 1)]),
    ("Quiet April 2025 week", date(2025, 4, 20), "STRAT", 8.1,
     dict(deadline=date(2025, 6, 11)),
     [E("D4", 2025, 4, 15, 2, novelty=0.3)]),
]


@pytest.mark.parametrize("name,at,which,expected,kwargs,events", SCENARIOS,
                         ids=[s[0] for s in SCENARIOS])
def test_calibration_scenarios(name, at, which, expected, kwargs, events):
    out = engine.score(events, at, which, **kwargs)
    assert out.capped == expected, f"{name}: got {out.capped}, want {expected}"


# ---------------------------------------------------------------------------
# Worked example 3 is NOT one of the 5 scenarios -- assert it separately.
# ---------------------------------------------------------------------------

def test_worked_example_3_both_scores():
    events = [E("E1", 2026, 7, 30, 1), E("E2", 2026, 7, 31, 1), E("E3", 2026, 7, 13, 1),
              E("E4", 2026, 7, 30, 2), E("B3", 2026, 7, 30, 1), E("D3", 2026, 7, 5, 2),
              E("A1", 2026, 4, 13, 2, until=(2026, 8, 1))]
    assessment = engine.evaluate(events, date(2026, 8, 1))
    assert assessment.tact.capped == 75.0
    assert assessment.strat.capped == 25.0
    assert assessment.tact.tier == 4
    assert assessment.strat.tier == 1


# ---------------------------------------------------------------------------
# Step 1 -- rumour gate: <2 independent sources is deleted for EVERY later step.
# ---------------------------------------------------------------------------

def test_rumour_gate_excludes_sub_two_sources():
    solo = E("B1", 2026, 7, 7, 1, sources=1)
    corroborated = E("B2", 2026, 7, 7, 1)
    assert engine.score([solo, corroborated], date(2026, 7, 7), "TACT").capped == \
        engine.score([corroborated], date(2026, 7, 7), "TACT").capped


def test_rumour_gate_blocks_floor():
    # B1 and B2 both "within 24h" but one is a lone rumour -> floor (a) must NOT fire.
    e1 = E("B1", 2026, 7, 7, 1, sources=1)   # rumour, deleted
    e2 = E("B2", 2026, 7, 7, 1)
    out = engine.score([e1, e2], date(2026, 7, 7), "TACT")
    assert out.capped < 70.0


# ---------------------------------------------------------------------------
# Step 3 -- stateful decay from state END, not first report.
# ---------------------------------------------------------------------------

def test_stateful_decays_from_state_end():
    # Same signal fired long ago but state active today -> age 0, full contribution.
    active = E("A1", 2026, 1, 1, 1, until=(2026, 8, 1))
    out = engine.score([active], date(2026, 6, 1), "STRAT")
    assert out.categories["A"] == 18.0  # 18 * 1.0 * 1.0 * 0.5^0


def test_stateful_decays_after_state_end():
    ended = E("A1", 2026, 1, 1, 1, until=(2026, 2, 1))
    out = engine.score([ended], date(2026, 6, 1), "STRAT")
    # age = 120 days, hl 14 -> 18 * 0.5^(120/14) << 18
    assert 0 < out.categories["A"] < 18.0


# ---------------------------------------------------------------------------
# Step 6 -- C1 deadline: final window (+20 both) vs distant (+8 STRAT only).
# ---------------------------------------------------------------------------

def test_c1_final_window_both_scores():
    at = date(2026, 6, 10)
    deadline = date(2026, 6, 12)  # 2 days left -> final
    a = engine.evaluate([], at, deadline=deadline)
    assert a.strat.categories["C"] == 20.0
    assert a.tact.categories["C"] == 20.0


def test_c1_distant_strat_only():
    at = date(2026, 5, 1)
    deadline = date(2026, 6, 12)  # >3 days -> +8 STRAT only
    a = engine.evaluate([], at, deadline=deadline)
    assert a.strat.categories["C"] == 8.0
    assert "C" not in a.tact.categories


# ---------------------------------------------------------------------------
# Step 10 -- floors, all three rules.
# ---------------------------------------------------------------------------

def test_floor_a_b1_b2():
    out = engine.score([E("B1", 2026, 7, 7, 1), E("B2", 2026, 7, 7, 1)], date(2026, 7, 7), "TACT")
    assert out.capped >= 70.0


def test_floor_b_e1_e2():
    out = engine.score([E("E1", 2026, 7, 6, 1), E("E2", 2026, 7, 7, 1)], date(2026, 7, 7), "TACT")
    assert out.capped == 75.0


def test_floor_c_b6_tier1():
    out = engine.score([E("B6", 2026, 7, 7, 1)], date(2026, 7, 7), "TACT")
    assert out.capped == 60.0


def test_floor_c_b6_tier3_ignored():
    out = engine.score([E("B6", 2026, 7, 7, 3)], date(2026, 7, 7), "TACT")
    assert out.capped < 60.0


# ---------------------------------------------------------------------------
# Step 9 -- deception (TACT only, requires a big A/B within 72h).
# ---------------------------------------------------------------------------

def test_deception_multiplies_only_with_big_ab():
    at = date(2026, 6, 11)
    b1 = E("B1", 2026, 6, 11, 1)  # 30 >= 15, within 72h
    plain = engine.score([b1], at, "TACT")
    dec = engine.score([b1], at, "TACT", deception=True)
    assert dec.capped > plain.capped


def test_deception_ignored_without_big_ab():
    at = date(2026, 6, 11)
    g1 = E("G1", 2026, 6, 11, 1)  # no A/B event
    plain = engine.score([g1], at, "TACT")
    dec = engine.score([g1], at, "TACT", deception=True)
    assert dec.capped == plain.capped


# ---------------------------------------------------------------------------
# Score membership -- the dual-membership signals.
# ---------------------------------------------------------------------------

def test_membership():
    assert is_strat_signal("E4") and is_tact_signal("E4")      # E4 in both
    assert is_strat_signal("D3") and is_tact_signal("D3")      # D3 in both
    assert is_strat_signal("D2") and not is_tact_signal("D2")  # D2 STRAT-only
    assert not is_strat_signal("A1") or is_strat_signal("A1")  # A1 STRAT-only
    assert not is_tact_signal("A1")
    assert is_tact_signal("B1") and not is_strat_signal("B1")


# ---------------------------------------------------------------------------
# Tier thresholds and Step 12 (alert decision + regime).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    (0.0, 0), (14.9, 0), (15.0, 1), (34.9, 1), (35.0, 2),
    (54.9, 2), (55.0, 3), (74.9, 3), (75.0, 4), (100.0, 4),
])
def test_tier(score, expected):
    assert tier(score) == expected


def test_alert_decision_backtest_cases():
    hist = [75.0] * 24  # wartime
    a = alert_decision(hist, {"E", "B"}, {"E", "B", "D"}, False, 0.0)
    assert a == "BASELINE-ONE-LINER"
    b = alert_decision(hist, {"E", "B"}, {"E", "D"}, False, 0.0)
    assert b == "ALERT-NEW-DIMENSION"


def test_alert_decision_normal_regime():
    assert alert_decision([30.0] * 7, {"E"}, {"E"}, False, 0.0) == "NORMAL-RULES"


def test_alert_decision_delta_priority():
    hist = [75.0] * 7
    assert alert_decision(hist, {"E"}, {"E"}, False, 11.0) == "ALERT-DELTA"


def test_next_regime_enter_and_exit():
    assert next_regime("NORMAL", [75.0] * 7) == "WARTIME"
    assert next_regime("NORMAL", [75.0] * 6 + [74.0]) == "NORMAL"
    assert next_regime("WARTIME", [54.0] * 7) == "NORMAL"
    assert next_regime("WARTIME", [54.0] * 6 + [55.0]) == "WARTIME"
