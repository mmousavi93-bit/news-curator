# War Signals: An Indications & Warnings Analysis of the 2025–2026 Iran Wars

**Purpose:** Empirical reconstruction of observable pre-war signals before the 12-day war
(13–24 Jun 2025) and the 40-day war (28 Feb – 8 Apr 2026), plus the July 2026 resumption,
compared against historical US war buildups (Iraq 2003, Afghanistan 2001, Vietnam 1964–65,
Venezuela 2025–26) and the pre-2008 financial crisis. Output feeds the News Curator's
deterministic risk engine (`config/risk_weights.yaml`) and signal-extraction prompts.

**Companion files:** `ESCALATION_SCORING.md` (scoring spec), `ECONOMIC_SHOCK_SCORING.md`
(market scorer), `AGENT_PROMPT.md` (LLM extraction prompt). Written 2026-08-01, while the
conflict is active (post-MOU-collapse strike exchanges ongoing).

---

## 1. Problem formulation

Given a continuous stream of noisy multilingual news (~thousands of items/day), estimate at
each pipeline run two probabilities for a Tehran resident:

- **P(strike wave on Iranian territory within 24–48h)** — tactical score, TACT
- **P(major escalation within 7 days)** — strategic score, STRAT

Constraints: only free public sources; signals must be discrete facts extractable by an LLM
and scored deterministically; single-source claims are RUMOUR and excluded; the adversary
environment includes **active deception** — both wars opened behind deliberate false-calm
campaigns, so naive sentiment reading of official statements is worse than useless.

The problem is asymmetric-cost binary alerting: a false alarm costs the owner a wasted
evening of preparation; a miss costs potentially everything. The system should therefore be
calibrated to tolerate ~1 false Level-3 alert per month rather than ever miss a real T-48h
window. But unlimited false positives destroy the system too — warning fatigue is precisely
why observers missed June 2025 (monthly "Israel preparing to strike" stories since Feb 2025
had trained everyone to discount them).

## 2. Case 1 — The 12-day war: a compressed, deception-masked onset

Signal environment April–June 2025: five rounds of US–Iran talks (Apr 12 – May 23), a
running 60-day Trump deadline, and chronic monthly strike rumors. The April 16 NYT story
("Trump waved off Israeli strike plan") created a "crisis averted" narrative 7 weeks before
the strike — the textbook false-calm anchor.

**Signals with real lead time (ambiguous at the time):**

- T-24d (May 20): CNN intelligence leak — Israel preparing strike, based on hard indicators
  (munitions movement, completed air exercise, intercepted comms). Retrospectively the best
  long-lead tell, but formatted identically to prior false alarms.
- T-13d (May 31): IAEA report — 60% HEU stock 408.6 kg, enough for ~9 weapons.
- T-~90d→T-1: the 60-day deadline mechanism. Trump's deadline **expired the day before the
  strike**. A tracked countdown clock alone would have flagged the June 11–13 window.

**The acute 48-hour cluster (all on June 11, T-2):** ordered departure of non-emergency
staff from Embassy Baghdad; authorized departures Bahrain/Kuwait; Hegseth authorizes
military-family departures CENTCOM-wide; Senate testimony of the CENTCOM commander
postponed <24h before schedule; UKMTO/JMIC and US MARAD escalation advisories; Trump "less
confident" about a deal; oil jumps the same day. Then T-1 (June 12): IAEA Board declares
Iran non-compliant (first since 2005); Iran announces a new enrichment site in response.

**Deception layer (T-2 to T-0):** Trump publicly pro-negotiation on June 12; Netanyahu
"weekend vacation + son's wedding" story; a fake diplomatic track leaked; a manufactured
Trump–Netanyahu rift story left undenied. Sixth round of talks was scheduled for June 15 —
the strike landed two days before it.

**Negative findings:** no pre-strike airline suspensions (aviation was reactive); the famous
30-tanker transatlantic surge was June 15–16, *after* H-hour (reinforcement, not warning);
carrier moves (Nimitz) were also post-strike; Israeli reserve call-ups came at H-hour.
Pre-June-13 GPS jamming was indistinguishable from the 2023–25 regional baseline.

**Lesson 1:** in a surprise-strike scenario, the differentiable signal is ~48h wide, and its
signature is **category convergence on a single day** — evacuation + bureaucratic anomaly +
rhetoric shift + market tremor — against a backdrop of official calm. The calm itself,
co-occurring with logistics moves, is a signal (deception indicator), not a de-escalator.

## 3. Case 2 — The 40-day war: a telegraphed buildup with the evacuation tell removed

Signal environment Dec 2025 – Feb 2026: rial collapse and the largest protests since 1979
(Dec 28), shoot-to-kill crackdown + national internet blackout (Jan 8–9), thousands dead.
Diplomatic predicate already laid in 2025: E3 snapback, UN sanctions reimposed Sep 27, 2025.

**Long-lead signals (30+ days):** Khamenei crackdown order + blackout (T-50); a zero-carrier
gap in CENTCOM Jan 5–25 followed by USS Abraham Lincoln CSG redirected into theater
(T-33); Trump "help is on its way" to protesters concurrent with the buildup (T-30); Gulf
states restricting US base access — bystanders hedging (T-30).

**Mid-lead (7–12 days):** Iranian Hormuz naval maneuvers timed to Geneva breakdown
(T-10/12); gold record $5,019 (T-10); new THAAD battery in Jordan + Patriot at Ovda, Israel
(T-7) — **defensive systems positioned ahead of an offensive strike is a classic tell**;
Netanyahu "prepared for any scenario, side by side with the US" (T-9).

**Short-lead (48h):** Feb 26 Geneva round ends, US maximalist demand (destroy Fordow/
Natanz/Isfahan, surrender all HEU), no deal; a "technical follow-up in Vienna" announced —
**never convened** (same never-held-meeting tell as June 2025's sixth round).

**The removed tell:** no ordered departures before Feb 28. Evacuations began the day of the
strike. The US deliberately sacrificed its civilian-protection lead time to preserve
surprise, having learned that OSINT watchers keyed on the June 2025 evacuation signal.
Deception again: a leaked F-22 deployment story masking real preparations; leader calls
kept off official channels.

**Lesson 2:** signals the adversary controls cheaply (statements, evacuation timing) get
suppressed or spoofed after they're publicly identified as tells. Signals that are
*expensive to hide* — carrier positions, THAAD emplacements, protest crackdowns, failed
talks, a missed meeting that was on the calendar — survive. Weight physical/logistical and
calendar-anomaly signals above declaratory ones, and never treat the absence of a
previously-diagnostic signal as safety.

## 4. Case 3 — The July 2026 resumption: a mechanical trigger-response cadence

Post-ceasefire structure: Apr 8 ceasefire → Apr 13 US naval blockade (~$500M/day cost to
Iran) → Jun 14 Pakistan-brokered MOU (Hormuz reopening, 60-day framework) → Iranian drift
from compliance to "route control + fees" over ~3 weeks → Jul 6–7 Iranian missile strikes
on three commercial vessels on the US-designated route → Jul 8 US strikes 80+ targets, MOU
declared dead → repeating cycle (Jul 11: 140 targets after a container-ship attack; Jul 13:
tanker disablings + Iranian drone/cruise strikes on Kuwait; Jul 29–31: continuing, Saudi
participation against PMF in Iraq).

**The empirical resumption law (July 2026):** every US strike wave followed an Iranian
vessel/tanker attack by **24–72 hours**. The highest-value tactical feed for a Tehran
resident right now is therefore UKMTO/JMIC maritime incident reporting, not political news.
Secondary tells: IRGC language shifting from "control" to "closure/mining"; Trump
infrastructure-strike ultimatums (public countdown clocks, historically executed within
days); further CSG/THAAD repositioning (scale indicator); proxy-front widening (PMF,
Hezbollah) preceding mainland strikes; absent Congressional brake (war-powers votes failing).

## 5. Historical invariants

**Iraq 2003 (deliberate war):** predicate (Sept 2002 UN speech, AUMF Oct, UNSCR 1441 Nov) →
force buildup to ~150k in Kuwait over 4 months, fully OSINT-visible → diplomatic exhaustion
(collapse of second resolution) → 48h ultimatum Mar 17 → war Mar 20. Everything telegraphed;
the ultimatum gave exact timing.

**Afghanistan 2001 (retaliatory):** 9/11 → ultimatum to the Taliban Sept 20 → basing
agreements (Pakistan, Uzbekistan) and carrier movements → strikes Oct 7. 26 days from cause
to war; the ultimatum-rejection was the gate.

**Vietnam 1964–65 (manufactured incremental):** ambiguous Tonkin incident + a pre-drafted
congressional resolution within 72h = premeditation tell. Escalation then gradual (Rolling
Thunder Feb 1965). Lesson: when the legal instrument appears faster than the facts could
justify, the decision predates the incident.

**Venezuela 2025–26 (pressure-to-decapitation):** legal predicate (FTO designation, Aug
2025) → buildup (Ford CSG, F-35s, 15k personnel, Aug–Oct) → limited kinetic phase (boat
strikes from Sep 2) → blockade (Dec) → decapitation operation and Maduro's capture (Jan 3,
2026). Each phase preceded by 2–4 weeks of visible posture/rhetoric/legal moves.

**Invariant sequence across all cases, including both Iran wars:**

1. **Predicate** (legal/narrative groundwork): weeks–months out. FTO designations, censure
   resolutions, snapback, atrocity narratives, "diplomacy exhausted" framing.
2. **Posture** (expensive physical moves): 1–5 weeks out. Carriers, bombers, air-defense
   emplacements, munitions movement, basing/overflight deals, bystander hedging.
3. **Calendar exhaustion**: 1–7 days out. Deadline expiry, final talks round failing,
   scheduled meetings that never convene, ultimatums with dates.
4. **Apparatus protection**: 0–3 days out — evacuations, advisories, cancelled testimony —
   *unless deliberately withheld for surprise* (Feb 2026).
5. **Deception spike**: T-2 to T-0 — official calm, vacation stories, fake diplomacy —
   co-occurring with stages 2–4. Contradiction between words and logistics is itself the
   final confirmation.

**Pre-2008 note (for the economic scorer):** crises telegraph in credit before equities and
in equities before headlines — yield-curve inversion (2006), ABX/subprime deterioration
(early 2007), Bear Stearns funds (Jun 2007), TED-spread spike (Aug 2007) all preceded the
equity collapse by 12+ months. Mapping: watch funding/credit/insurance-of-risk metrics
(spreads, war-risk premia, freight rates) as the market analog of "posture" — expensive,
hard-to-fake commitments of capital — and discount cheap declaratory market commentary
exactly as we discount rhetoric. Detail in `ECONOMIC_SHOCK_SCORING.md`.

## 6. Signal taxonomy and empirical weights basis

Categories ordered by evidential value (specificity × cost-to-fake), with observed leads:

| Cat | Signal class | Observed lead | Fakeable? | Case evidence |
|---|---|---|---|---|
| A | Force posture/logistics (CSG, THAAD, bombers, tankers, munitions) | 7–33d | expensive | Lincoln T-33, THAAD T-7 (2026); tankers post-hoc (2025) |
| B | Apparatus protection (evacuations, advisories, cancelled events) | 0–3d | suppressible | Jun 11 2025 cluster; withheld Feb 2026 |
| C | Diplomatic calendar (deadlines, failed rounds, ghost meetings, censure) | 1–90d | hard to fake | 60-day deadline; ghost 6th round; ghost Vienna round |
| D | Leadership rhetoric (ultimatums with dates > mood shifts > chronic threats) | 0–30d | cheap | Trump "less confident" T-2; "armada" T-30; Katz noise |
| E | Kinetic precursors (vessel strikes, proxy widening, intercepts) | 24–72h | no | July 2026 cadence law |
| F | Deception/anomaly (calm-plus-logistics contradiction, normalcy theater) | 0–2d | is the fake | both wars |
| G | Market confirmation (oil, gold, defense) | 0–10d | no, but lagging | oil T-2 (2025); gold record T-10 (2026) |
| H | Internal shock (protests, crackdown, blackout, currency collapse) | 30–60d | no | Dec 25–Jan 26 |

Three structural rules derived from the cases:

- **Convergence beats magnitude.** One category screaming is a rumor cycle; three categories
  moving within 72h is a war. June 11, 2025 = B+C+D+G same day. Feb 21–26, 2026 = A+C+D+G
  within five days.
- **Anomaly beats content.** The missed Senate hearing, the never-held sixth round, the
  never-held Vienna round — events *failing to happen on schedule* outperformed everything
  published. The agent must track scheduled events and score their non-occurrence.
- **Repetition discounts, novelty restores.** A signal type that fires monthly without
  consequence decays toward zero weight (strike-rumor stories); it regains full weight only
  when corroborated by a signal from a different, expensive category.

## 7. Verification note

War timelines cross-checked against Britannica/Wikipedia/CFR tracker; individual signals
carry inline sourcing in the research annexes (subagent reports, condensed here). Residual
uncertainty flagged: exact rial-collapse timing, some casualty figures disputed (protest
deaths 4k–30k range), Polymarket "prescient trader" evidence is post-hoc forensic. None of
these affect the scoring design.
