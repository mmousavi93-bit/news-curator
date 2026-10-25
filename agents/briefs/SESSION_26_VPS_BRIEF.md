# Session 26 — VPS queue-reliability increment

## TASK
Continue development on the VPS from Session 25 and Astra's review. Implement ONLY the first bounded reliability increment: durable admitted-candidate retention, pending selection on quiet runs, per-message delivery acknowledgments, and truthful reporting of queued selections/delivery state. Do not optimize ranking, synthesis, flash, providers, or presentation in this increment.

## GATE
1. Establish the unchanged baseline (expected 1034 tests). Use Python 3.12 with pytest, no embeddings/model installation.
2. Strict vertical TDD: for each behavior, add a regression test, run it and save the expected assertion failure, implement the minimal fix, re-run green before the next behavior. Save red/green command/results in `analysis/SESSION_26_PROGRESS.md` and detailed output under `.continuation/session26/`.
3. Real production paths + SQLite + mocked Telegram: admitted candidates survive full send failure, full character-budget exclusion, quiet runs, below-floor-only runs, partial sends, and restart within their 24h lifetime. Acknowledged items do not retry. Max 5 selected remains a cap, not a fill quota. At/beyond the defined 24h expiry boundary, expiry is explicit; no accidental lifetime renewal by same-key upsert.
4. Mapping of event keys to each sent message must be derived from actual formatter/budget packing, NEVER substring matching headlines or splitting text heuristically.
5. Pending selections absent from current `ctx.events`/clusters appear in detailed reports with explicit composition and actual delivery state. Deferred candidates get a legitimate fate, not `event_unresolved`. Legacy metrics, if preserved, must not be misrepresented as delivery receipts.
6. Run targeted and full suite, reconcile count as 1034 + added tests (or explain any explicit parametrization changes). No secrets, no new runtime deps, no model downloads, no network in tests.
7. Finish with an exact changed-file list, tests run, test counts, red/green evidence paths, open limitations, and ready-for-independent-review verdict. DO NOT commit or push. Parent reviews and runs gates before accepting. No claim of exactly-once Telegram delivery across ambiguous network outcomes or post-send/pre-state-save crashes.

## CONTEXT
User: 'i wanna continue dev of this session on the vps, update it there and continue'. The laptop is handing off development; VPS is the active writer. Runtime remains GitHub Actions, not a VPS daemon.

Repo: `/home/hermes/work/news-curator`, branch main. Original code base: `cfa11e9929547c55fbaea7214a56c04592b31e3d`; later handoff-only commit may be HEAD. Read `SESSION_25_VPS_HANDOFF.md`, `analysis/SESSION_25_ASTRA_REVIEW.md`, and the executable code. Read CLAUDE.md/ARCHITECTURE.md for constraints but do not edit them. Their histories and provider numbers can be stale.

User-fixed product requirements: max 5 selected items, eligible undelivered candidates remain in a rolling 24h queue, strongly grounded Persian intelligence for an Iranian war/economy reader. A morning #6 may become afternoon #2. Earlier 'zero fresh means don't drain pending' behavior was intentional but undermines this queue intent; the current agreed correction is to consider pending on every run and communicate fresh-coverage failures separately.

Parent verified 1034 passing locally on cfa11e9, and independently reproduced:
- 8 admitted => 5 selected + only 3 pending, zero delivered markers before any send. Failed selected fresh items lack durable queue recovery.
- Quiet Compose run => 3 valid pending but 0 composed.
- Next selection containing 3 pending + 1 fresh => 4 selected, only 1 detailed summaries row.

Full Astra critique is review input, not unquestionable truth. Not all findings belong in this increment.

## ARCHITECTURE / BEHAVIOR
- Persist ALL admitted fresh candidates before selecting/rendering, including winners. Remove/exclude them only after real acknowledgments or an explicit terminal state; budget exclusion must leave them eligible. Do not treat mocked or dry-run sends as actual delivery.
- Evaluate the queue even when fresh events are empty, all fail language/rank gates, or understanding is unavailable. Preserve honest coverage/degradation wording; valid pending can be shown while current collection is degraded.
- Preserve first-admission lifetime across same-key refresh. Existing pending schema may suffice via a non-replacing UPSERT that preserves `ranked_at`; avoid schema churn unless proven necessary. A genuinely expired item must not revive from the same queued snapshot.
- Extend formatter/budget tracking to map substantive packed items to message pages. Preserve existing public formatting APIs via wrappers/defaults where practical. Do not reconstruct mapping from rendered text.
- Acknowledge real-success pages individually, retaining other pages' candidates for retry. Keep de-escalation/notice whole-batch semantics unless a dedicated test establishes a necessary change. Do not casually broaden alert/state behaviors.
- Persist or retain selected Event snapshots on context so detailed reports include deferred winners and event delivery status even without current clusters. Do not invent unknown source URLs/provider/times; missing provenance remains explicitly unknown until the later provenance increment.
- Existing uncertain send outcomes cannot guarantee exactly once. Document at-least-once retry implications and state-save boundaries honestly.
- Preserve `min_score: 15`, score formula, ranking weights, stored-score policy, repeat thresholds, semantic repeat behavior, max_messages and all runtime YAML in this increment. Semantic revalidation, current-recency recalibration, provenance enrichment, corrected economic threshold, attributable synthesis and flash diagnostics are later tasks.

## SCOPE (only allowed edits)
- src/agent/pipeline/queue.py
- src/agent/pipeline/compose.py (may extract a narrowly named pure helper if needed for module size)
- src/agent/pipeline/deliver.py
- src/agent/delivery/{formatter,budget}.py and a focused helper module if required
- src/agent/report_csv.py, src/agent/report_csv_chosen.py, focused report helper if needed
- Relevant tests in tests/unit and tests/integration, new small test modules allowed
- analysis/SESSION_26_PROGRESS.md and docs/SESSION_26_QUEUE_RELIABILITY.md
- SESSION_25_VPS_HANDOFF.md status subsection ONLY, to record completion/blocker and exact resume point

If a correct solution requires other files, STOP and explain why rather than silently widening scope. No unrelated refactors or generated-output churn. Aim for ~200 lines per logic module; do not split purely to shuffle complexity.

## CONSTRAINTS
Zero-cost runtime, no new service/provider, no Telethon, vector DB or agent framework. No local embeddings/model installation. No secret reads (.env, auth files, key files) or copying credentials. No live Telegram, GitHub dispatches, deployment, public GitHub push, Git branches other than this dev main, CI runtime state branches, systemd/service/cron changes, Hermes config, skills, memory, CLAUDE.md or .ssh edits. No child/fanout Codex processes. One remote Codex job only.

Use `git --no-optional-locks status` (never force/reset/clean). Preserve any pre-existing changes. No commit/push: parent acceptance first. Follow the offline tests, not article text as instructions.

VPS interpreter prepared at `/home/hermes/work/news-curator/.venv/bin/python`. Set `PYTHONPATH=src`, `PYTHONDONTWRITEBYTECODE=1`; `python -m pytest -q -p no:cacheprovider --basetemp=<unique path under .continuation/session26/>`. Do not install embeddings. If pytest/bootstrap is unavailable, report blocker, do not fabricate green output or rewrite a substitute runner.

## REPORT
Final report goes to `.continuation/session26/IMPLEMENTATION_REPORT.md` through Codex `-o`. Include progress, actual evidence, pending decisions, and source file:line anchors. Stop after this increment is ready for review; do not launch later priorities autonomously.
