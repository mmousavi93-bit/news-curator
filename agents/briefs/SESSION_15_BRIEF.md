# Session 15 — Relevance participates in the digest sort (not just the gate)

## Problem (measured from run 35240873642, artifact 20260917T162536Z)

The digest shipped 5 stories and only ONE was iran_direct, ranked LAST:

| rank | story | relevance | category | score |
|---|---|---|---|---|
| 1 | Yemen/Houthi Taiz | strategic | military | 12.98 |
| 2 | Israel dismisses Gaza intel cmdr | strategic | military | 12.91 |
| 3 | Boat stops tanker E of Aden | strategic | security | 11.27 |
| 4 | Aramco restarts pipeline | economy | economy | 11.24 |
| 5 | **Amnesty accuses Iran (1401)** | **iran_direct** | security | 11.00 |

Meanwhile 3 iran_direct stories were dropped before the sort even ran:
- `943d5ffd` Iran summons German ambassador (politics, 10.79, 9 sources) → repeat-dropped (mid, below_floor 11.0)
- `53b793dc` Iran fighter squadron south (military, 9.31, 5 sources) → repeat-dropped (mid, below_floor)
- `63204557` 4 Fatemiyoun killed Zahedan (security, 6.94) → rank-dropped (below min_score 8)

## Root cause

`rank.py:score_event` computes importance = category + corroboration + tier +
recency + size — relevance is NOT a term. `relevance.yaml:7` documents that the
scorer "adds the highest matching tier's weight to the digest score", but the
code only uses it as a binary gate (`min_relevance`). The "relevance is a
FILTER, importance is the SORT" rule (settings.yaml digest_rank comment,
2026-08-30) was tested as a hard rule, but the owner has since lifted it:
"not a hard rule — apply and measure".

## Change

Add the relevance-tier weight (iran_direct=8 / strategic=4 / economy=3) to
`score_event`, threaded through `rank_events` (sort + min_score) and
`repeat_decision._decide` (repeat gate). Recalibrate the two importance floors
by exactly `+min_relevance` (=3) so NON-Iran stories keep their exact same
floor and only Iran/strategic stories get a relative lift:

- `min_score`: 8 → 11
- `repeat_bypass_score`: 11.0 → 14.0

Net relative lift vs the gate baseline: economy +0, strategic +1, iran_direct +5.

## Scope

- `src/agent/pipeline/rank.py` — score_event / event_order_key / rank_events
- `src/agent/pipeline/repeat_decision.py` — _decide threads relevance into score
- `src/agent/pipeline/relevance.py` — docstring only (filter → filter + sort term)
- `config/settings.yaml` — min_score + repeat_bypass_score + comments
- `tests/unit/test_pipeline_rank.py` — update the one locked test, add a lift test

## Not in scope (measured, deferred)

- `lead_only` gate (validate.py): the Minab-school story (`3561cba4`) is a
  lead-tier channel and ships in the lead message, not the digest — untouched.
- Source mix: 26 sources, healthy Iran-domestic coverage — no change.
- LLM availability (10/52 clusters "unavailable" = the groq 429/TPM issue,
  Session 14) — separate problem, not this change.
