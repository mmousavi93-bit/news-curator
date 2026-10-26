# Queue reliability contract — Session 26

Development is local; production remains GitHub Actions. No new dependencies, services or model downloads.

## Selection is not delivery

Every fresh candidate admitted by the existing language/rank gates is stored in `pending` before selection, formatting or sending. Both selected winners and deferred losers survive process restart. Select at most five from the eligible fresh+pending pool. Empty-fresh, below-floor-only and degraded-understanding runs still consider valid pending snapshots; coverage warnings remain visible.

`ranked_at` is the first queue-admission time, **not article publication time**. An old/date-only article that passes the existing rank/language gates can be admitted now and gets the full queue lifetime. Updating an existing key refreshes its Event JSON/score without extending that clock. Rows at or beyond the configured 24-hour boundary are explicitly expired, not selected.

Before removing an expired snapshot, the queue stores a compact key/time terminal marker in the existing `meta` table. This prevents same-key replay after restart without consulting publish time or inventing a new article-age gate. Terminal markers age out after the configured `retention.events_days` from observed expiry, like the existing finite event/delivery memory; rejection is not promised after that memory has aged out. Delivered keys are excluded and pruned. Stored-score aging and semantic revalidation are deliberately not redesigned here.

Production SQLite connections use autocommit. This protects admission/receipt writes on the current machine's database. On ephemeral GitHub Actions runners, future runs still depend on successful encryption and persistence of that database to the state branch. This increment does not claim to repair a failed workflow state save.

## Packing and acknowledgment

The final greedy packing pass exports item indices plus full/headline-only forms for each page, including the final overflow re-pack. Compose maps those indices directly to Events in the renderer's item order. Headline searches, text splitting and header/footer matching are not used.

Each successful nonmocked Telegram page acknowledges only the event keys actually packed on that page, immediately before the next send. Failed/unknown, mocked, dry-run, unavailable-client and budget-excluded candidates remain unacknowledged. A retry after partial success selects only undelivered candidates. A real acknowledgment writes the binary delivered marker; that marker does not encode the render form. The separate `composition_state` preserves `headline_only`, rather than claiming the summary body was delivered.

For legacy callers without page metadata, whole-batch acknowledgment remains the conservative fallback. De-escalation notice/escalation-day state retains its existing whole-batch-success gate.

A lost Telegram response can mean the server delivered the page while the client cannot confirm it. A crash between successful send and local receipt persistence can also produce a retry. This is **not exactly-once delivery**.

## CSV interpretation

`chosen` now includes queue-only selected/deferred/expired snapshots even when current clusters no longer exist. Deferred candidates have fate `deferred`, not `event_unresolved`. `summaries` includes all selected snapshots, including budget exclusions; presence in this file does not establish delivery. Unknown source/provider/time/member provenance remains blank.

An actual selected/stored queue snapshot and its lifecycle take precedence over a rejected current same-key version. Pending rows do not borrow current-only extraction provider, source/time or pre-LLM triage metadata, even when a current cluster has the same key. Current collection/extraction failures still appear in coverage warnings and run diagnostics; they do not relabel a queued winner as unavailable.

Both detailed CSVs expose:

- `composition_state`: `full`, `headline_only`, `budget_excluded`, `deferred`, `expired`, `rendered_legacy`, or `not_selected`.
- `delivery_state`: `acknowledged`, `failed_or_unknown`, `mocked`, `dry_run`, or `not_attempted`.
- `origin`: `fresh`, `pending`, or `unknown`.

`run` adds selected/rendered/delivered_events and page outcome counts. **Only real receipt fields establish delivery.** For backward compatibility, legacy fate `sent`/`sent_followup` and run `sent` describe composition. Legacy `deliver` counts successful mock or real messages and may include lead sends.

## Acceptance evidence

See `analysis/SESSION_26_PROGRESS.md` for baseline, regression red/green outputs, full-suite reconciliation and independent-review status. Tests use real SQLite and mocked Telegram, never live sends or downloaded models.
