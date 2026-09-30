# Extraction Precision/Recall — Phase 11 Accuracy Gate (probe run 36720698151)

**Date:** 2026-09-30 · **Sample:** 12 clusters (frozen Sep-28 read, `fixtures/extraction_eval/items_20260928.csv`)
**Method:** per-provider isolated `signals.extract()` (probe `tools/probe_extraction.py`), hand-labeled against `config/prompts/signal_extraction.txt`.

## Verdict: GATE NOT MET — no provider is within range of precision ≥ 0.8

| provider | avail % | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|
| gemini-3.6-flash | **8.3** | 0 | 0 | 2 | n/a (down) | 0.00 |
| groq qwen3.8-27b | 91.7 | 0 | 6 | 2 | **0.000** | 0.00 |
| groq2 qwen3.8-27b | 91.7 | 0 | 7 | 2 | **0.000** | 0.00 |
| mistral 8b | 91.7 | 2 | 20 | 0 | **0.091** | 1.00 |

Best available precision is **9.1%** (mistral), an order of magnitude below the gate. The score must remain behind `NoopStage("score")`.

## Ground-truth labels (12 clusters)

- **E5** (interception of a major attack) — the two RAF Fairford clusters only: UK police disclosed a foiled terror plot against a US airbase used to strike Iran. E3 does *not* fire (no attack occurred; UK ≠ Gulf region).
- **none** — the other 10: diplomacy (Iran-nuke talks, Netanyahu–MBZ), market move (oil spike → `economic_events`, not captured), diplomatic spat (Israel–Dutch), garbage cluster, Ben Gvir prisoner threat, car-scrappage, unconfirmed Hormuz rumour, Rouhani commentary, and out-of-scope Ethiopia–Tigray (E4 is wrong; it is not an Iran proxy).

## Findings

1. **Failure mode is over-extraction onto "none" clusters.** Rule 1 ("fits nothing IS nothing") is violated by every non-gemini provider. groq/groq2 stamp `D4` onto diplomacy; mistral invents signals across 11 of 12 clusters (2.5 spurious signals/cluster avg).
2. **groq/groq2 (qwen3.8-27b) map Fairford to `E3`, not `E5`** — they fail the interception-vs-attack distinction, the single most load-bearing signal on the day.
3. **mistral 8b is the only provider with recall 1.0** (caught both E5) but floods 20 false positives — the exact "array-flake / over-extraction" failure predicted for 8b. 9% precision confirms 8b is never acceptable for high-stakes extraction.
4. **gemini is saturated (8.3% availability)** — demoted for days. The plan's "gemini-first when self-healed" premise cannot be exercised until its rate limit clears; no accuracy measurement is possible while it is down.

## Constraints on the measurement

- Only **2 positive clusters** → recall denominator is tiny; the recall figures are weak evidence, the precision figures are strong.
- `economic_events` / countdowns / scheduled / soothing fields are **not captured** by this probe; the oil-spike economic signal (MSTRESS) is invisible here.

## Recommendation

1. Keep the deterministic score gated (`NoopStage`). Extraction accuracy is not close to shippable.
2. Wait for **gemini to self-heal** (rate-limit reset), then re-run the probe to measure *its* precision/recall before any other decision.
3. If gemini clears 0.8, wire it as the dedicated extraction provider. If it does not, the catalog prompt needs a negative-example/calibration overhaul (models are ignoring Rule 1) and/or a different provider — not a billing change.
4. Re-probe with a **larger positive sample** before a final gate verdict; the precision signal here is already decisive.
