# SESSION_10H_BRIEF — reconstruct the accuracy-gate dataset (consultation)

## Context
To build the 5-window accuracy-gate dataset (stage 3b), clusters must be
reconstructed from the pipeline's historical run-reports. The CI-only embedder
(MiniLM) is involved, so consult before writing any embedding/re-clustering code.

## What the artifacts actually contain (verified by reading the CSVs)
- `read_*.csv`: `source_id,url,title,body,published_at_utc,date_only,lang`.
  Body is TRUNCATED at ~400 chars (documented) — the raw item text is not full.
- `chosen_*.csv`: `cluster_key,fate,reason,n_members,sources,provider,best_tier,
  category,claim_status,independent_count,score,headline,summary`. `sources` is a
  pipe-separated SOURCE-ID list — there are NO member URLs, so the member→cluster
  mapping cannot be read directly.
- `Cluster.key` = sha256 of sorted member URLs ([:16]); deterministic on members.

## Proposed reconstruction (validate or correct)
In CI: download run-reports → parse `read_*.csv` items → embed with MiniLM →
`cluster_items` → recompute `Cluster.key` → join to `chosen_*.csv.cluster_key` →
emit `dataset.json`.

## Known problems with that approach
1. Body truncation means re-embedding differs from the original run, so some
   clusters split/merge and their recomputed key misses `chosen_*.csv` (the
   join-rate loss the project already documents).
2. No member URLs in `chosen_*.csv`; only source ids — so membership must be
   re-derived, not read.
3. Raw LLM responses were never persisted, so the dataset's `capture` field must
   be filled by FRESH re-extraction at eval time, not from history.

## Questions (report only, edit nothing)
1. Is re-embed + re-cluster (MiniLM + `cluster_items` + `Cluster.key` join) the
   right reconstruction? Or is there a better way that avoids the body-truncation
   join loss — e.g. reconstruct membership by matching `read_*.csv` URLs against
   the `sources` list, or cluster a different way?
2. What is an acceptable join rate, and how should unmatched/split/merged
   clusters be treated in the dataset (drop, keep as `fate`-only, flag)?
3. Confirm the `dataset.json` shape for the accuracy gate, specifically what goes
   in `capture` given raw responses are not persisted — fresh re-extraction, or a
   marker that makes the harness skip capture and re-run live?
4. Any simpler alternative that avoids the embedder entirely for this gate?
