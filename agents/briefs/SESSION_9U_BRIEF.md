# Session 9u — Telegram feedback loop (read-only)

**Status:** built, green (841 tests), awaiting push + live confirmation.
**Priorities:** user reprioritized to the feedback loop; 9t (clusterer) is deferred.

## Goal

Close the loop on output quality. The bot currently only *sends* digests
(`delivery/telegram.py`, `sendMessage`). This session adds the *receive* side so
the owner's feedback becomes data: reactions (👍/👎/…) on digest messages, and
DMs sent directly to the bot. Read-only — the harvested CSV is read by hand
when tuning; nothing here changes a scoring/clustering/pipeline threshold.

## The constraint that shaped everything

`getUpdates` is unreachable from Iran (POSTMORTEMS, line 1623). The owner's
machine cannot poll it, and neither can a local agent. But the GitHub Actions
runner is outside Iran and already holds `TELEGRAM_BOT_TOKEN`. So feedback is
harvested **server-side, once per pipeline run**, not from the owner's PC.

## Design

- **Transport reuse, no new method:** `getUpdates` is POSTed through the
  existing `Transport` (`delivery/transport.py`) — the Bot API accepts POST
  for every method, so the send-only transport abstraction is untouched.
  No Telethon/MTProto (constraint 6). `timeout=0` = short poll (return pending
  now), because this runs on a CI runner, not as a long-lived listener.
- **What's parsed** (`delivery/feedback.py`):
  - `message_reaction` updates → `FeedbackRecord(kind="reaction")` with the
    digest `message_id`, channel id/title, user, and comma-joined emojis from
    `new_reaction` (empty = reaction removed). Emoji types only; `custom_emoji`/
    `paid` are skipped rather than half-decoded.
  - `message` updates → `kind="dm"` **only when `chat.type == "private"`**.
    Group/channel messages and `channel_post` are ignored (not the owner
    talking to the bot).
  - Everything else (`edited_message`, `callback_query`, …) is dropped.
  - The max `update_id` is computed over **raw** updates, so an unparseable
    update still advances the offset and never re-fetches forever.
- **Offset persistence** (`feedback.py`): the last `update_id` lives in the
  state DB's existing `meta` KV table under `feedback_last_update_id`.
  No `SCHEMA_VERSION` bump — one integer does not justify a migration.
  Autocommit (`isolation_level=None` in `memory/db.py`), so no explicit commit.
- **Output:** `feedback_<ts>.csv` in `NEWS_CURATOR_REPORT_DIR`, same
  `%Y%m%dT%H%M%SZ` timestamp and `utf-8-sig` as the other observability CSVs.
  Columns: `run_at_utc, update_id, kind, chat_id, chat_title, user_id,
  user_name, message_id, reactions, text, date_utc`.
- **Never breaks the run:** `FeedbackClient.fetch` never raises (returns
  `([], offset)` on transport error / non-200 / `ok:false`). The workflow step
  is `if: always()` + `continue-on-error: true`, placed after "Run pipeline"
  and before "Encrypt state" so the offset is persisted into the encrypted
  state blob. Missing state DB → harvest still runs, offset not persisted
  (updates may re-read until state returns; harmless for read-only data).

## Files

- `src/agent/delivery/feedback.py` — `FeedbackRecord`, parsers, `FeedbackClient`.
- `src/agent/feedback.py` — `main()` entry (`python -m agent.feedback --db state.db`),
  `harvest()`, offset load/save, CSV writer.
- `.github/workflows/pipeline.yml` — "Harvest feedback" step.
- `tests/unit/test_delivery_feedback.py` (14) + `tests/unit/test_feedback.py` (12).

## Known limits (deliberate, next sessions)

1. **Reactions are message-level, not entry-level.** A digest message carries
   several stories; a 👎 means "this digest was off", not "this story was off".
   Per-entry granularity needs the bot to capture `sendMessage` → `message_id`
   and map entries to messages (not done here).
2. **Not real-time.** Harvested every 3-hour run. Real-time needs an always-on
   host outside Iran (webhook worker or a long-poll daemon) — a bigger lift.
3. **Reaction delivery needs live confirmation.** `message_reaction` updates
   for a bot's own channel posts are documented Bot API behavior, but this is
   the first live exercise of it. DMs are a guaranteed fallback path (private
   messages to the bot always arrive via `getUpdates`).

## Live-run acceptance

After the next run: `feedback_<ts>.csv` appears in the run-reports artifact
once the owner reacts/DMs; the "Harvest feedback" step logs
`harvested N record(s)`; the digest still posts (feedback must not disturb it).
