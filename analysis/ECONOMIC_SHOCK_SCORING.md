# Economic-Shock Relevance Scoring — Specification v1

Purpose: decide whether a news item/cluster is **relevant to a radical move** in key
markets (US equities, oil, gold, FX/credit), and maintain a macro-stress score. Companion
to `ESCALATION_SCORING.md`; same deterministic rules (LLM extracts, Python scores,
single-source = RUMOUR = excluded).

## 1. Design principle (2008 + 2025/26 lessons)

Crises telegraph in **committed capital**, not commentary. Ordering observed empirically:

- 2008: yield-curve inversion (2006) → subprime/ABX deterioration (early 2007) → Bear
  Stearns funds (Jun 2007) → TED spread spike (Aug 2007) → equity peak (Oct 2007) → crash
  (Sep–Oct 2008). Credit led equities by 12+ months; headlines lagged everything.
- 2026 wars: war-risk insurance, freight rates and gold moved days before equities cared;
  oil was coincident-to-2-days-leading; defense stocks were purely reactive.

So: **funding/credit/insurance metrics = posture signals** (expensive, hard to fake);
**analyst commentary and price targets = rhetoric** (cheap, weight ≈ 0).

## 2. Tracked metrics (all free)

| Metric | Source (free) | Radical-move trigger |
|---|---|---|
| Brent/WTI | stooq.com CSV / FRED | ±5% day; ±10% week |
| VIX | FRED (VIXCLS) | cross above 25; above 35 = crisis regime |
| Gold | stooq | +3% day or new all-time high |
| HY credit spread | FRED (BAMLH0A0HYM2) | +50bp/week; >600bp level |
| 10y–2y curve | FRED (T10Y2Y) | inversion entry/exit |
| USD/rial context | open bazaar-rate trackers | >15%/week step |
| S&P 500 | stooq | −3% day; −7% week |
| Tanker/war-risk | press-reported VLCC rates & Hormuz premia (Tier-2 sources) | premium doubling |

Metric pulls are code, not news items: one scheduled fetch per run into SQLite;
signals fire on threshold crossings (deterministic, replayable).

## 3. Event→channel relevance matrix

LLM tags each cluster with event types; Python maps to affected channels. A cluster is
**kept for the economic feed** iff matrix weight ≥ 3, else dropped.

| Event type (extracted) | Oil | US eq | Gold | Credit | Weight basis |
|---|---|---|---|---|---|
| Hormuz closure/attack/mining | 5 | 4 | 4 | 3 | 20%+ of global oil transits |
| Strike on Iranian territory / Iranian strike on Gulf states | 5 | 3 | 4 | 2 | proven Jun-25/Feb-26 moves |
| US–China military incident (Taiwan strait, SCS) | 3 | 5 | 5 | 4 | WW3-channel |
| Chip/export-control escalation, TSMC disruption | 1 | 5 | 3 | 3 | earnings channel |
| Major bank distress / funding stress / large CDS blowout | 1 | 5 | 4 | 5 | 2008 channel |
| Sovereign default / EM currency crisis | 1 | 3 | 3 | 4 | contagion |
| OPEC+/supply decisions, SPR releases | 4 | 2 | 1 | 1 | supply-side |
| Sanctions on oil buyers/shippers (secondary) | 4 | 2 | 2 | 2 | flows |
| Central-bank emergency action (intermeeting) | 2 | 5 | 4 | 5 | regime marker |
| War-risk insurance / freight-rate doubling | 4 | 2 | 2 | 2 | committed-capital tell |
| Political commentary, price-target notes, "analysts warn" | 0 | 0 | 0 | 0 | noise by definition |

## 4. Macro-stress score (MSTRESS, 0–100)

```
MSTRESS = min(100, Σ metric_triggers_active × weights + Σ event_scores(7d, decayed))
```

Metric trigger weights: VIX>35 =25, VIX>25 =12, HY +50bp/wk =18, curve event =8,
oil ±10%/wk =15, gold ATH =8, S&P −7%/wk =15, war-risk doubling =12.

Regime tags (drive digest framing, deterministic):
- **GEO-WAR** if MSTRESS ≥40 and oil+gold triggers dominate → "middle-east/WW3 channel"
- **CREDIT** if HY + VIX + curve dominate → "2008-pattern channel"; explicitly flag
  "credit leading equities — historically 6–12 months of runway, monitor weekly not daily"
- **MIXED/CALM** otherwise.

Trend deltas over 7/30 days reported alongside level, same as escalation scores.

## 5. Interaction with escalation scorer

- G-signals in `ESCALATION_SCORING.md` are fed from these metric triggers (oil, gold) —
  one fetch, two consumers.
- MSTRESS never raises TACT (markets confirm, they don't predict strikes — proven both
  wars); TACT ≥75 adds +10 to MSTRESS (wars do predict market stress).
- Digest layout: escalation first, markets second, one line each when unchanged.
