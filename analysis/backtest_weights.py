#!/usr/bin/env python3
"""Backtest of ESCALATION_SCORING.md draft weights against the five §2 calibration
targets. Pure stdlib, deterministic, no network. Run: python3 backtest_weights.py
Writes backtest_results.csv next to this file.

Documented assumptions (spec gaps, flagged in results):
  A1. tier_mult: Tier1=1.0, Tier2=0.8, Tier3=0.5 (Tier3 nominate-only per §5).
  A2. Two decay modes tested. 'decay': age from first report (spec as written).
      'state': posture signals (A1,A2,A3,A4,A7) are STATES — age=0 while the
      posture persists; decay starts when it ends. H1 crackdown likewise.
  A3. Convergence counts distinct categories contributing to THAT score whose
      event fired (or state active) within 72h of evaluation.
"""
import csv, math, os
from datetime import date

TIER = {1: 1.0, 2: 0.8, 3: 0.5}
STATEFUL = {"A1", "A2", "A3", "A4", "A7", "H1"}
# id: (base, half_life_days)
CAT = {
    "A1": (18, 14), "A2": (22, 14), "A3": (20, 10), "A4": (20, 10), "A5": (16, 4),
    "A6": (14, 10), "A7": (12, 14), "A8": (10, 14), "A9": (10, 7),
    "B1": (30, 3), "B2": (25, 3), "B3": (15, 3), "B4": (15, 3), "B5": (12, 2), "B6": (18, 2),
    "C2": (16, 5), "C3": (20, 4), "C4": (14, 10), "C5": (10, 21), "C6": (12, 7),
    "D1": (14, 4), "D2": (10, 3), "D3": (12, 4), "D4": (2, 2),
    "E1": (35, 3), "E2": (15, 3), "E3": (30, 3), "E4": (12, 5), "E5": (10, 3), "E6": (8, 4),
    "G1": (6, 2), "G2": (4, 3),
    "H1": (12, 30), "H2": (14, 14), "H3": (8, 14),
}
STRAT_CATS = {"A", "C", "D", "H", "G"}          # +E4 handled below
TACT_CATS = {"B", "E", "G"}                       # +C1 final window, +D1/D3


def contrib(ev, at, mode):
    sid, fired, tier = ev["id"], ev["date"], ev["tier"]
    base, hl = CAT[sid]
    age = (at - fired).days
    if age < 0:
        return 0.0
    if mode == "state" and sid in STATEFUL:
        until = ev.get("until", at)
        age = 0 if until >= at else (at - until).days
    nov = ev.get("novelty", 1.0)
    return base * TIER[tier] * nov * 0.5 ** (age / hl)


def in_tact(sid):
    return sid[0] in TACT_CATS or sid in ("D1", "D3")


def in_strat(sid):
    return (sid[0] in STRAT_CATS) or sid == "E4"


def recent(ev, at, mode):
    if mode == "state" and ev["id"] in STATEFUL and ev.get("until", at) >= at:
        return True
    return 0 <= (at - ev["date"]).days <= 3


def score(events, at, which, mode, deadline=None, deception=False):
    per_cat, cats72 = {}, set()
    sel = in_tact if which == "TACT" else in_strat
    for ev in events:
        if not sel(ev["id"]):
            continue
        c = contrib(ev, at, mode)
        if c <= 0.01:
            continue
        letter = ev["id"][0]
        per_cat[letter] = per_cat.get(letter, 0.0) + c
        if recent(ev, at, mode):
            cats72.add(letter)
    if deadline is not None and (deadline - at).days >= 0:
        final = (deadline - at).days <= 3
        if which == "STRAT":
            per_cat["C"] = per_cat.get("C", 0.0) + (20 if final else 8)
            cats72.add("C")
        elif final:
            per_cat["C"] = per_cat.get("C", 0.0) + 20
            cats72.add("C")
    per_cat["D"] = min(per_cat.get("D", 0.0), 20)
    per_cat["G"] = min(per_cat.get("G", 0.0), 10)
    raw = sum(per_cat.values())
    conv = min(1.6, 1 + 0.15 * max(0, len(cats72) - 1))
    s = raw * conv
    if which == "TACT" and deception:
        big_ab = any(ev["id"][0] in "AB" and contrib(ev, at, mode) >= 15
                     and recent(ev, at, mode) for ev in events)
        if big_ab:
            s *= 1.3
    # floor rules
    if which == "TACT":
        ids72 = {e["id"] for e in events if 0 <= (at - e["date"]).days <= 1}
        if {"B1", "B2"} <= ids72:
            s = max(s, 70)
        f7 = {e["id"] for e in events if 0 <= (at - e["date"]).days <= 7}
        if {"E1", "E2"} <= f7:
            s = max(s, 75)
        if any(e["id"] == "B6" and e["tier"] <= 2 and recent(e, at, mode) for e in events):
            s = max(s, 60)
    return min(100.0, s)


def E(sid, y, m, d, tier, until=None, novelty=1.0):
    ev = {"id": sid, "date": date(y, m, d), "tier": tier, "novelty": novelty}
    if until:
        ev["until"] = date(*until)
    return ev


SCENARIOS = [
    dict(name="Jun 11 2025 evening", at=date(2025, 6, 11), which="TACT",
         target=">=75", lo=75, hi=100, deadline=date(2025, 6, 12), deception=True,
         events=[E("B1", 2025, 6, 11, 1), E("B2", 2025, 6, 11, 1), E("B3", 2025, 6, 11, 1),
                 E("B5", 2025, 6, 11, 2), E("D2", 2025, 6, 11, 2), E("G1", 2025, 6, 11, 2),
                 E("A6", 2025, 5, 20, 2), E("A8", 2025, 5, 20, 2)]),
    dict(name="Feb 21 2026", at=date(2026, 2, 21), which="STRAT",
         target="55-70", lo=55, hi=70,
         events=[E("H1", 2026, 1, 8, 2, until=(2026, 1, 20)), E("H2", 2026, 1, 9, 2),
                 E("H3", 2025, 12, 28, 2),
                 E("A1", 2026, 1, 25, 2, until=(2026, 4, 8)),
                 E("A7", 2026, 1, 29, 2, until=(2026, 4, 8)),
                 E("A3", 2026, 2, 21, 2, until=(2026, 4, 8)),
                 E("G2", 2026, 2, 18, 1), E("D4", 2026, 2, 19, 2), E("D4", 2026, 1, 29, 2)]),
    dict(name="Feb 27 2026", at=date(2026, 2, 27), which="STRAT",
         target=">=70", lo=70, hi=100,
         events=[E("H1", 2026, 1, 8, 2, until=(2026, 1, 20)), E("H2", 2026, 1, 9, 2),
                 E("H3", 2025, 12, 28, 2),
                 E("A1", 2026, 1, 25, 2, until=(2026, 4, 8)),
                 E("A7", 2026, 1, 29, 2, until=(2026, 4, 8)),
                 E("A3", 2026, 2, 21, 2, until=(2026, 4, 8)),
                 E("G2", 2026, 2, 18, 1), E("G1", 2026, 2, 26, 1),
                 E("C2", 2026, 2, 26, 1), E("C3", 2026, 2, 27, 1), E("D2", 2026, 2, 26, 2)]),
    dict(name="Jul 7 2026", at=date(2026, 7, 7), which="TACT",
         target=">=75", lo=75, hi=100,
         events=[E("E1", 2026, 7, 6, 1), E("E2", 2026, 7, 7, 1), E("B3", 2026, 7, 7, 1),
                 E("D3", 2026, 7, 1, 2), E("G1", 2026, 7, 7, 1)]),
    dict(name="Quiet April 2025 week", at=date(2025, 4, 20), which="STRAT",
         target="<15", lo=0, hi=15, deadline=date(2025, 6, 11),
         events=[E("D4", 2025, 4, 15, 2, novelty=0.3)]),
]


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results.csv")
    rows = []
    for sc in SCENARIOS:
        for mode in ("decay", "state"):
            s = score(sc["events"], sc["at"], sc["which"], mode,
                      sc.get("deadline"), sc.get("deception", False))
            ok = sc["lo"] <= s <= sc["hi"] if sc["hi"] < 100 else s >= sc["lo"]
            if sc["name"].startswith("Quiet"):
                ok = s < 15
            rows.append([sc["name"], sc["which"], sc["target"], mode, round(s, 1),
                         "PASS" if ok else "FAIL"])
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "score_type", "target", "decay_mode", "score", "result"])
        w.writerows(rows)
    for r in rows:
        print("{:26s} {:5s} target {:7s} mode={:5s} -> {:6.1f}  {}".format(
            r[0], r[1], r[2], r[3], r[4], r[5]))


def alert_decision(tact_history, cats_today, cats_last14, strat_tier_rose, delta):
    """Step 12 of SCORING_RULEBOOK.md. Message layer only; scores untouched."""
    wartime = len(tact_history) >= 7 and all(t >= 75 for t in tact_history[-7:])
    if not wartime:
        return "NORMAL-RULES"
    if delta >= 10:
        return "ALERT-DELTA"
    if cats_today - cats_last14:
        return "ALERT-NEW-DIMENSION"
    if strat_tier_rose:
        return "ALERT-STRAT-RISE"
    return "BASELINE-ONE-LINER"


def alert_tests():
    hist = [75.0] * 24                                   # Jul 8 -> Aug 1 cadence
    a = alert_decision(hist, {"E", "B"}, {"E", "B", "D"}, False, 0.0)
    b = alert_decision(hist, {"E", "B"}, {"E", "D"}, False, 0.0)   # first B in >14d
    print("day-24 no-change ->", a, "| PASS" if a == "BASELINE-ONE-LINER" else "| FAIL")
    print("day-24 fresh B1  ->", b, "| PASS" if b == "ALERT-NEW-DIMENSION" else "| FAIL")


if __name__ == "__main__":
    alert_tests()
    main()
