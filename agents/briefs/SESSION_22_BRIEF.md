# SESSION 22 BRIEF — Pipeline Improvement Plan (2026-09-23)

Owner-approved direction: read the past 3 days of run artifacts, design an
improvement plan (15 candidate optimizations → top 5), run 5 agents to
implement, orchestrate + review, finalize.

## Evidence base (9 runs, 2026-09-22 → 09-23)

| run | items | clusters | events | sent | repeat_drop | cap_drop | oversized | irrelevant |
|-----|-------|----------|--------|------|-------------|----------|-----------|------------|
| 35869579849 | 151 | 70 | 14 | 10 | 15 | ? | 2 | 29 |
| 35866102212 | 335 | 150 | 44 | 23 | 11 | 6 | 11 | 70 |
| 35847524803 | 439 | 150 | 51 | 28 | 21 | 32 | 4 | 59 |
| 35796214976 | 358 | 130 | 19 | 11 | 14 | ? | 24 | 62 |
| 35765753967 | 487 | 150 | 36 | 20 | 24 | 35 | 22 | 60 |
| 35734560868 | 317 | 142 | 43 | 23 | 25 | ? | 0 | 58 |
| 35714818231 | 123 | 54 | 16 | 9 | 5 | ? | 6 | 22 |
| 35712130649 | 438 | 150 | 42 | 24 | 25 | 37 | 8 | 61 |

Provider cascade (calls/fails) per run — gemini is **0/0 across all 9 runs**;
groq fails 30–80%; mistral never fails; groq2 mostly clean.

Key measured signals:
- `irrelevant` = 58–70 clusters/run ≈ **40% of the 150-cluster LLM budget** is
  spent on content the model then drops. Meanwhile `cap_dropped` = 32–37
  relevant clusters are silently dropped for lack of budget.
- `oversized` = 4–24 clusters/run dropped **despite** the "2 to 60 words" bound
  already in `config/prompts/understand.txt` line 18. The bound alone doesn't
  hold against mistral/ministral; a 61–90-word summary is not a ramble.
- Same-run fragmentation (measured, not assumed): `pairs.csv` run 35866102212
  shows the Hormuz tanker split at **sim=0.7658, novelty=fragment** and the
  Trump–Xi meeting at **sim=0.7226, novelty=fragment** — both `kept_below_threshold`
  because the HIGH band (0.80) is a single cosine with no novelty tier. The
  `novelty` field (added Session 21) is currently **observed, not acted on**.
- `why_matters` live uptake: 1 of ~11 rendered entries (~9%); the one line was
  a grounded restatement of the summary's implication, not background.
- gemini free tier is now 20 RPD; `llm/limits.py` quota-skips it. Demotion is
  permanent within the health sample — a quota-starved/transiently-saturated
  provider is never re-tried, so gemini stays 0/0 while groq thrashes 429.

## 15 candidate optimizations (ranked by impact)

1. **Pre-LLM relevance pre-screen** — reuse existing MiniLM embeddings to
   reorder clusters before the LLM call (reorder, never drop), so the cap
   budget covers on-mission clusters instead of ~40% irrelevant.
2. **Same-run fragment merge** — wire `novelty` into `samerun_dedup.py`'s drop
   decision (fragment pairs at [0.60, 0.80) merge; development pairs survive).
3. **Summary trim-not-drop** — trim 61–90-word summaries instead of dropping
   the cluster; keep the `MAX_RESPONSE_CHARS` ramble guard.
4. **why_matters elicitation** — tighten emit trigger + concrete template;
   keep the no-invention fence verbatim.
5. **Cascade self-healing demotion + skip-reason logging** — make demotion
   non-permanent (re-try after N runs) and log per-provider skip reason.
6. cap_dropped relevance surfacing (report) — feeds #1, observability only.
7. Provider skip-reason logging — folded into #5.
8. groq token pacer recalibration — 3rd re-tune; one-variable rule risk.
9. cluster_similarity_threshold review — settings comment notes 0.62 "too
   strict"; needs pairs data first; threshold churn risk.
10. Digest follow-up compression — M1 "follow-up flood" rejected territory.
11. irrelevant-reason subfield (topic vs no-substance) — feeds #1.
12. clickbait threshold review — 6–15/run; verify not over-dropping.
13. lang_drops / language gate — working (0–2/run); monitor only.
14. lead_events visibility — 0–4/run; fine.
15. Provider error-string logging — overlaps #5/#7.

## TOP 5 (selected) + agent specs

All 5 are **file-disjoint**. Each agent creates its OWN test file under
`tests/unit/` and does NOT commit, does NOT run the full suite (other agents
are editing in parallel), and touches ONLY the files listed.

### A1 — Same-run fragment merge
- SCOPE: `src/agent/pipeline/samerun_dedup.py` + new `tests/unit/test_samerun_fragment.py`.
- TASK: in `drop_same_run_dups`, compute novelty BEFORE the drop decision
  (reuse `_content_novelty(event, [other]) or _content_novelty(other, [event])`),
  and drop the weaker side when `sim >= event_repeat_threshold` OR
  (`novelty == "fragment"` AND `sim >= FRAGMENT_FLOOR`). `FRAGMENT_FLOOR = 0.60`
  is a module-level constant (NOT a settings knob), justified by the measured
  0.7658/0.7226 fragment pairs. Preserve the 9r non-transitive-deletion break.
- GATE: `py -3.12 -m pytest tests/unit/test_samerun_fragment.py -q` green, and
  the test asserts (a) fragment pair sim 0.76 drops the weaker side, (b)
  development pair sim 0.76 keeps both, (c) sim < 0.60 keeps both, (d) sim >=
  0.80 drops regardless of novelty.

### A2 — Pre-LLM relevance pre-screen (reorder, never drop)
- SCOPE: NEW `src/agent/pipeline/prerelevance.py` (≤200 lines) + new
  `tests/unit/test_prerelevance.py`. NO wiring, NO settings change.
- TASK: a deterministic, zero-LLM pure function that takes a list of cluster
  text+centroid-vector pairs plus a set of on-mission anchor vectors, and
  returns the cluster list REORDERED (on-mission first, off-mission last) by
  cosine similarity to the anchors. Must never drop — only reorder — so the
  existing cap selection naturally favors relevant clusters.
- GATE: `py -3.12 -m pytest tests/unit/test_prerelevance.py -q` green. Tests
  use SYNTHETIC vectors (MiniLM is CI-only, NOT installed locally — do not add
  it as a dependency). Assert: output is a permutation (same set), a
  known-relevant cluster ranks above a known-irrelevant one, deterministic
  (stable order for equal scores).

### A3 — Summary trim-not-drop
- SCOPE: `src/agent/pipeline/contract.py` + `src/agent/pipeline/batch.py` +
  new `tests/unit/test_trim_not_drop.py`.
- TASK: for summaries that exceed `SUMMARY_WORD_BOUNDS` upper (60) but stay
  within a soft ceiling (e.g. 90 words) AND under `MAX_RESPONSE_CHARS`, trim to
  60 words (or first 2 sentences) instead of returning `within_bounds=False`.
  Real rambles (over the soft ceiling or over `MAX_RESPONSE_CHARS`) still drop.
  Wire the trim through `build_event` so the event survives with a trimmed
  summary. Keep `why_matters` trim semantics unchanged.
- GATE: `py -3.12 -m pytest tests/unit/test_trim_not_drop.py -q` green AND
  `py -3.12 -m pytest tests/unit/test_pipeline_batch.py tests/unit/test_pipeline_compose.py -q`
  still green. Assert: 75-word summary trims to ≤60 and survives; 500-word
  summary still drops; 55-word summary untouched.

### A4 — why_matters elicitation
- SCOPE: `config/prompts/understand.txt` + `config/prompts/understand_batch.txt`
  ONLY (both already have the `why_matters` field). No code changes.
- TASK: sharpen the emit trigger so the model produces the «چرا مهم است:» line
  more often, AND give a concrete template (background/significance, not a
  restatement of the summary). KEEP VERBATIM the anti-hallucination fence
  (no date/number/quote/name absent from the source; omit-rather-than-guess;
  zero extra LLM calls). One variable: only the trigger + template phrasing.
- GATE: `py -3.12 -m pytest tests/unit/test_pipeline_compose.py tests/unit/test_pipeline_batch.py -q`
  still green (prompt change must not break parsing), AND the fence phrases are
  unchanged (grep-verify), AND the new trigger phrase is present.

### A5 — Cascade self-healing demotion + skip-reason logging
- SCOPE: `src/agent/llm/health.py` + `src/agent/llm/limits.py` (skip-reason
  only) + new `tests/unit/test_cascade_selfheal.py`.
- TASK: (1) demotion is not permanent — a provider demoted by accumulated
  failures is re-tried after a cooldown (e.g. `demotion_retry_after_runs` runs
  or `demotion_retry_after_hours`), so a quota-starved/transiently-saturated
  provider (gemini) re-enters rotation instead of staying 0/0 forever.
  (2) log the per-provider skip reason (demoted/quota_exhausted/cooldown/
  breaker) so a 0/0 provider is diagnosable from the run CSV. Do NOT change the
  429/503 breaker semantics (429/503 never count toward the breaker).
- GATE: `py -3.12 -m pytest tests/unit/test_cascade_selfheal.py -q` green. Test
  asserts: a demoted provider is retried after the cooldown elapses; a healthy
  provider stays first; skip reason is recorded per provider.

## Orchestration rules (agents must obey)
- Python: `py -3.12 -m pytest` (3.12 has the deps; `python` is 3.11).
- Constraint 12: no file over ~200 lines.
- One-variable rule: change ONLY the assigned thing; do not re-tune unrelated
  thresholds or drop logic.
- MiniLM/sentence-transformers is CI-only — tests use synthetic vectors.
- Do NOT commit; do NOT run the full suite; do NOT touch files outside SCOPE.
- REPORT: exact files changed + gate result (paste the pytest line) + a 2-line
  summary of the diff.
