# Stage 3a offline persistence and replay

Run `py -3.12 -B analysis/eval_signal_extraction.py` from the repository root.
Add `--dataset <history.json> --labels <labels.json>` for captured source histories.
The machine-readable JSON report goes to stdout; the command writes no artifacts
and makes no network/model calls. Replaying identical captures is deterministic.
Missing or empty labels report **no labels; accuracy gate incomplete**.

The dataset and independent label contracts are documented in
[`tests/fixtures/risk_extraction/README.md`](../tests/fixtures/risk_extraction/README.md).
Captured raw responses pass through the existing extractor and deterministic
attribution/history/calendar code. Source snapshots, original text and publication
timestamps are inputs. Cutoffs are injected and future evidence is rejected.
Skipped/refused/unavailable clusters remain unknown and make canonical days
incomplete. Attribution rejection is reported without mutating accepted history.

The report includes cluster fates, parse counts, unknown/abstention counts,
invalid IDs/evidence rejections, collection completeness, calls/tokens, missing
markets and extracted scores. With independent labels it also computes candidate
and accepted TP/FP/FN, micro precision/recall/F1, accepted per-ID/category and
candidate per-language breakdowns, exact field accuracy, floor-critical misses,
quiet correct-none, and supplied history/source coverage. Empty denominators are
null. Gold positives with missing predictions remain false negatives.

Five **synthetic scorer fidelity** checks import the unchanged `SCENARIOS` without
calling the CSV-writing main. Explicit-count SignalEvents must match rounded
reference state-mode arithmetic and all five bands. These checks do not provide
source coverage or extraction accuracy and may contain synthetic future state
ends; those are never used as source histories.

`memory.db.persist_signal_history` writes evidence, occurrences/episodes,
baselines, calendar, novelty ledger and daily checkpoints in one savepoint.
`load_signal_history` requires an existing complete ledger/catalog. Explicitly
construct `SignalHistory(catalog)` only for an authorized new offline history.
Use plain mapping `source_snapshots` with original configured tier/group, a stable
`run_key`, an aware `observed_at`, and optional `RawCandidateRecord` captures.
Reusing a key with different state/provenance or writing older state halts.
Identical replay is a no-op, including after restart. Dates and observation times
are separate. Open/end/confirmation/check dates and linked evidence round-trip.

The additive extension has its own `meta.signal_schema_version=1` marker while
the existing database version remains 4 for compatibility. On open it creates
only missing companion tables and never replaces existing tables or columns.
`signal_occurrences` links the existing `signal_events` table;
`signal_baselines` links `speaker_statements`; `signal_calendar_rows` links
`scheduled_events` and soothing statement rows. Existing columns remain intact.
Raw evidence and source snapshots do not reference expiring collector item rows.
Retention uses at least 180 days for extraction evidence, 45 for baselines,
protects active states and referenced baselines, and preserves novelty/checkpoints.

Pending human work: five complete source histories, independent reviewed labels,
semantic evidence/date/corroboration audit, oracle versus extracted scores,
keyword baseline comparison, uncertainty estimates, repeated live classifications,
latency/token budgets and digest regression. No historical accuracy is claimed.
Production wiring (stage 4), model calls, scorer/weight edits and labels remain
outside this stage.
