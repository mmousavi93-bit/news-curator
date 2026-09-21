"""Deterministic risk scoring engine. No LLM calls permitted in this package.

Constraint 3: risk scores are deterministic Python, never LLM output. This
module is SCORING_RULEBOOK.md v1 translated 1:1 into code, consuming
config/risk_weights.yaml so the weights and the scorer cannot drift apart.
The gate is: identical input -> identical score, and the 5 calibration
scenarios in analysis/backtest_weights.py land in their bands.

Only STRAT and TACT are scored here. MSTRESS (ECONOMIC_SHOCK_SCORING.md) is a
separate, deferred spec -- the risk_history columns for it are nullable.

Novelty is an INPUT (SignalEvent.novelty, default 1.0). The Step-2 quiet-cycle
reset belongs to the storage/extraction layer, not this pure scorer; the
backtest (and this module's gate) passes novelty explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

from agent.config import ConfigError

# Category letters per score (SCORING_RULEBOOK.md "Score membership").
_STRAT_LETTERS = frozenset("ACDHG")
_TACT_LETTERS = frozenset("BEG")

# Signals that belong to a score despite their leading letter.
_STRAT_EXTRA = frozenset({"E4"})
_TACT_EXTRA = frozenset({"D1", "D3"})


def is_strat_signal(signal_id: str) -> bool:
    return signal_id[0] in _STRAT_LETTERS or signal_id in _STRAT_EXTRA


def is_tact_signal(signal_id: str) -> bool:
    return signal_id[0] in _TACT_LETTERS or signal_id in _TACT_EXTRA


def tier(score: float) -> int:
    """0-14.9 -> 0 | 15-34.9 -> 1 | 35-54.9 -> 2 | 55-74.9 -> 3 | 75+ -> 4."""
    if score < 15:
        return 0
    if score < 35:
        return 1
    if score < 55:
        return 2
    if score < 75:
        return 3
    return 4


@dataclass(frozen=True)
class SignalEvent:
    """One extracted signal. Five fields (rulebook INPUTS), plus the stateful
    state_end_date. independent_source_count defaults to 2 so pre-gated
    backtest scenarios pass the Step-1 rumour gate untouched."""

    signal_id: str
    event_date: date
    source_tier: int
    independent_source_count: int = 2
    novelty: float = 1.0
    state_end_date: date | None = None


@dataclass(frozen=True)
class ScoreOutcome:
    which: str
    capped: float                     # min(100, uncapped), rounded to 1dp
    uncapped: float                   # full precision (deltas, session-2 d3)
    categories: Mapping[str, float]   # per-category totals AFTER caps (detail)
    recent_categories: frozenset[str]  # cats within 72h / active state (Step 8)
    tier: int


@dataclass(frozen=True)
class RiskAssessment:
    eval_date: date
    strat: ScoreOutcome
    tact: ScoreOutcome


class RiskEngine:
    """Loads config/risk_weights.yaml and scores STRAT + TACT deterministically."""

    def __init__(self, weights: Mapping[str, object]) -> None:
        self._catalog, self._c1_static, self._c1_final = self._parse_signals(weights)
        self._stateful = frozenset(weights.get("stateful") or ())
        self._tier_mult = self._parse_tiers(weights)

    @staticmethod
    def _parse_signals(weights: Mapping[str, object]):
        raw = weights.get("signals")
        if not isinstance(raw, dict) or not raw:
            raise ConfigError("risk_weights.yaml: expected a 'signals' mapping")
        catalog: dict[str, tuple[float, float]] = {}
        for sid, spec in raw.items():
            if not isinstance(spec, dict) or "base" not in spec:
                raise ConfigError(f"risk_weights.yaml: signals.{sid!r} must map to a dict with 'base'")
            if sid == "C1":
                continue  # Step-6 deadline; static values, not decay
            hl = spec.get("half_life_days")
            if not isinstance(hl, (int, float)):
                raise ConfigError(f"risk_weights.yaml: signals.{sid} missing 'half_life_days'")
            catalog[sid] = (float(spec["base"]), float(hl))
        c1 = raw.get("C1") or {}
        return catalog, float(c1.get("base", 8.0)), float(c1.get("final_72h", 20.0))

    @staticmethod
    def _parse_tiers(weights: Mapping[str, object]) -> dict[int, float]:
        tm = weights.get("tier_multipliers")
        if not isinstance(tm, dict):
            raise ConfigError("risk_weights.yaml: expected 'tier_multipliers'")
        return {int(k): float(v) for k, v in tm.items()}

    def _contribution(self, ev: SignalEvent, at: date) -> float:
        base, hl = self._catalog[ev.signal_id]
        age = (at - ev.event_date).days
        if age < 0:
            return 0.0
        if ev.signal_id in self._stateful:
            until = ev.state_end_date if ev.state_end_date is not None else at
            age = 0 if until >= at else (at - until).days
        return base * self._tier_mult[ev.source_tier] * ev.novelty * 0.5 ** (age / hl)

    def _recent(self, ev: SignalEvent, at: date) -> bool:
        if ev.signal_id in self._stateful:
            until = ev.state_end_date if ev.state_end_date is not None else at
            if until >= at:
                return True
        return 0 <= (at - ev.event_date).days <= 3

    def score(
        self,
        events: Sequence[SignalEvent],
        eval_date: date,
        which: str,
        *,
        deadline: date | None = None,
        deception: bool = False,
    ) -> ScoreOutcome:
        """Steps 1-11. `which` is "STRAT" or "TACT"."""
        sel = is_tact_signal if which == "TACT" else is_strat_signal
        # Step 1 -- rumour gate.
        gated = [e for e in events if e.independent_source_count >= 2]
        per_cat: dict[str, float] = {}
        recent: set[str] = set()
        for ev in gated:
            if not sel(ev.signal_id):
                continue
            c = self._contribution(ev, eval_date)
            if c <= 0.01:  # Step 5 -- discarded, cannot trigger convergence
                continue
            letter = ev.signal_id[0]
            per_cat[letter] = per_cat.get(letter, 0.0) + c
            if self._recent(ev, eval_date):
                recent.add(letter)

        # Step 6 -- C1 deadline.
        if deadline is not None and deadline >= eval_date:
            final = (deadline - eval_date).days <= 3
            if which == "STRAT":
                per_cat["C"] = per_cat.get("C", 0.0) + (self._c1_final if final else self._c1_static)
                recent.add("C")
            elif final:
                per_cat["C"] = per_cat.get("C", 0.0) + self._c1_final
                recent.add("C")

        # Step 7 -- category caps.
        per_cat["D"] = min(per_cat.get("D", 0.0), 20.0)
        per_cat["G"] = min(per_cat.get("G", 0.0), 10.0)
        raw = sum(per_cat.values())

        # Step 8 -- convergence.
        conv = min(1.6, 1.0 + 0.15 * max(0, len(recent) - 1))
        uncapped = raw * conv

        # Step 9 -- deception (TACT only).
        if which == "TACT" and deception and self._big_ab(gated, eval_date):
            uncapped *= 1.3

        # Step 10 -- floors (TACT only).
        if which == "TACT":
            uncapped = max(uncapped, self._floor(gated, eval_date))

        capped = round(min(100.0, uncapped), 1)
        return ScoreOutcome(which, capped, uncapped, dict(per_cat), frozenset(recent), tier(capped))

    def _big_ab(self, events: Sequence[SignalEvent], at: date) -> bool:
        return any(
            e.signal_id[0] in "AB"
            and self._contribution(e, at) >= 15.0
            and self._recent(e, at)
            for e in events
        )

    def _floor(self, events: Sequence[SignalEvent], at: date) -> float:
        f = 0.0
        ids24 = {e.signal_id for e in events if 0 <= (at - e.event_date).days <= 1}
        if {"B1", "B2"} <= ids24:
            f = max(f, 70.0)
        ids7 = {e.signal_id for e in events if 0 <= (at - e.event_date).days <= 7}
        if {"E1", "E2"} <= ids7:
            f = max(f, 75.0)
        if any(e.signal_id == "B6" and e.source_tier <= 2 and self._recent(e, at) for e in events):
            f = max(f, 60.0)
        return f

    def evaluate(
        self,
        events: Sequence[SignalEvent],
        eval_date: date,
        *,
        deadline: date | None = None,
        deception: bool = False,
    ) -> RiskAssessment:
        return RiskAssessment(
            eval_date,
            self.score(events, eval_date, "STRAT", deadline=deadline, deception=deception),
            self.score(events, eval_date, "TACT", deadline=deadline, deception=deception),
        )


def alert_decision(
    tact_history: Sequence[float],
    categories_today: set[str],
    categories_last14: set[str],
    strat_tier_rose: bool,
    delta: float,
) -> str:
    """Step 12, message layer only (scores never modified here). Callers first
    check regime(); this decides what to say INSIDE wartime. In NORMAL the
    caller uses its own tier-transition rule (not modelled here)."""
    if len(tact_history) >= 7 and all(t >= 75 for t in tact_history[-7:]):
        if delta >= 10:
            return "ALERT-DELTA"
        if categories_today - categories_last14:
            return "ALERT-NEW-DIMENSION"
        if strat_tier_rose:
            return "ALERT-STRAT-RISE"
        return "BASELINE-ONE-LINER"
    return "NORMAL-RULES"


def next_regime(current: str, tact_history: Sequence[float]) -> str:
    """WARTIME if the last 7 daily TACTs are all >= 75; exit only after 7
    consecutive < 55 (de-escalation is itself a full alert)."""
    if current == "WARTIME":
        return "NORMAL" if len(tact_history) >= 7 and all(t < 55 for t in tact_history[-7:]) else "WARTIME"
    return "WARTIME" if len(tact_history) >= 7 and all(t >= 75 for t in tact_history[-7:]) else "NORMAL"
