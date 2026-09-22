# SESSION_21 — Output-quality optimization + assumption review

Date: 2026-09-22. Trigger: owner directive — "think and improve the non-ML output
qualities, rethink assumptions, improve the pipeline if needed, get help from a
reviewer agent, optimize the whole project."

## What was done

1. Grounded analysis of the output pipeline (render/rank/compose/samerun_dedup/
   repeat_decision/report_csv_pairs) against the real Sep 8 run artifact
   (`outputs/summaries_20260908T225636Z.csv`, `output-log.txt`).
2. Dispatched a fresh-context adversarial reviewer (v4-pro) to verify each
   finding against the code and hunt for missed items.
3. Implemented the reviewer's top recommendation (see "Implemented" below).
4. Full suite green: 913 tests (was 910).

## Findings (as corrected by the reviewer — read these, not the v1 drafts)

- **F1 — cluster fragmentation (accurate).** `samerun_dedup.py` docstring
  documents the tanker cluster fragmenting n=48/12/9/3 and Azraq n=59/18/23/4;
  the same-run path collapses only at `event_repeat_threshold` 0.80, so a
  paraphrase (strike vs its retaliation, cosine 0.60–0.65) survives as a
  separate item. The Sep 8 run's same-run dedup fired exactly once.
- **F2 — `significance: none` gate (partially accurate, do NOT treat as a
  defect).** It is an owner-signed, intentional drop lever (Session 17) with a
  sound fallback (missing→`economy`=keep). The "none is the safe answer" claim
  is an unmeasured hypothesis, and any drop-rate measurement must separate the
  significance drop from the independent `min_score` floor or it confounds them.
- **F3 — headline/summary fallback duplication (partially accurate, LOW).** Real
  code path (`build_event` chains `summary or headline`), zero run evidence of
  it firing; cosmetic, not a constraint-11 issue. Do not spend a session.
- **F4 — cap wall (misattributed).** The Sep 8 loss was provider
  *circuit-breakers* (`bai`/`gemini` opened after 2 consecutive failures,
  `backoff.circuit_breaker_failures: 2`), not the 70-call ceiling: 23 answered,
  6 cap-lost, 7 unavailable. If retry budget is ever revisited, start from the
  breaker log, not the cap count.

## Reviewer's missed items (higher leverage than the v1 findings)

- **M1 (HIGH)** — 16 of 19 delivered items were `sent_followup` compact lines;
  the mid-band follow-up flood may itself be the dominant output-shape problem.
  Measure full-entry vs compact-line ratio first.
- **M2 (HIGH)** — same-run dedup has NO two-band/development logic, only the
  0.80 collapse; the cross-run gate already has the tested two-band + high-water
  ratchet to reuse. Higher leverage than gathering pair data.
- **M3 (HIGH)** — score double-counts correlated axes: `significance_weight +
  category_weight + tier_bonus` all reward the same "military escalation"
  judgment, which is what makes "one event told 6 ways" rank at the top.
- **M4 (MEDIUM)** — circuit-breakers arguably too aggressive for free tiers; a
  longer failure window or per-provider reopen probe is the fix, not faster
  failing.
- **M5 (MEDIUM)** — the entire evidence base is config-stale: the Sep 8 run used
  a 40-cluster cap + keyword-relevance ranker, predating both `max_clusters_per_run:
  150` and the Session 17 significance ranker. Any threshold decision must be
  re-derived on a post-Session-17 artifact.

## Implemented (this session)

- **`novelty` field on `PairRecord` + `pairs_<ts>.csv`** — `samerun_dedup.py`
  now tags every logged pair `development` (either side brings a new
  number/entity the other lacks, reusing `repeat_decision._content_novelty`) or
  `fragment` (same story, should have merged). This is the measurement the
  fragmentation decision D1 needs, produced on every run on the CURRENT config,
  with zero LLM calls and zero threshold/drop-logic changes. Analysis-time
  filter: count `decision == kept_below_threshold AND novelty == fragment` to
  get true fragmentation; `development` pairs are legitimately distinct.
  - Files: `src/agent/pipeline/samerun_dedup.py`,
    `src/agent/report_csv_pairs.py`,
    `tests/unit/test_pipeline_samerun_pairs.py` (+3 tests).

## Owner-decision items (queued — NOT unilateral)

- **D1** — `event_repeat_threshold` tuning / same-run two-band port (M2), to be
  settled from two runs of `pairs_<ts>.csv` (novelty-filtered).
- **D2** — significance-gate retention/revision, from a measurement that
  separates the significance drop from the `min_score` drop.
- **D3** — category+significance redundancy in the score formula (M3).
- **M1** — follow-up flood vs full-entry ratio.

## Next

Run the pipeline twice (CI has the keys; local does not), pull `pairs_<ts>.csv`,
and settle D1/D2 from the novelty-filtered data. No threshold changes before then
(one-variable rule).
