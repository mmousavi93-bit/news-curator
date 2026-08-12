# Escalation Scoring System — Specification v1

Deterministic scoring for imminent-attack warning (Tehran resident). Derived from the
empirical analysis in `WAR_SIGNALS_PAPER.md`. Designed to drop into `risk/engine.py` +
`config/risk_weights.yaml` per project hard constraints: LLM extracts discrete signals
only; all arithmetic is Python; identical input → identical score.

## 1. Architecture

Two scores computed each run, both 0–100:

- **STRAT** — P(major escalation ≤ 7 days). Slow half-lives, posture-heavy.
- **TACT** — P(strike wave on Iran ≤ 48h). Fast half-lives, trigger-heavy.

Displayed alert level = max of the two mapped tiers. Signals are typed events with
`(signal_id, category, timestamp, source_tier, corroboration_count)`. The LLM's only job
is to map a news cluster to zero or more `signal_id`s with a quote + source list
(see `AGENT_PROMPT.md`). Everything below is pure Python.

## 2. Signal catalog and base weights

Weights reflect specificity × cost-to-fake. Calibration contract — tune weights/conv until
historical reconstructions produce: Jun 11 2025 evening → TACT ≥ 75 (Tier 4); Feb 21 2026 →
STRAT 55–70 (Tier 3); Feb 27 2026 → STRAT ≥ 70; Jul 7 2026 → TACT ≥ 75; any quiet April
2025 week → < 15. The backtest is automated: `analysis/backtest_weights.py` (stdlib-only,
deterministic, results in `analysis/backtest_results.csv`). As of 2026-08-01 all five pass
under the rules below (Jun 11 saturates to 100 — acceptable, strike came 40h later;
Feb 21 lands at 63.9). **Feb 21 fails (scores 40) if posture signals are decayed from first
report — the posture-persistence rule below is load-bearing, not cosmetic.** Run the
backtest whenever weights or rules change; it becomes the Phase 8 gate.

**Tier multipliers** (previously unspecified): Tier 1 = 1.0, Tier 2 = 0.8, Tier 3 = 0.5.
Tier 3 nominates only and can never corroborate alone (§5).

Contribution per signal = `base × tier_mult × novelty × decay(age)`. Uncorroborated
(single independent source) → RUMOUR → contributes 0 (constraint 10).

### Category A — Force posture (STRAT-dominant)
| id | signal | base | half-life |
|---|---|---|---|
| A1 | Carrier strike group ordered toward/into CENTCOM | 18 | 14d |
| A2 | 2+ CSGs simultaneously in theater | 22 | 14d |
| A3 | THAAD/Patriot/air-defense battery newly emplaced in region | 20 | 10d |
| A4 | Strategic bombers deployed in range (Diego Garcia etc.) | 20 | 10d |
| A5 | Tanker-aircraft surge (≥10 crossing to theater) | 16 | 4d |
| A6 | Munitions shipments / pre-positioning reported | 14 | 10d |
| A7 | Bystander hedging (Gulf states restrict US base/airspace access) | 12 | 14d |
| A8 | Israeli force regeneration / large air exercise completed | 10 | 14d |
| A9 | Reserve call-ups / leave cancellations / mobilization orders (any side) | 10 | 7d |

### Category B — Apparatus protection (TACT-dominant, highest single-signal value)
| id | signal | base | half-life |
|---|---|---|---|
| B1 | Ordered departure, US embassy in region | 30 | 3d |
| B2 | Military-family departures authorized (CENTCOM) | 25 | 3d |
| B3 | UKMTO/JMIC/MARAD advisory with escalation language above baseline | 15 | 3d |
| B4 | Pre-emptive airline suspension / war-risk insurance spike | 15 | 3d |
| B5 | Official event anomaly: cancelled testimony/press briefing/trip of relevant commander | 12 | 2d |
| B6 | Airspace closure / major NOTAM restriction in-theater (either side, pre-strike) | 18 | 2d |

Rule: **absence of B never subtracts** (Feb 2026: tell deliberately withheld).

### Category C — Diplomatic calendar (both scores)
| id | signal | base | half-life |
|---|---|---|---|
| C1 | Explicit deadline/ultimatum set with date → tracked as countdown; +8 static, escalating to 20 in final 72h of the window | special | n/a |
| C2 | Final/high-stakes talks round ends: no deal + maximalist demands | 16 | 5d |
| C3 | **Ghost meeting**: scheduled talks/summit fails to convene | 20 | 4d |
| C4 | IAEA censure / non-compliance finding / snapback trigger | 14 | 10d |
| C5 | New legal predicate (FTO designation, AUMF-analog, war-powers vote failing) | 10 | 21d |
| C6 | Mediator activity collapse (broker publicly quits/blames) | 12 | 7d |

### Category D — Rhetoric (capped: total D contribution ≤ 20)
| id | signal | base | half-life |
|---|---|---|---|
| D1 | Head-of-state ultimatum with explicit timeframe or named targets | 14 | 4d |
| D2 | Head-of-state optimism→pessimism flip on live negotiations | 10 | 3d |
| D3 | IRGC/Iran language shift: route-control → closure/mining/"crush" | 12 | 4d |
| D4 | Chronic existential threats, anonymous "preparing to strike" stories | 2 | 2d |

### Category E — Kinetic triggers (TACT-dominant; the July 2026 law)
| id | signal | base | half-life |
|---|---|---|---|
| E1 | Iranian strike on commercial vessel(s) in Hormuz/Omani waters | 35 | 72h |
| E2 | Second vessel/asset strike within 7 days of prior | +15 | 72h |
| E3 | Attack on US base/asset in Gulf | 30 | 72h |
| E4 | Proxy front widening (PMF/Hezbollah/Houthi strikes or strikes on them) | 12 | 5d |
| E5 | Interception of major attack / near-miss disclosed | 10 | 3d |
| E6 | Major cyberattack on critical infrastructure/military comms (attributed, corroborated) | 8 | 4d |

### Category F — Deception/anomaly (multiplier, not additive)
If official de-escalatory messaging (vacation stories, "deal is close", strike denials)
co-occurs within 72h with any A or B signal ≥15 points: multiply TACT by **1.3** and tag
message "pattern consistent with pre-strike deception (seen Jun 2025, Feb 2026)". Never
let soothing statements reduce a score driven by physical signals.

### Category G — Market confirmation (cap 10 total; confirm-only)
| id | signal | base | half-life |
|---|---|---|---|
| G1 | Oil +5% intraday without supply-side news | 6 | 2d |
| G2 | Gold record/spike concurrent with regional military news | 4 | 3d |

### Category H — Internal shock (STRAT-only)
| id | signal | base | half-life |
|---|---|---|---|
| H1 | Mass protests + lethal crackdown order | 12 | 30d |
| H2 | National internet blackout | 14 | 14d |
| H3 | Currency collapse (rial step-change >15%/week) | 8 | 14d |

## 3. Aggregation (deterministic)

```
raw   = Σ contributions (category caps applied: D≤20, G≤10)
conv  = 1 + 0.15 × (distinct_categories_firing_in_72h − 1), capped at 1.6
score = min(100, raw × conv × F_multiplier_if_TACT)
```

- STRAT sums categories A, C, D, H (+E4, +G); TACT sums B, C1-final-window, D1/D3, E, F, G.
- **Convergence multiplier** implements Lesson: 3+ categories in 72h is a war signature.
  It counts distinct categories contributing to *that* score whose signal fired (or whose
  state is active) within the 72h window.
- **Posture persistence:** stateful signals — A1, A2, A3, A4, A7, H1 — describe a *state*,
  not an event. While the state demonstrably holds (carrier still in theater, battery still
  emplaced, crackdown ongoing), decay age is 0; once the state ends, decay age counts
  from the state's end date, not first report. Empirical basis: USS Lincoln was in theater
  on Feb 21 2026 but 27 days past its first report; first-report decay had cut it to 26%
  weight and the backtest missed Tier 3 by 15 points.
- **Novelty factor:** per signal_id, weight × 0.6 after each firing that is followed by 14
  quiet days (min 0.3); reset to 1.0 when any A/B/E signal ≥15 fires. Implements
  warning-fatigue discount with expensive-category reset.
- **Trend delta:** report score vs. previous run and vs. 7-day mean; deltas matter more
  than levels (constraint: identical input → identical score keeps deltas meaningful).
- **Floor rules** (override, applied after everything else): B1+B2 within 24h → TACT ≥ 70
  (the Jun 11 2025 signature); E1 followed by E2 → TACT ≥ 75 (the Jul 2026 cadence law);
  B6 airspace closure corroborated at Tier 1–2 → TACT ≥ 60. Floors guarantee that a proven
  near-sufficient signature is never diluted by a quiet week's decay arithmetic.

## 4. Alert tiers → owner guidance (message composer)

| Tier | Score | Meaning | One-line guidance to include |
|---|---|---|---|
| 0 | <15 | baseline | — |
| 1 | 15–34 | elevated | "Worth topping up: cash, fuel, meds, water." |
| 2 | 35–54 | high | "Go-bag ready; charge power banks; know your low-rise/basement option; keep car fueled." |
| 3 | 55–74 | severe (≤7d plausible) | "Avoid vicinity of known military/IRGC/leadership sites; offline maps; agree family rally point; expect internet blackout." |
| 4 | 75+ | imminent (≤48h plausible) | "Sleep away from high-value-target districts; water stored; documents + cash on person; assume comms loss." |

Tier transitions, not levels, drive message prominence (per-run messages stay short when
nothing changed). Tier 3+ additionally recommends enabling the high-frequency cron flag.

**Sustained-conflict (WARTIME) regime** — added 2026-08-01: during a prolonged exchange
cadence the E1+E2 floor pins TACT ≥ 75 indefinitely, which would produce a Tier-4 siren
every day — warning-fatigue suicide. Enter WARTIME after 7 consecutive days of TACT ≥ 75;
exit after 7 consecutive days < 55 (hysteresis; exit is itself an alert). In WARTIME the
daily message collapses to a one-liner, and full alerts fire only on (i) score ≥ trailing
7-day mean + 10, (ii) a category silent ≥ 14 days firing (the false-negative guard under
a saturated score), or (iii) a STRAT tier rise. Scores themselves are never suppressed —
only the message layer changes. Full procedure: `SCORING_RULEBOOK.md` Step 12.

## 5. Digestion strategy — free sources, tiered

Feeds land in `config/sources.yaml`; tiers in `config/credibility.yaml`. All free/public.

**Tier 1 — mechanical/official (highest credibility, low volume; poll every run):**
UKMTO advisories (ukmto.org, RSS/page), US embassy security alerts (RSS per post),
State Dept travel advisories (RSS), IAEA news (RSS), CENTCOM releases, ISW Iran updates
(RSS), safeairspace.net (NOTAM-level airspace risk), Kpler/TankerTrackers public posts via
t.me/s/ where available, FRED series for G-signals (free API).

**Tier 2 — quality press (RSS, corroboration layer):**
Reuters/AP world RSS, Al Jazeera, BBC (en + Persian), Times of Israel, Al-Monitor,
Amwaj.media, Tehran Times + IRNA/Press TV (regime signal channel — credibility-tiered low
for facts, high for *official-language-shift detection*, D3/H signals), Iran International
(opposition bias flagged), Naval News, The War Zone (twz.com), Aviationist.

**Tier 3 — OSINT via t.me/s/ public previews (per constraint 6):**
tanker/flight-tracking channels, regional incident aggregators. Tier-3 items can *nominate*
signals but never confirm alone; corroboration requires Tier 1–2. This is where tanker
surges (A5) and GPS-jamming anomalies surface first.

**Digestion rules:**
1. Cluster first (existing pipeline). LLM maps each cluster → signal_ids or `none`.
2. An item is **kept** iff it maps to a signal_id, updates a tracked countdown (C1), or
   updates a scheduled-event registry entry. Everything else is discarded regardless of
   how dramatic it reads. This is the volume-reduction contract.
3. **Scheduled-event registry** (new, small SQLite table): upcoming talks, deadlines,
   IAEA board dates, announced meetings. Each run checks for ghost meetings (C3) and
   expiring countdowns (C1). The two wars' best short-lead signals were *non-events* —
   without a registry the agent cannot see them.
4. Rhetoric is scored only for *shifts* against each speaker's 30-day baseline (D2/D3),
   never for absolute harshness (Katz lesson: chronic threats carry no timing info).
5. Deception rule: soothing official statements are stored as F-inputs, never as
   negative evidence.

## 6. Known failure modes

- **Suppressed tells (proven):** Feb 2026 withheld evacuations. Mitigation: no category is
  necessary; A/C/E alone can reach Tier 4.
- **Compressed onset (proven):** Jun 2025's 48h window with 3h polling → worst-case ~5h
  blind spot. Mitigation: Tier 3+ auto-recommends 1h cron flag (still free on public repo).
- **Regime-side surprise:** Iranian retaliation waves need no US posture signals; E and D3
  carry that load.
- **Deliberate market head-fakes / single-source OSINT hoaxes:** RUMOUR exclusion + G cap.
- **Model gaming:** this file is public; weights are too. Acceptable — the signals that
  matter (A, E, C-ghosts) are expensive to spoof by construction.

## 7. Review of `deep-research-report.md` (other-AI report) — adopted vs. rejected

**Adopted:** airspace-closure/NOTAM signal (→ B6), mobilization signals (→ A9), cyber
category (→ E6, low weight — its own examples were self-labeled hypothetical), explicit
minimum-score floor for critical combos (→ §3 floor rules), multi-language coverage
reminder (already a project property).

**Rejected, with reasons:** (1) Factual errors — dates War A onset to Jun 22 (strike was
Jun 13); treats the Jun 16 tanker surge as a pre-war signal when it was post-strike
reinforcement; cites a "US Embassy Tehran" evacuation cable (no such embassy exists).
Do not backtest against its timeline. (2) Binary signal detection with ad-hoc
normalization — not deterministic, violates constraint 3. (3) No deception handling: it
would read official calm as de-escalation, the exact failure mode of Jun 2025/Feb 2026.
(4) No non-event (ghost-meeting/deadline) detection — the best short-lead signal class in
both wars. (5) No warning-fatigue/novelty mechanism. (6) Recommended paid feeds
(Bloomberg, Jane's, Maxar, Windward, X API) — violate the zero-cost constraint.
(7) Additive social-media weight without corroboration gate — violates constraint 10.
