# SESSION_24_BRIEF — Persist the oversized bounds_reason (mistral-verbosity observability)

## Why

7-day calibration (9 runs, 1,388 clusters) found the "بدون خلاصه ماند" footer
defect is NOT an outage (`llm_failed=0` every run). It is the LLM-output
contract gate:

- 91 items dropped at the understand contract: **86 `oversized` + 5 `unparseable`**.
- All 86 oversized came from **mistral** (`ministral-8b-2512`, the last cascade
  rung) — measured via the `provider` column: runs where mistral served 14–17
  calls produced 15–24 oversized; runs where it served 0 produced 0.
- The numeric bounds ARE already in both prompts ("2 to 60 words (never
  empty)"), so mistral overshoots the bound it was told. The trim-not-drop band
  (61–90 words) already salvages the mild over-run; the 86 are the >90-word
  "ramble class" (or 61–90 run-ons) that `within_bounds` rejects.

The blocker to a correct fix: `within_bounds` computes the exact reason
(`"summary 97 words (bounds (2, 60))"`, `"headline 30 words"`, etc.), logs it,
then **throws it away** — `process_element`/`run_single` return only the fate
string `"oversized"`, and `report_csv_chosen._fate_for` writes `reason=""`. So
the CSV cannot tell a 95-word mistral Persian summary from a 150-word ramble,
and the 90-word soft ceiling cannot be calibrated against real mistral word
counts.

Also found (diagnosis, out of scope for this change):
- **gemini makes 0 calls in every run** despite being first in
  `llm.order` and its key being exported — 20 free RPD head-start wasted,
  forcing more fall-through to mistral. Root cause is in the run log (Azure,
  often blocked from Iran), not the CSVs.
- groq 429s ~50% of calls (the TPD daily wall — a known, documented limit).

## Change

Persist the contract-gate reason for every LLM-understand failure fate into
`chosen_<ts>.csv`, mirroring the existing `repeat_drop_reasons` pattern:

1. `batch_run.py`: `process_element` returns `(event, fate, reason)`; `run_batches`
   accumulates a `fate_reasons` dict for `oversized` (the `bounds_reason` word
   count), `unparseable`, `unavailable` (status), and the batch-level
   raw-length `oversized`, and stashes it on `ctx.fate_reasons`.
2. `single_run.py`: same `fate_reasons` accumulation + stash (the rollback
   path must not drift).
3. `report_csv_chosen.py::_fate_for`: when a fate comes from `cluster_fates`,
   read its reason from `ctx.fate_reasons` (fall back to `""` when unset).

No threshold, prompt, or pipeline-behavior change. Purely additive observability.

## Gate

- `pytest -q` green (Py3.12 interpreter).
- Secret scan clean.
- New regression tests: oversized reason carries the word count; `fate_reasons`
  unset falls back to `""` without crashing.

## Next step (after this lands and one live run completes)

Read the actual mistral `summary N words` distribution from the next
`chosen_*.csv`, then calibrate `SUMMARY_SOFT_CEILING_WORDS` (90) — or add a
mistral-specific trim — against real numbers instead of the current English-
terse assumption. Do NOT raise the ceiling blind.
