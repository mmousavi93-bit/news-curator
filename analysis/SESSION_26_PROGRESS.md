# Session 26 — local queue-reliability progress

## Current state

Development remains local in `Hermes Projects/News Curator/session26-local`, branch `dev/session26-queue-local`, implementation base `72a568eda00d5eeff132b6abf52bbd6869b6cff3`. **Local acceptance complete: 1059 tests pass; final independent review SHIP, 38/40, zero BLOCKING/MAJOR.** Parent verified all 11 source/test files against their full pre-review SHA256 values, independently verified the reviewer's focused-suite log, and reran its 19 assertions with a non-vacuous fresh-deferred fixture. The owner subsequently authorized commit/push. No manual production dispatch or live Telegram send is part of this release; remote publication and CI must be verified separately.

Review history: round 1 rejected the publication/admission-time regression (33/40); it was repaired. Round 2 returned SHIP (37/40) but missed the parent-reproduced MAJOR same-key reporting collision, so acceptance was withheld and it was repaired. Fresh round 3 used configured `metis-deepseek-pro` / `deepseek-v4-pro` with invocation-only flags; no Hermes config changes or fourth round. Final local acceptance is based on actual gates and source fingerprints, not reviewer score alone.

The VPS is reachable again. Its working tree is clean at the same base. The old runner exited 0 but its implementation report states bubblewrap blocked every command: no implementation changes and no tests from that job. Parent independently ran the remote unchanged suite after access recovery: **1034 passed in 72.31s**. Remote evidence: `.continuation/session26/parent-resume-validation.log`. No remote job was restarted.

Codex local execution was blocked by `401 token_revoked`; the main assistant implemented directly. No login/config changes were made.

## RED → GREEN evidence

Detailed logs live under ignored `.continuation/session26-local/` in this worktree. Every regression exercises real queue/compose/delivery/report paths and SQLite; sends are offline fakes.

| Behavior | Red evidence | Actual red result | Green evidence |
|---|---|---|---|
| Persist selected and deferred admissions across restart | `retention-red.log` | 1 failed: only 3 of 8 admissions survived | Included in `partial-green.log`, `report-green.log`, `full.log` |
| Pending selection on quiet / below-floor / AI-failed / language-failed runs | `quiet-red.log` | 4 failed | `quiet-green.log`: 54 passed |
| Preserve first admission; exact 24h expiry and expired-snapshot replay | `expiry-red.log` | 1 failed | `expiry-green.log`: 55 passed; this initial implementation still had the publication-time regression found in review |
| Per-page acknowledgment with identical headlines and partial send | `partial-red.log` | 1 failed | `partial-green.log`: 55 passed |
| Pending winners / deferred fates / budget exclusions in CSVs | `report-red.log` | 3 failed | `report-green.log`: 90 passed |
| Admit 26h/71h-old date-only publication NOW, retain the full admission lifetime; prevent timestamp-free expired replay after restart | `review-fix-red.log` | 3 real assertion failures | `review-fix-green.log`: 93 passed; final `review-fix-full.log`: 1053 passed |
| Authoritative stored snapshot/lifecycle despite same-key current rejection; do not borrow current-only provenance | `snapshot-red.log` | 6 real assertion failures: selected/deferred/expired x language-rejected/unavailable | `snapshot-green.log`: 99 passed; `snapshot-full.log`: 1059 passed |

An interrupted continuation later overwrote the misleadingly named `retention-green.log` with a broader run containing the then-expected partial-send failure. Do not cite that file as green. The subsequent 55/90/full green runs include the retention regression.

## Commands and measured results

Interpreter: `C:/Users/CafeBazaar/AppData/Local/Programs/Python/Python312/python.exe`; environment `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src`. All pytest commands use `-q -p no:cacheprovider` and separate `--basetemp=.continuation/session26-local/<gate>-temp`.

- **Current final-candidate full suite (`snapshot-full.log`): 1059 passed in 63.25s**, exit 0. Focused suite (`snapshot-green.log`): **99 passed in 8.81s**. Audit independently collected 25 added cases and reconciled baseline 1034 + 25 = 1059. Earlier counts below are historical checkpoints, not the current gate.
- Current file/syntax/module-size/credential-pattern audit (`snapshot-audit.log`, `verification.json`): 14 changed files, zero recognized credential-pattern hits, `git diff --check` clean. Parent original collision probe now reports `chosen_matches_actual_snapshot: true` (`parent-review/snapshot-collision-green.json`).
- Additional production-path report matrix (`parent-review/report-matrix.json`): **18 cases passed, zero violations**, covering current normal/cap/pre-LLM overlap, selected/deferred/expired queue states, and tight/normal budgets. Confirmed unique rows, authoritative snapshot/fate, unknown provenance left blank, unrelated current loss diagnostics preserved, and no invented receipts. These are separate probes, not added to the 1059 pytest count.
- Round 3 actual focused log (`review-three/focused.log`): **99 passed in 8.51s**. Parent rerun of reviewer probes (`parent-review/verified-independent-probes.log/.json`): **19 assertions passed**, zero failures; forcing cap 2 yielded **2 actual fresh-deferred rows**, avoiding a vacuous `all([])` check. All probe temporary files stayed under the allowed local evidence directory. These assertions are separate from the full pytest count.
- Parent full source/test SHA256 comparison: **11 files verified, zero mismatches**, unchanged HEAD. Exact hashes and acceptance metadata: `final-source-acceptance.json`.
- Publication preflight reran the complete suite: **1059 passed in 59.93s**, exit 0 (`publication-tests.log`). All 11 reviewed source/test fingerprints still match. The full outbound range includes the previously VPS-only handoff/docs commit; recognizable credential patterns were checked across all **24 outgoing files**, with zero hits. `CONTINUITY.md` received a status-only correction so its active section no longer sends development back to the VPS. The increment now contains **15 files**, including that additional status document; no reviewed source/test code changed.
- Staging exposed one cosmetic whitespace warning previously invisible while the new test was untracked: `tests/unit/test_queue_reliability.py:350: new blank line at EOF`. The reviewed fixture bytes are preserved, rather than changing a frozen test. This is an explicit non-blocking exception, not a claim that strict staged whitespace checking is clean. Twelve Markdown hard-break warnings in the previously committed Astra report are likewise preserved. No other outgoing whitespace warnings are accepted.

- Unchanged full baseline (`baseline.log`): **1034 passed in 84.29s**.
- Targeted suite (`report-green.log`): `tests/unit/test_queue_reliability.py tests/unit/test_delivery_page_tracking.py tests/unit/test_report_csv.py tests/unit/test_pipeline_queue.py tests/unit/test_pipeline_compose.py tests/unit/test_pipeline_deliver.py` — **90 passed in 7.13s**.
- Full suite (`full.log`): **1050 passed in 60.57s**. Expected reconciliation: baseline 1034 + 16 added cases. No old tests removed; one original assertion now correctly treats selection as admission rather than delivery.
- Post-review final full suite (`review-fix-full.log`): **1053 passed in 59.91s**, exit 0. Reconciliation independently counted by `verification.json`: baseline 1034 + 19 new cases. Includes bounded expiry-marker cleanup, nonrenewal, direct-upsert protection and unrelated-meta preservation assertions.
- Allowlisted changed+untracked file audit (`review-fix-audit.log`, `verification.json`): 14 files, zero recognized credential-pattern matches, AST syntax and <=200-line production-module gates passed; `git diff --check` passed. This is not an exhaustive secret/history audit.
- Packing metadata property loop compares new tracked output with the legacy formatter across small UTF-16 budgets, identical headlines, priority ordering, overflow reservation, full details and headline-only fallback.
- Additional restart tests cover failed, mocked, dry-run and unavailable delivery paths without real receipts.

## Implementation boundaries

All admissions are persisted before rendering/sending using production SQLite autocommit. Same-key updates preserve `ranked_at`; exact expiry is excluded. Successful nonmocked pages write only their actual packed event keys. Other pages/candidates remain retryable. Notice/escalation state still requires the whole batch to succeed.

Correction to the initial expiry implementation: `Event.first_seen_at` is earliest publication, NOT queue admission (`batch.py`), so it cannot gate fresh admission. Expired queue keys now get compact `meta` terminal markers before full snapshot deletion. Replay/direct persistence cannot renew or bypass them. Markers age out after existing `retention.events_days` from observed expiry; behavior beyond aged-out memory is not guaranteed, just as existing event/delivery memory is finite. No schema or runtime-setting change.

Detailed reports preserve pending snapshots and expose composition, delivery and origin separately. Unknown queue provenance remains blank. Legacy `sent`/`sent_followup`/run `sent` still mean composition, not receipts; legacy `deliver` also includes mock successes and lead sends. New receipt fields are authoritative.

Same-key reporting correction: actual composed/stored snapshots precede current rejected copies, and queue lifecycle precedes a current extraction-failure fate. Pending/expired rows use queue-snapshot reporting even if a current cluster has the same key; current-only provider/source/time/pre-LLM metadata is not attributed to the older snapshot. Non-queue legacy fate handling is preserved. Round 2's MINOR blank score for expired snapshots is deferred: missing but honest, not an invented score. The headline-only delivery wording was clarified: delivery markers are binary; render form lives in `composition_state`.

No runtime YAML, floor15, scoring weights, semantic repeat logic, synthesis, flash or providers were changed. Pending semantic revalidation, chronological threshold/score replay, provenance enrichment and presentation remain separate increments.

## Acceptance and deferred limits

All local implementation, regression, scope/syntax/credential-pattern and independent-review gates are complete. Round 3 SHIP is accepted by the parent for this bounded increment. Review evidence: `.continuation/session26-local/review-three/cli-output.log`, `MANIFEST.md`, `focused.log`; parent acceptance `final-source-acceptance.json` and `verification.json` under `.continuation/session26-local/`.

Deferred non-blocking reporting findings: expired snapshots may have a blank historical score (MINOR); a current replay rejected by a historical terminal marker can show origin `pending` rather than `fresh` (NIT), though expiry/selection behavior is correct. No fourth review and no cosmetic code changes after the final source pin.

Local acceptance itself did not authorize publication; the owner subsequently approved commit/push with a separate continuation. This release does not manually dispatch a production workflow or send Telegram messages. Remote commit and exact-head test/embedding CI are the publication gates, not proof of live delivery. Exactly-once delivery remains unpromised across ambiguous Telegram responses, a send-to-receipt-save crash, or unsuccessful CI encrypted-state persistence. Broader ranking/synthesis/flash/provenance/semantic revalidation work remains separate.

## Exact increment files (15; reviewed code unchanged)

- `CONTINUITY.md` (active status subsection only; publication preparation)
- `SESSION_25_VPS_HANDOFF.md` (active status subsection only)
- `analysis/SESSION_26_PROGRESS.md`
- `docs/SESSION_26_QUEUE_RELIABILITY.md`
- `src/agent/delivery/budget.py`
- `src/agent/delivery/formatter.py`
- `src/agent/pipeline/compose.py`
- `src/agent/pipeline/deliver.py`
- `src/agent/pipeline/queue.py`
- `src/agent/report_csv.py`
- `src/agent/report_csv_chosen.py`
- `src/agent/report_csv_lifecycle.py`
- `tests/unit/test_delivery_page_tracking.py`
- `tests/unit/test_pipeline_queue.py`
- `tests/unit/test_queue_reliability.py`
