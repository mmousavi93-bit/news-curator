# Phase 2 Brief — Telegram delivery

**Role:** Implementer. **Written by:** Architect, 2026-08-12.
**Prerequisite:** Phase 1 complete and gate-green (41 passed, verified on owner's machine).

Read `CLAUDE.md` first. Its hard constraints outrank everything in this brief. If this brief
contradicts a hard constraint, stop and say so — do not resolve it yourself.

---

## Why this phase is second, not third

The original plan built 40 collectors before proving a message could reach the phone. That
front-loads the least reversible work behind the most uncertain question. This phase answers
"does anything actually arrive?" while the answer is still cheap to act on.

**No collectors in this phase.** No RSS, no `t.me/s/` reading, no LLM calls, no storage. The
input to this phase is a hand-built message object in a test. The output is bytes on the wire.

---

## Deliverable

A delivery package that takes a structured message and puts it in a private Telegram channel,
correctly, within Telegram's limits, without ever leaking a credential.

### Files (all under 200 lines — split rather than exceed)

| File | Job |
|---|---|
| `src/agent/delivery/message.py` | The message data model. Dataclasses only, no I/O. |
| `src/agent/delivery/formatter.py` | Message object → Telegram-ready string(s). |
| `src/agent/delivery/budget.py` | The 4,096-char budgeting and priority truncation logic. |
| `src/agent/delivery/telegram.py` | The HTTP client: send, rate limit, retry, mock transport. |
| `tests/unit/test_formatter.py`, `test_budget.py`, `test_telegram.py` | |
| `tests/integration/test_delivery_mock.py` | End-to-end through the mock transport. |

If `budget.py` is trivial enough to live inside `formatter.py` without pushing it near 200
lines, merge them and say so in your report. Do not create a file to satisfy a table.

---

## Requirements

### 1. Configuration and secrets

- Two env vars, and they are the **only** way credentials enter the process:
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`.
- Neither may appear as a literal in any file, fixture, test, log line or error message.
  The repo is public. Register both with the Phase 1 redaction filter at startup.
- `TELEGRAM_CHANNEL_ID` is a channel ID (typically a negative integer like `-1001234567890`)
  or an `@channelusername`. Accept both. Validate the shape and fail loudly on neither.
- Absent env vars must **not** crash import or the dry-run. Absent credentials = mock mode.

### 2. Parse mode — use HTML, not MarkdownV2

This is a decision, not a preference. Telegram's MarkdownV2 requires escaping eighteen
characters including `.`, `-`, `!`, `(`, `)`. News headlines are full of all of them. One
missed escape is a `400 Bad Request` at 3am on an unattended run, and the failure mode is a
message that never arrives rather than one that looks wrong.

Use `parse_mode=HTML` with the four supported tags only: `<b>`, `<i>`, `<a href="">`, `<code>`.
Escape `&`, `<`, `>` in all interpolated text — headline text, source names, everything that
did not originate in the formatter. Test this with a headline containing `<script>`, `&amp;`,
and a bare `<`.

### 3. The 4,096-character budget

Telegram hard-caps a message at 4,096 characters. **The formatter must budget before sending,
never discover the limit at send time.**

- Length is counted in **UTF-16 code units**, not Python characters, because that is what
  Telegram counts. Persian, Arabic and Hebrew text and any emoji make these diverge. Get this
  wrong and the message truncates mid-entity and the send fails. Implement a helper and test
  it against a Persian string.
- HTML tags count toward the limit. Budget the rendered string, not the plain text.
- When content exceeds budget, truncate **by priority**, not by tail-chopping:
  1. Never drop the header (alert level / timestamp / run marker).
  2. Drop lowest-priority items first — the message model carries an explicit priority.
  3. Within a retained item, the headline survives and the detail line is dropped before the
     item itself is dropped.
  4. Always append an honest overflow marker, e.g. `… +7 more`. Never silently discard.
- Splitting across multiple messages is permitted and preferred over aggressive truncation
  when the caller asks for it. Cap the split at a configurable maximum (default 3) so a
  runaway run cannot post forty messages. Never split mid-HTML-tag.

### 4. Sending, rate limits and retries

- HTTP via `requests` (already a Phase 1-era dependency in the sandbox; declare it in
  `pyproject.toml` with the one-line justification if not already there). No `python-telegram-bot`,
  no async framework, no Telethon/MTProto — constraint 6.
- Telegram allows 1 message/second per chat. Enforce a client-side minimum interval between
  sends. Do not rely on the server to tell you.
- Retry policy, exponential backoff with jitter, max 4 attempts:
  - `429` → honour the `retry_after` field in the response body. It is authoritative. Sleep
    that long, do not use your own backoff for this case.
  - `5xx` and connection/timeout errors → back off and retry.
  - `4xx` other than 429 → **do not retry**. A 400 will be a 400 forever; retrying wastes the
    run and can look like abuse. Log the error code and the Telegram `description` field, and
    return failure.
- A delivery failure must never crash the pipeline run. Return a result object the caller can
  act on. A run that collected news and failed to send is still a run that must exit cleanly
  and record what happened.
- Set an explicit connect and read timeout on every request. An unattended job with no timeout
  is an unattended job that hangs until GitHub kills it at 6 hours.

### 5. Mock mode — mandatory

- The transport is injectable. The default transport in tests is a mock that records calls and
  returns canned responses; it must be able to simulate 429-with-retry_after, 500, 400, and a
  connection timeout.
- `python -m agent.run --dry-run` must still exit 0 with its single summary line, with zero
  env vars and zero network. Add a `--send-test` flag that builds a fixed sample message and
  sends it — through the mock when credentials are absent, for real when they are present.
  That flag is how the owner will verify the live path in one command.

---

## Gate

All must hold before this phase is called done:

1. `python -m pytest -q` green, with the new tests, run by the owner on Windows.
2. `python -m agent.run --dry-run` unchanged: exit 0, one line, no env vars, no network.
3. `python -m agent.run --send-test` with no credentials set: exits 0, sends nothing real,
   prints what it *would* have sent.
4. A test proves a 4,200-character message is either truncated to ≤4,096 UTF-16 units with an
   overflow marker, or split — and in both cases no HTML tag is broken.
5. A test proves a `429` with `retry_after: 2` is honoured and the send eventually succeeds.
6. A test proves a `400` is **not** retried.
7. A test proves the bot token never appears in any log record emitted during a failed send,
   including the exception path.
8. No file over 200 lines.

## Out of scope — do not build

Collectors, storage, dedup, clustering, LLM calls, the digest schedule, the settings bot,
inline keyboards, or reading messages back from Telegram. v1 delivery is write-only.
