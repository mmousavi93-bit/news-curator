# SESSION_10G_BRIEF — reconcile the accuracy-gate cutoffs (consultation)

## The problem (found during review, not by you)
Your 10D plan says the 5 hand-labeled histories should be frozen "at the exact
scenario cutoffs" (2025-06-11, 2025-04-20, 2026-02-21, 2026-02-27, 2026-07-07).
But those are the SYNTHETIC backtest dates. The pipeline's first run was ~Aug
2026 — no run-reports (`read_*.csv` / `chosen_*.csv`) exist for any of them, so
there are no source-grounded clusters for the extractor to run on. "Assemble the
dataset from run-reports" is impossible for those five dates.

## The proposal (validate or correct)
Split the two gates that 10D conflated:
- **Scorer fidelity** = the 5 synthetic backtest scenarios (already reproduced
  exactly in stage 2; keep them there).
- **Extractor accuracy** = 5 REAL pipeline windows from Aug–Oct 2026, where
  source-grounded clusters actually exist.

## What I need from you (report only, edit nothing)
1. Confirm or correct the re-dating.
2. Specify the SELECTION CRITERIA for the 5 real windows, preserving the intent
   of the original five: TACT-heavy pre-strike, STRAT posture accumulation,
   strike onset, second TACT spike, and a QUIET week with no false tier rise.
   What must each window contain to be a valid accuracy-gate sample (signal
   diversity; floor-critical B1/B2/B6/E1/E2 coverage; quiet-week negatives)?
3. Any correction to `analysis/SESSION_10F_LABEL_WORKSHEET.md` (its "Half A
   source = run-reports" claim is now known-wrong for the historical dates).
4. Is there any defensible way to KEEP the historical dates (external archive
   sourcing of raw news clusters), or is re-dating to Aug–Oct 2026 strictly correct?

## CONSTRAINTS
- Read-only. Report a verdict + criteria + worksheet corrections.
- The extractor accuracy gate must use real, source-grounded clusters only —
  never manufacture articles from SCENARIOS.
