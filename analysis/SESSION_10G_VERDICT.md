# SESSION_10G — reconciliation verdict (astra, read-only consultation)

## Verdict
Split the two gates. Re-dating the accuracy gate to real Aug–Oct 2026 windows is
correct — but treat Aug–Oct 2026 as a **candidate search period**, not five
already-available windows. The inspected local artifacts (2× Sept 8 run-reports
+ 1× Sept 28 fixture; 201/437/436 items) cannot complete either five-window gate
as-is: missing bodies, many `cap_dropped`/`unavailable`, timestamps past cutoff,
and **no raw extraction responses or credibility snapshots** in `chosen_*.csv` /
`read_*.csv`.

## Decisions
1. **Scorer fidelity** = the 5 synthetic backtest scenarios + bands (already
   reproduced exactly in stage 2). Unchanged.
2. **Extractor accuracy** = 5 real windows selected by evidence + collection
   completeness, compared to independently labeled oracle scores (NOT the
   synthetic bands). Nonzero B1/B2/B6/E1/E2 positive coverage required — zero
   misses on zero examples is not coverage.
3. **Historical dates remain methodologically valid only via external archival
   reconstruction** (contemporaneous articles, verified dates, reconstructed
   clusters, provenance) — heavy, and must disclose archive-selection gaps.
   Re-dating is the practical repo-backed choice.
4. **Worksheet corrections** (applied in SESSION_10F): separate synthetic vs
   real windows; qualify Half-A sourcing; keep captures hidden from labelers;
   restore the no-false-tier-rise requirement.

## Data gap (for stage 3b)
Full run history lives on GitHub artifacts (`tools/download_run_reports.py`),
not locally. Raw LLM responses are not persisted anywhere in the project (the
`capture` replay field cannot be back-filled from history) — so the accuracy run
requires **fresh re-extraction** of reconstructed clusters, not captured replay.
