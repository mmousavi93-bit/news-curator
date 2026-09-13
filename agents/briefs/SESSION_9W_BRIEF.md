# Session 9w — hotfix: understand max_tokens 700→2000 (batched output budget)

Date: 2026-09-13. Commit: `fbec745` (pushed to main). Suite: 844 passed.

## The bug (why the channel showed "N خبر بدون خلاصه ماند")
- 9v's 2–3-sentence summaries pushed a 5-cluster batch JSON array past the
  single-cluster `max_tokens: 700` cap → every response truncated mid-JSON →
  `extract_json_array` raised → every cluster "unparseable" → the digest
  dumped raw source titles ("خلاصه خودکار در دسترس نیست — عناوین خام منابع").
- Live evidence: run 34752049889 (schedule, 75e0b73) — 40/40 clusters
  unparseable, 0 events, 0 sent.
- Root cause: 700 was set in 9i (ramble cap, ~400-token single cluster) and
  never scaled when 9s introduced batching. Weekly-audit correction (09-13):
  the break began at 9s — its FIRST run (aea8400, 09:55 UTC) was already
  35/40 unparseable; 9v's longer summaries only finished it (40/40). 9s's
  batching crossed the 700 cap on its own, not 9v.

## The fix
- `src/agent/llm/providers.py`: max_tokens 700→2000 (worst case at field
  bounds × batch 7 ≈ 1540; `within_bounds` + `MAX_RESPONSE_CHARS` stay the
  real content gates).
- `src/agent/pipeline/contract.py`: stale "1-2 sentence" comment → "2-3".
- `tests/unit/test_llm_providers.py`: shape test 700→2000 + new
  `test_max_tokens_covers_worst_case_batch_output` regression guard.

## Verified
- Manual dispatch run 34752866099 (small catch-up cycle): 0 unparseable,
  4 events, 3 sent with real 2–3-sentence mini-briefs. Dedup working
  (0.82 collapsed; 0.44 / 0.50 kept).

## PENDING — verify on the next FULL schedule digest
- Next full run: `pipeline` workflow, cron `30 8,11,14,17,20` UTC → 11:30 UTC
  (15:00 Tehran), on `fbec745`.
- Steps: list runs → find the schedule `pipeline` run on/after fbec745 →
  download `run-reports` artifact → read `run_*.csv` (events>0, no
  unparseable), `summaries_*.csv` (elaborated summaries), `chosen_*.csv`
  (fates), `pairs_*.csv` (similarity vs 0.80).
- Verify: (a) economy surfaced? (b) corroboration shown (independent_count≥2)?
  (c) dedup distances; (d) elaborated mini-briefs present.
- Note (corrected by weekly audit): economy was already surfacing pre-9v —
  26 of 31 healthy runs sent 1–5 economy items at weight 2. The 09-13
  catch-up's "0 economy" was an artifact of a tiny war-heavy cycle, not a
  missing-sources gap. Economy weight 2→5 is tuning an already-working
  feature — verify it doesn't drown politics/security on the full digest.
