# Session 12 — Quality round: false-drop fixes, ordered by impact

Goal: noticeably improve digest quality, highest-impact item first, per owner.
Evidence base: run `34872885506` (commit `bb569b3`), artifacts parsed from
`chosen_20260914T171005Z.csv` / `run_...csv`.

## Diagnosis (from the real run, not vibes)

42 clusters -> 40 LLM'd -> 6 delivered. Fates of the 40: 16 repeat_dropped,
12 irrelevant, 4 sent + 2 sent_followup, 2 rank_dropped, 2 clickbait,
1 lang_dropped, 1 relevance_dropped. The 6 delivered were all correctly
ranked (war/oil surfaced, rumour demoted) and relevant (zero trivia). The
three quality defects live in the DROP side, not the delivery side:

1. **lang_dropped killed the run's only corroborated, high-confidence item**
   (Portugal FM / Israel settlements: score 12.04, independent_count=2,
   claim=likely, 2 sources). Root cause CONFIRMED by byte inspection: a
   single Arabic yeh U+064A in the transliteration "بتسيلم" (B'Tselem) trips
   `is_persian_output`, which drops on ANY Arabic-only codepoint. Persian
   LLM output routinely carries one stray ي/ك in transliterated foreign
   names — so the gate has a silent bias AGAINST exactly the international,
   multi-source stories the digest needs. HIGHEST impact.

2. **repeat gate "no_development"** dropped the top-scored item (Israeli
   strikes continuing: 13.50, 6 members) — this is BY DESIGN (settings.yaml
   lines 564-569: high-band same-story ships only with development). NOT a
   bug; documented as a known limitation (development = indep_count↑ or
   claim↑ only; content novelty needs an LLM call we can't afford on free
   tier). No code change this round.

3. **cap `max_clusters_per_run: 40`** dropped a directly-relevant Reuters
   Hormuz-shipping-collapse item (cap_dropped) even on a quiet 42-cluster
   day. Busy days drop far more (~62% measured previously). Config knob.

## Changes (this round)

### 1. langgate.py — split hard/soft markers (code)
`is_persian_output` currently: any of `ة ى ي ك إ` or Hebrew -> drop.
New: STRONG = `ة ى إ` + Hebrew (any occurrence -> drop, unambiguous Arabic);
SOFT = `ي ك` (only drop at count >= 2). Monotonic — recovers single-ي
transliterations, never adds a drop. Genuine Arabic drift still caught by
`ة/ى/إ` (every Arabic sentence carries at least one) or by two+ `ي/ك`.

### 2. test_pipeline_langgate.py — regression test (test)
Add: single soft marker ("بتسيلم" / a lone ي) now PASSES; two soft markers
still FAIL (existing `AR_YA+AR_KAF` case is already 2, still drops).

### 3. settings.yaml — cap 40 -> 55 (config)
Recover cap_dropped overflow; +LLM calls only on high-volume days (quiet
days add ~2). Tradeoff documented: more groq load on busy days — the
health-aware wiring (eac926c) degrades gracefully; owner can dial back.

## Verify
- `pytest -q` green (existing langgate tests must still pass unchanged).
- Dispatch a real run; confirm the corroborated "likely" item no longer
  lands in `lang_dropped` fate.
