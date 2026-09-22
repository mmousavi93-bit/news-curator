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
  **RESOLVED round 2:** terms uncorrelated (see Data result round 2) — no change.
- **M1** — follow-up flood vs full-entry ratio. **RESOLVED round 2:** all
  follow-ups are MID-band full entries, backlog artifact — no change.

## Next

Run the pipeline twice (CI has the keys; local does not), pull `pairs_<ts>.csv`,
and settle D1/D2 from the novelty-filtered data. No threshold changes before then
(one-variable rule).

## Data result (runs 35712130649 + 35719159597, post-Session-17 150-cap)

Settled D1/M2 empirically — and it OVERTURNS the fragmentation premise.

- Run 35712130649 (manual dispatch, ~12.5h backlog, 58 min runtime):
  - `pairs_*.csv`: 51 pairs (sim ≥0.40), **0 fragments, 51 development, 0
    dropped, max cosine 0.63**. The 0.40–0.63 survivors are DISTINCT stories
    sharing domain vocabulary (drug seizure vs cigarette smuggling; karate team
    silver vs athlete silver; US/France Hormuz diplomacy vs Trump-Iran meeting)
    — NOT "one event told N ways".
  - `chosen_*.csv` fate: irrelevant 61, cap_dropped 37 (all `on_mission=0`,
    i.e. off-mission noise dropped first — good design), repeat_dropped 25,
    relevance_dropped 16, clickbait 14, oversized 8, sent 8, sent_followup 16,
    rank_dropped 1, lang_dropped 1.
  - **Cross-run repeat gate is the fragmentation catch**: 25 `repeat_dropped`
    with `reason` `band=mid/high sim=… blocked=below_floor` (or
    `kept=above_floor`) — including the "Gaza evacuation threat" duplicate (one
    kept at score 23.7, the 13.1 paraphrase blocked). The two-band +
    score-floor logic M2 claimed was "missing from same-run" ALREADY exists
    cross-run and is where the paraphrase band is actually handled.
  - Delivered 24 items (8 sent + 16 followup) are all distinct. No
    fragmentation in the output.
- **Conclusion**: D1 = leave `event_repeat_threshold` at 0.80. Do NOT add a
  same-run two-band. The `novelty` field is a WEAK signal on live-war feeds
  (every update adds a death toll/location/actor → ~all pairs classify
  `development`), so `fragment==0` is not a reliable "no fragmentation" proof
  — but the cross-run `reason` field already settles it anyway.

## Data result (round 2 — M1/M3 settled, also rejected)

The reviewer's other two HIGH findings were tested on the same two runs:

- **M1 (follow-up flood) — REJECTED.** All 16 `sent_followup` items in run 1
  carry `reason band=mid sim=0.59–0.80 kept=above_floor` — MID band renders as
  a FULL normal entry with only a "پیگیری ·" continuity marker, NOT a compact
  line. Zero HIGH-band (`follow_up_high`) compact lines fired in either run
  (run 2: 2 `sent_followup`, both `band=mid`). The "flood" is a backlog
  artifact (a 12.5h gap means most big stories had SOME prior mention), and
  even then it is full entries, not truncated lines.
- **M3 (score triple-count) — REJECTED.** Reconstructed the three terms for
  the 24 delivered items from `chosen_*.csv` + `digest_rank` weights:
  `corr(significance_weight, category_weight) = +0.003`,
  `corr(category_weight, tier_bonus) = −0.041`,
  `corr(significance_weight, tier_bonus) = −0.585`. The terms are independent
  (escalation spans military AND politics; economy is its own category) and
  significance is *negatively* correlated with source tier (breaking escalation
  arrives on lower-tier regional sources first). No double-counting.
- **HIGH-band follow-up — reachability settled by probe.** The HIGH band *does*
  fire — run 2 had three `band=high sim=0.84–0.85` matches, all
  `blocked=no_development` (verbatim repeat, correctly dropped). The compact
  summary-carrying follow-up line (`follow_up_high`, developed) is unit-tested
  but had no production sample. `tools/probe_embed_reachability.py` (workflow
  `probe-embed`, run 35749144182) settled it with the real MiniLM embedder:
  appending a new fact (number or entity) to a real summary drops cosine only
  1.000 → 0.975/0.976, so a *developed* story stays well above the 0.80 band.
  `follow_up_high` is live — it's rare because "same story + new fact" events
  are rare, not because the band is unreachable. Side observation: a reworded
  *same-fact* pair landed at 0.797, i.e. the 0.80 boundary sits exactly at
  paraphrase similarity — the band is doing knife-edge work.

## Implemented (round 2)

- `if: always()` on the `Upload run reports` CI step (`.github/workflows/
  pipeline.yml`, commit d0b7897): a failed/timed-out run still wrote its
  `pairs_*.csv` dedup log but the upload step silently dropped it. Matches the
  state-backup convention; protects the diagnostic exactly when it's most
  needed (run 1 nearly timed out at 58 min).
- `tools/analyze_pairs.py` (commit 3a7ed13): parses `pairs_*.csv` into
  per-bucket fragment/development counts + a fragment list.

## Real remaining levers (smaller than assumed)

- groq fail rate ~30% in run 1 (13/44) is a BACKLOG rate-limit artifact, not a
  steady-state defect: run 2 (clean, 3h) was groq 5/5 clean with llm_failed=0.
  No action; re-observe on the next backlog run.
- cap/timeout mismatch: RESOLVED — `timeout-minutes` raised 90→150 (commit
  6a00bcd), a pure safety ceiling.
