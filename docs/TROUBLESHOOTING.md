# Troubleshooting

Failure signatures, most likely first. All logs are public-redacted — read
them in Actions without fear, but never paste a secret into a file.

## Run failed: "DECRYPT FAILED -- halting (constraint 14)"

The state branch file will not decrypt. Causes: wrong AGE_SECRET_KEY
secret, or a corrupted state.db.age. Check the state-branch artifact
history; restore the last good artifact (it is the same file), or re-run
the RUNBOOK bootstrap if no state ever existed. The halt is deliberate:
starting from empty memory is worse than not running.

## Run failed: "no state database at ..."

The decrypt step did not produce state.db. Same causes as above; the
pipeline correctly refuses to create one implicitly. `--init-db` exists
for the one-time bootstrap (RUNBOOK §3) and must never appear in a
workflow.

## Gate failed on collect-test

Read the named failure lines, do not re-run blindly (and never "Re-run
jobs" — it replays the old SHA; dispatch fresh):

- "every published_at is null" → the source's date format defeated the
  parser (collector bug; open an issue with the report JSON).
- "newest item is N days old" → the source is dormant (source fact;
  replace or disable it — see ADDING_SOURCES.md §4).
- "all kept items share one identical published_at" → a now()
  substitution bug in a collector. Real; fix it.
- Date-only advisories sharing one date are EXPECTED and do not trip this.

## "coverage: signal X covered by no enabled source"

A signal (risk_weights.yaml) has no witness. Add it to a plausible
source's `signals_covered`, or accept the gap knowingly (G1/G2 are
supplied by the markets fetcher, not by sources). With
`coverage_check_fails_build: false` this warns; promoted to true it halts.

## "source health: X degraded"

X has returned no items (or errored) for `degraded_after_empty_runs`
consecutive runs. The counter resets on one healthy run. If it never
resets, the feed is dead — replace it (ADDING_SOURCES.md).

## Digest silent for days, runs green

60-day cron kill switch (RUNBOOK §8), or — less likely — every run
produced "Nothing new": check a log for `collect: fetched=0` and a
`source health` cascade, which means the network path changed, not the
world.

## Local runs on Windows

`python -m agent.run --dry-run` runs the whole pipe offline (mock wiring:
no keys, no model, no network). `pip install -e .[dev]` for the test
suite; `python -m pytest -q` must match the count recorded in
POSTMORTEMS.md (464 as of 2026-08-29) — a mismatch is investigated, not
ignored. Do NOT install `.[embeddings]` locally; the ~2 GB torch stack is
CI's job.

## The one rule for every bug you file

Attach the JSON report / the log lines, never a screenshot of them, and
say which workflow run (the commit SHA is printed in every job).
