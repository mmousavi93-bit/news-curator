# Session 27 — quality and capacity verification

## What changed

The serial router may use the configured Groq overflow key when its exact same model will become available sooner. A ready preferred provider still wins. RPM/TPM safety windows, real-usage correction, breaker/cooldown behavior and daily/per-run budgets remain enforced. This does not proactively promote Mistral or change model IDs, source coverage, clustering caps or ranking thresholds.

The optional Persian overview now requires two distinct event IDs and exact current-event quotations. Structural checks reject unknown IDs, fabricated or cross-event quotations, unsupported numeral/date/sign tokens, and generated background offered as evidence. Non-confirmed evidence receives explicit unverified-report attribution. This is not semantic entailment proof: supported quotations alone cannot guarantee that every connective or inference in generated prose is correct.

Composition first packs substantive news, asks for an overview only from full-form survivors, and discards the overview if adding it would demote or exclude any originally full item. HTML escaping remains the formatter's responsibility. No second synthesis pass is attempted. The existing five-item maximum, full 24-hour queue and real page-acknowledgement implementation are unchanged.

## Source and integration

Development began at `29626f035efaf58a5227f8ff8f561f0c0fdffa0e`, with 1,265 passing tests. Before parent verification, the worktree fast-forwarded to `131655ed8f3c0b6d78d00445fd56002941da6b52` to preserve unrelated upstream risk-persistence work. That work's ten paths do not overlap this release's implementation. This release does not claim ownership or independent approval of that upstream feature.

The implementation brief is `agents/briefs/SESSION_27_BRIEF.md`. No settings, source registry, dependencies, risk logic, runtime workflow, Telegram credentials or state branch were changed by this work.

## Executed evidence

- Parent initial full collection and execution after integration: **1,347 collected; 1,347 passed in 65.71s**. Final parent run after the numeric-token edge cases: **1,349 passed in 61.94s**. These are local results, not CI status.
- Parent focused queue/receipt/synthesis/capacity execution: **82 passed** before the final three numeric regressions and chronological replay test were added.
- Parent found that the first numeric guard accepted newly negative numbers and rearranged slash dates. All three regressions genuinely failed before the fix and passed afterward; the combined synthesis contract run passed 32 tests.
- Implementation RED/GREEN records and parent execution logs are retained under ignored `.continuation/session27/`; the public tests are the reproducible acceptance gates.

### Capacity experiment — simulated, not a live speed claim

`tests/integration/test_session27_capacity.py` drives the actual router with a fake clock and explicitly simulated successful HTTP responses. Both Groq keys use the exact production model ID and the configured 8K TPM/30 RPM limits. There are 24 successful logical requests with identical token footprints and one-second simulated response latency.

| Actual code under test | Requests | Simulated elapsed | Pacing time |
|---|---:|---:|---:|
| Pinned pre-change router, both keys configured | 24 | 2,761 seconds | 2,737 seconds |
| Changed router, both keys configured | 24 | 1,322 seconds | 1,298 seconds |

The elapsed reduction is **52.12%** in this controlled case. The old router sent all 24 requests to the preferred key; the new router sent 12 to each. These are not fabricated production observations: they are explicitly synthetic transport results used to measure scheduling behavior. Real outages, quotas, malformed model output and collection latency can change production performance.

### Chronological archived queue replay

`tests/fixtures/session27_archive.json` contains the observed admitted, scored snapshots from Actions runs **40332907395** and **40348842883**. `tools/replay_digest_snapshots.py` uses the real composer and SQLite store, blocks network access, rejects an existing database target and checks the actual isolated DB path. It bootstraps the first observed pending queue, admits subsequent fresh snapshots, compares selections with historically acknowledged keys, and replays those observed receipts without importing or calling Telegram delivery.

| Snapshot | Admitted pool | Selected | Deferred |
|---|---:|---:|---:|
| 40332907395 | 40 | 5 | 35 |
| 40348842883 | 46 | 5 | 41 |

Pre-change and changed code selected identical event-key sets. All 35 deferred keys from the first snapshot remained candidates in the second. Every selected item retained full form, with matching page identities. Exactly one logical overview fixture call occurred per snapshot.

Limits: the source CSVs contain understood outputs, not independently fact-checked articles. They omit original admission timestamps, previous-context fields and unrounded scores. The replay therefore characterizes the post-ranking queue boundary, not source-level relevance, upstream extraction coverage, whole-day retention or an exact historical rendering. Its two overview responses are explicitly curated test fixtures, not live model responses. Existing SQLite retention and delivery regressions cover the other invariants. No ranking-floor or deletion-gate calibration is claimed from these two snapshots.

Reproduce locally (Python 3.12, `PYTHONPATH=src`, no keys/network/models):

```text
python -m pytest -q -p no:cacheprovider
python tools/replay_digest_snapshots.py --source . --fixture tests/fixtures/session27_archive.json --database <NEW_SCRATCH_DB_PATH> --output <SCRATCH_REPORT_PATH>
```

## Release gates and limits

Independent review, parent hash/scope/credential-pattern checks, publication and exact-head CI are tracked separately in the local release manifest. A local green suite is not proof of those steps. Do not infer a production release from this document alone; verify the commit and CI run.

No manual live digest is authorized or performed for this release. Normal existing schedules remain intact. No improved reader engagement, complete factuality, exactly-once delivery, live runtime reduction or optimal ranking is claimed. Rollback is a normal revert of this bounded change, never a reset of encrypted state.
