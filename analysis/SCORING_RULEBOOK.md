# SCORING RULEBOOK — mechanical calculation procedure v1

Purpose: any agent (or human) following these steps with a calculator MUST reproduce the
reference implementation (`analysis/backtest_weights.py`) to the decimal. No judgment
calls exist below; if you think a step needs interpretation, the rulebook has a bug —
report it, do not improvise. In production, scoring is Python (`risk/engine.py`,
constraint 3) — this document is its human-readable twin and the manual fallback.

## INPUTS you must have before starting

1. A list of SIGNAL EVENTS. Each event has exactly five fields:
   `signal_id` (from the catalog below), `event_date`, `source_tier` (1, 2 or 3),
   `independent_source_count` (integer), `novelty` (default 1.0 — see Step 2).
   Stateful signals (marked ★ below) also carry `state_end_date` (the last date the
   state was confirmed still true; blank = still active today).
2. EVALUATION DATE ("today").
3. Any ACTIVE DEADLINE (C1): a date somebody in authority explicitly set.
4. DECEPTION FLAG: TRUE if any recorded soothing statement (denial, vacation story,
   "deal is close") is dated within 72h of the evaluation date. Otherwise FALSE.

## CONSTANTS

Tier multiplier: Tier 1 = 1.0 | Tier 2 = 0.8 | Tier 3 = 0.5
Score membership:
  STRAT uses ids starting with A, C, D, H, G — plus E4 — plus C1 (Step 6).
  TACT  uses ids starting with B, E, G — plus D1 and D3 — plus C1 only in final window.
Category caps (per score): D-total ≤ 20, G-total ≤ 10.
Convergence: 1 + 0.15 × (N − 1), maximum 1.6 (N defined in Step 8).
Deception multiplier (TACT only): × 1.3 (condition in Step 9).

CATALOG — signal_id: (BASE, HALF_LIFE in days). ★ = stateful.
A1★(18,14) A2★(22,14) A3★(20,10) A4★(20,10) A5(16,4) A6(14,10) A7★(12,14) A8(10,14) A9(10,7)
B1(30,3) B2(25,3) B3(15,3) B4(15,3) B5(12,2) B6(18,2)
C2(16,5) C3(20,4) C4(14,10) C5(10,21) C6(12,7)          C1 = special, Step 6
D1(14,4) D2(10,3) D3(12,4) D4(2,2)
E1(35,3) E2(15,3) E3(30,3) E4(12,5) E5(10,3) E6(8,4)
G1(6,2) G2(4,3)
H1★(12,30) H2(14,14) H3(8,14)

## PROCEDURE — execute in order, no steps skipped

STEP 1 — RUMOUR gate. Delete every event with independent_source_count < 2.
  Deleted events do not exist for any later step, including floors and convergence.
  (Tier-3 sources can never be one of the two — they nominate, they do not corroborate.)

STEP 2 — Novelty. Default 1.0. If this signal_id previously fired and was followed by 14+
  quiet days with no consequence, multiply its novelty by 0.6 per such cycle (minimum
  0.3). Reset novelty to 1.0 for all ids the moment any A, B or E event with
  BASE × tier_multiplier ≥ 15 fires.

STEP 3 — AGE in whole days.
  Non-stateful id: AGE = evaluation_date − event_date.
  Stateful id (★): if state_end_date is blank or ≥ evaluation_date → AGE = 0.
                   else → AGE = evaluation_date − state_end_date.
  If AGE < 0 (future event) → contribution 0.

STEP 4 — DECAY = 0.5 ^ (AGE ÷ HALF_LIFE). Keep at least 4 decimals.

STEP 5 — CONTRIBUTION = BASE × TIER_MULT × NOVELTY × DECAY.
  If CONTRIBUTION ≤ 0.01, discard the event (it also cannot trigger convergence).

STEP 6 — C1 deadline. If an active deadline exists and evaluation_date ≤ deadline:
  days_left = deadline − evaluation_date.
  If days_left ≤ 3: add 20 to category C of BOTH scores; C counts for convergence in both.
  Else: add 8 to category C of STRAT only; C counts for STRAT convergence.

STEP 7 — Buckets and caps. For each score separately, keep only that score's ids
  (see Score membership), sum contributions per category letter, then cap D at 20 and
  G at 10. RAW = sum of the capped category totals.

STEP 8 — Convergence. N = number of distinct category letters, in THIS score's buckets,
  that have at least one surviving event with event_date within 72h of evaluation_date
  OR (stateful) state still active. Add C if Step 6 added it. CONV = min(1.6, 1 + 0.15 × (N−1)).
  SCORE = RAW × CONV.

STEP 9 — Deception (TACT only). If DECEPTION FLAG is TRUE and at least one A- or B-event
  has CONTRIBUTION ≥ 15 with event_date within 72h → TACT = TACT × 1.3.

STEP 10 — Floors (TACT only, applied after Step 9, never reduce):
  a) B1 and B2 both within 24h of evaluation → TACT = max(TACT, 70)
  b) E1 fired and E2 fired within 7 days of evaluation → TACT = max(TACT, 75)
  c) B6 at Tier 1–2 within 72h → TACT = max(TACT, 60)

STEP 11 — Final. SCORE = min(100, SCORE), round to 1 decimal.
  Tiers: 0–14.9 → Tier 0 | 15–34.9 → 1 | 35–54.9 → 2 | 55–74.9 → 3 | 75+ → 4.
  Displayed alert = the HIGHER of the two tiers. Report deltas vs previous run and 7-day mean.

STEP 12 — ALERT DECISION (message layer only — scores are NEVER modified by this step).
  REGIME: WARTIME if TACT ≥ 75 on every one of the last 7 consecutive daily evaluations;
  otherwise NORMAL. Once in WARTIME, exit only after TACT < 55 for 7 consecutive days —
  and the exit itself is a full alert (de-escalation is news).
  In NORMAL: full alert on any upward tier transition; short one-liner otherwise.
  In WARTIME: the daily message is one line — "Wartime baseline, day N. Tier 4,
  unchanged." A full alert fires ONLY if at least one of:
    (i)  DELTA: today's TACT or STRAT ≥ its own trailing 7-day mean + 10 points;
    (ii) NEW DIMENSION: a category letter fires today that produced no surviving event
         in the previous 14 days — e.g. first evacuation order (B1/B2), first blackout
         (H2), a second carrier (A2) — while the exchange cadence was already running;
    (iii) STRAT rises a tier (strategic deepening beneath a saturated TACT).
  Rationale: costs are asymmetric — a false alarm wastes an evening, a miss can be
  fatal — so moderate false-positive tolerance is correct in NORMAL regime. But a
  Tier-4 siren every day through a 60-day strike-exchange cadence trains the owner to
  ignore the one alert that matters (the exact fatigue that blinded observers before
  Jun 2025). Under a saturated score, information lives in deviation from baseline;
  rule (ii) is the false-negative guard — the score cannot exceed 100, but a category
  silent for 14 days going loud is precisely how in-war mainland escalation announces
  itself.

## WORKED EXAMPLE 1 — Jun 11 2025 evening, TACT (expected: 100.0)

Events (all ≥2 sources): B1, B2, B3 (Tier 1, Jun 11), B5, D2, G1 (Tier 2, Jun 11),
A6, A8 (Tier 2, May 20). Deadline Jun 12. Deception flag TRUE.
TACT ids only → D2, A6, A8 excluded (D2 is not D1/D3; A is STRAT-only).
  B1: 30 × 1.0 × 1.0 × 0.5^(0/3) = 30.00      B2: 25.00      B3: 15.00
  B5: 12 × 0.8 × 1.0 × 1.0000    =  9.60      G1: 6 × 0.8 =  4.80
Step 6: days_left = 1 ≤ 3 → +20 to C.        RAW = 30+25+15+9.6+4.8+20 = 104.40
Step 8: categories within 72h: B, G, C → N=3 → CONV 1.30 → 104.40 × 1.30 = 135.72
Step 9: deception TRUE, B1 contribution 30 ≥ 15 → × 1.3 = 176.44
Step 10: floor (a) also active (70 — no effect). Step 11: min(100, 176.4) = **100.0**

## WORKED EXAMPLE 2 — Feb 21 2026, STRAT (expected: 63.9)

Events: A3 (T2, Feb 21, state active), A1 (T2, Jan 25, state active), A7 (T2, Jan 29,
state active), H1 (T2, Jan 8, state ended Jan 20), H2 (T2, Jan 9), H3 (T2, Dec 28),
D4 (T2, Feb 19), D4 (T2, Jan 29), G2 (T1, Feb 18). No deadline. No deception.
  A3: 20 × 0.8 × 0.5^(0/10)      = 16.000   (state active → AGE 0)
  A1: 18 × 0.8 × 0.5^(0/14)      = 14.400   (state active)
  A7: 12 × 0.8 × 0.5^(0/14)      =  9.600   (state active)
  H1: 12 × 0.8 × 0.5^(32/30)     =  4.581   (AGE from state end Jan 20 → 32 days)
  H2: 14 × 0.8 × 0.5^(43/14)     =  1.332
  H3:  8 × 0.8 × 0.5^(55/14)     =  0.420
  D4 Feb 19: 2 × 0.8 × 0.5^(2/2) =  0.800
  D4 Jan 29: 2 × 0.8 × 0.5^(23/2)=  0.0003 → ≤ 0.01, discarded (Step 5)
  G2: 4 × 1.0 × 0.5^(3/3)        =  2.000
Buckets: A 40.000 | H 6.333 | D 0.800 (cap 20 ok) | G 2.000 (cap 10 ok)
RAW = 49.134.  Step 8: within 72h or active state: A (states), D (Feb 19), G (Feb 18)
→ N=3 → CONV 1.30.  SCORE = 49.134 × 1.30 = 63.87 → **63.9** (Tier 3).

## WORKED EXAMPLE 3 — Aug 1 2026, both scores (expected: TACT 75.0, STRAT 25.0)

Events: E1 (T1, Jul 30), E2 (T1, Jul 31), E3 (T1, Jul 13), E4 (T2, Jul 30),
B3 (T1, Jul 30), D3 (T2, Jul 5), A1 (T2, Apr 13, state active). No deadline, no deception.
TACT: E1 35×0.5^(2/3)=22.050 | E2 15×0.5^(1/3)=11.906 | E3 30×0.5^(19/3)=0.370
  | E4 12×0.8×0.5^(2/5)=7.276 | B3 15×0.5^(2/3)=9.450 | D3 12×0.8×0.5^(27/4)=0.090
  (A1 excluded — A is not a TACT category.)
  RAW = 51.14. Within 72h: E, B → N=2 → CONV 1.15 → 58.81.
  Floor (b): E1 + E2 within 7 days → max(58.81, 75) = **75.0** (Tier 4).
STRAT: A1 14.400 (state) | E4 7.276 | D3 0.090. RAW = 21.77.
  Within 72h/active: A, E → N=2 → CONV 1.15 → 25.03 → **25.0** (Tier 1).
Displayed alert = Tier 4.
Step 12: TACT has been ≥ 75 since Jul 8 (24 consecutive days) → WARTIME regime.
  (i) delta 0 < 10; (ii) today's categories E and B both fired within the last 14 days —
  no new dimension; (iii) STRAT tier unchanged. → **one-liner only: "Wartime baseline,
  day 24. Tier 4, unchanged."** If a B1 (first evacuation order in >14 days) had appeared
  today, rule (ii) would force a full alert regardless of the score already being pinned.

## SELF-CHECK before reporting any score

□ Every used event had ≥ 2 independent non-Tier-3 sources (Step 1).
□ No stateful signal was decayed while its state was still confirmed (Step 3).
□ D capped at 20, G capped at 10, per score (Step 7).
□ Convergence counted only categories present in THAT score (Step 8).
□ Floors checked even when the multiplied score seems high enough (Step 10).
□ Your arithmetic reproduces Worked Example 2 to ±0.1 before you trust yourself on live data.
