# SESSION_10F — Risk extraction labeling worksheet (stage 3b)

Human gate for stages 4–5. Two halves, different owners. **Collection +
reconstruction is agent work; semantic labeling is yours.**

## Two separate gates (do NOT conflate)

**Scorer fidelity** — the 5 synthetic backtest scenarios (2025-06-11, 2025-04-20,
2026-02-21, 2026-02-27, 2026-07-07). Already reproduced exactly in stage 2. Nothing
more to do here.

**Extractor accuracy** — 5 REAL pipeline windows from Aug–Oct 2026, selected by
evidence + collection completeness (below), NOT pre-existing. These are candidates
to be searched for in the run history; they are not yet frozen.

## Selection criteria for the 5 real windows (astra, 10G)

| Window | Required evidence |
|---|---|
| TACT-heavy pre-strike | Actual precautionary actions before a grounded strike boundary; B1/B2 + qualifying pre-strike B6, with advisories/contemplated-departures/reactive-closures as negatives |
| STRAT accumulation | Multiple posture developments over time; state continuity + ends; reposts must not become new deployments |
| Strike onset | Grounded occurrence timing/attribution/impact; attacks vs threats vs intercepted vs recap |
| Second TACT spike | A distinct later escalation, not repeat reporting; E2 needs two E1-type incidents ≤7 days apart, both identified |
| Quiet week | 7 consecutive observed days, mostly negatives, incl. plausible false positives (chronic rhetoric, recaps, unchanged advisories); ≥90% correct-none, no extractor false tier-3/4 rise |

Across all five: signal/category/language diversity + **nonzero, independently
verified B1/B2/B6/E1/E2 positives**. Zero misses on zero examples ≠ coverage.

## Half A — dataset (agent: collect + reconstruct; you: confirm completeness)

Source reality (verified 10G): local `outputs/` holds only 2 Sept-8 run-report
sets + 1 Sept-28 fixture. Full history is on GitHub artifacts via
`tools/download_run_reports.py`. `read_*.csv` has item text; `chosen_*.csv` has
fates/source-lists but NOT member-item mappings, credibility snapshots, or raw
extraction responses. **Raw LLM responses are not persisted anywhere**, so the
accuracy run needs **fresh re-extraction** of reconstructed clusters (captured
at eval time), not replay of historical captures.

## Half B — labels (you write, blind to extractor output)

`labels.json`, written BEFORE seeing extractor output, then independently
reviewed. Schema in `tests/fixtures/risk_extraction/README.md`. Per cluster:
`candidates` (occurrence keys, or empty = negative); `accepted` = six fields
(signal_id, event_date, source_tier, independent_source_count, novelty,
state_end_date) at that cutoff; optional `side_channels` (deadline, deception);
`coverage` names every missing source/history/class.

Rules: blind to model output · label every cluster incl. negatives · missing
capture on a gold positive = FN · missing history = "gate incomplete" · freeze
labels+versions before the acceptance run.

## Acceptance thresholds (design targets)
precision ≥95% · recall ≥90% · F1 ≥92% · zero fabricated evidence · 100%
deterministic derivation · zero misses on gold B1/B2/B6/E1/E2 · quiet-week
≥90% correct-none.

## Next steps
1. Agent downloads full run history (`tools/download_run_reports.py`), reconstructs
   5 candidate windows, proposes them for your approval.
2. You label (yourself, or agent walks you window-by-window).
3. Fresh re-extraction → measure → keep/cut on the thresholds.
