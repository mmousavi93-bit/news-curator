# Phase 7 Brief — Actions (collect wiring, composer, delivery, workflows)

> **STATUS: BUILT and gate-green 2026-08-29 (suite 429). The full pipe runs as
> `python -m agent.run --db state.db`. Gate (3 consecutive unattended runs) is
> owner-clock-bound -- RUNBOOK.md is the sequence. Two workflow traps caught in
> build: `git add -f` needed on the state branch (the repo's own *.age gitignore
> would swallow the add), and the digest cron is detected via
> `github.event.schedule` (false on dispatch). Details in POSTMORTEMS.md.**

Architect brief, written 2026-08-29 after Phase 6 closed green (suite 415). An
Implementer runs from this file plus `CLAUDE.md` and `ARCHITECTURE.md`.

---

## Why this phase is seventh

Phases 1–6 built every component of the pipeline and wired half of it. What is
still missing before the thing runs unattended: the collect stage (the pipe
currently starts at filter with an empty ctx.items), the composer (events
exist but nothing turns them into a Telegram message), the deliver stage
(Phase 2's client exists but nothing calls it from the pipeline), and the two
workflows that schedule, decrypt, run, re-encrypt and push state.

Gate: **three consecutive unattended runs** — which is a clock-bound owner
gate. This phase's deliverable is everything the gate needs; the gate itself
runs after the owner's secrets land.

---

## Deliverable

`python -m agent.run --db state.db` runs the whole pipe: collect → filter →
embed → cluster → understand → compose → deliver, with state persisted and a
message sent. The workflows run it every 3 hours plus a 07:00 Tehran digest,
on age-decrypted state, re-encrypting and force-pushing after.

### Files (all under ~200 lines)

```
src/agent/pipeline/collect.py    CollectStage: fetch -> dedup -> store -> ctx.items
src/agent/pipeline/compose.py    ComposeStage: events -> Message (char-budgeted)
src/agent/pipeline/deliver.py    DeliverStage: Message -> Telegram (or mock)
.github/workflows/pipeline.yml   both crons, decrypt, run, encrypt, state push, backup
tests/unit/test_pipeline_{collect,compose,deliver}.py
```

Edits: `pipeline/__init__.py` (wire the three real stages + digest flag),
`run.py` (open ctx.db for full runs, read NEWS_CURATOR_DIGEST), `ci.yml`
(nothing — workflows are not unit-tested, the runbook is).

---

## Requirements

### 1. CollectStage: fetch, dedup, store, hand forward — never silently skip storing

Runs `registry.collect_all` (Phase 3, gate-green), stores what survives
dedup via `memory/dedup.store_new`, prunes per retention, sets
`ctx.items = stored.new`, counters `collect`. The collect_fn is injectable
(tests inject a stub; production default is registry.collect_all).

- `ctx.db` is None AND (dry_run or mock_mode): skip the stage entirely with
  one log line. Dry runs and mock runs are offline by definition.
- `ctx.db` is None and NOT dry_run: raise. A real run without a database is
  a silent no-store — the exact failure constraint 14 exists to prevent.
  (The workflow decrypts first; if that failed, run.py already exited 1.)
- Per-source failures are recorded by registry (Phase 3 contract) and never
  crash the run.

### 2. ComposeStage: events become ONE budgeted message, or an honest one-liner

Tone contract lives in the message text this stage builds — calm, factual,
no drama (CLAUDE.md). Composer decisions:

- **No events → one short honest line**: "Nothing new since the last run."
  (constraint 11). The message still sends — the owner chose per-run
  messages over threshold-only (session-3 decision).
- **Each event → one Item**: headline = event headline/summary first line;
  `priority` lower = more important (delivery/message.py convention):
  source tier (from credibility, via the cluster) then recency. `detail` =
  summary, truncated. No URLs (clusters have member urls; the message is a
  digest, not a link farm — v1 decision, revisit if the owner asks).
- **Dates are Tehran wall-clock, and date_only is respected**: if EVERY
  member of the cluster is date_only, print the date with "time not stated"
  — never 03:30 Tehran invented from midnight UTC (constraints 10, 11; the
  pending composer consumer, now built). `collectors/tz.to_tehran` only.
- **Header**: alert framing is the scorer's job (v1.5); v1 header carries
  the run date (Tehran) and, on the 07:00 run, the "daily digest" marker
  (ctx.daily_digest, set from NEWS_CURATOR_DIGEST env — no new CLI flag,
  run.py stays at the cap).
- **Budgeting**: build the Message, then `formatter.format_single` +
  `budget.fit_single` — Phase 2's layers already own truncation priority
  and the 4,096-cap. The composer never hand-truncates.

### 3. DeliverStage: send, or say why not — never crash

`TelegramClient.from_env(os.environ).send(text)` (Phase 2, gate-green):
mock path when credentials absent, `SendResult` on failure, no exception
escapes. dry_run logs "would have sent" with the text, sends nothing.
Counter: `deliver` = 1 on send, 0 otherwise. The summary line then reads
honestly on every path.

### 4. run.py: the full run needs --db, and nothing else changes

`python -m agent.run --db state.db` (no other flags): open the db with
`create_if_absent=False` + `assert_halt_flags` (constraint 14: an absent db
halts, it never starts fresh), attach to ctx, run the pipe. `--init-db` is
allowed to create (owner bootstrap) — the workflow NEVER passes it.
`NEWS_CURATOR_DIGEST=true` env sets ctx.daily_digest. run.py stays ≤200
lines; trim comments before adding.

### 5. Workflows: one file, both schedules, state branch is a side effect

`pipeline.yml` (public repo → unlimited minutes, cron auto-disables after
60 days of repo inactivity — the runbook says to touch the repo):

- `schedule: 0 */3 * * *` and `0 7 * * *` (Asia/Tehran times, stated in
  comments — GitHub cron is UTC, 07:00 Tehran = 03:30 UTC; the pipeline
  schedule 0 */3 UTC is close enough to every-3h Tehran and keeps cron
  simple; the DIGEST entry is `30 3 * * *`).
- Steps: checkout → setup-python → pip install `.[embeddings]` →
  `age -d -i $AGE_SECRET_KEY_FILE state.db.age` (write the key to a 0600
  temp file, never argv/env in logs; on failure: exit 1 loudly) →
  `python -m agent.run --db state.db` → on success: `age -r $AGE_PUBLIC_KEY
  -o state.db.age.new` + mv → commit `state.db.age` to the orphan `state`
  branch and force-push → upload-artifact `state.db.age` (90-day backup).
  The digest cron additionally sets NEWS_CURATOR_DIGEST=true.
- Secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, GEMINI_API_KEY,
  GROQ_API_KEY, OPENROUTER_API_KEY (optional), AGE_SECRET_KEY,
  AGE_PUBLIC_KEY. All `env:`-level, never echoed.
- `permissions: contents: write` for the state push. Push uses the
  checkout's remote; failure of the push logs but does not fail the job
  (a failed push loses a backup, not the run — next run re-pushes; a
  failed ENCRYPT does fail the job: losing state is the worst outcome).

### 6. Mock discipline carries over

Every stage reads its inputs from ctx and writes to ctx; no stage imports
another stage. Tests build ctx directly (Phase 6 pattern). The offline
suite: no requests, no sentence-transformers, no keys — collect skips in
mock mode, deliver takes the mock path, compose is pure.

---

## Gate

1. Full-pipe smoke (mock): build_stages + a tmp db + a stub collect_fn
   returning N items + FakeEmbedder + stub router → run_pipeline →
   ctx.events non-empty, message composed, `deliver` counter set, summary
   line has items/clusters/messages.
2. Collect: stub report of 5 items, 3 dedup-new → ctx.items has 3, stored
   rows exist, per-source dup counts logged; dry_run skips with one line;
   missing db raises.
3. Compose: empty events → the honest one-liner; events → Items ordered by
   priority; all-date_only cluster renders "time not stated"; digest flag
   changes the header; message ≤ 4,096 UTF-16 units (budget.py already
   gate-tested; assert the composed message fits).
4. Deliver: no credentials → mock path, no exception; dry_run → nothing
   sent; failure → SendResult logged, run continues.
5. Offline: suite green with requests AND sentence_transformers
   unimportable; `--dry-run` still the one summary line.
6. Full suite green. Baseline **415**. State the new expected number before
   running, reconcile.
7. Grep gates: no `datetime.now`/`time.time` in pipeline/; no prompt
   literals; no secret names in any log format string (AGE_*/TOKEN names
   appear in env var handling only, values never).

---

## Out of scope — do not build

- Widen sources / signals_covered / source-health. Phase 8.
- Validate stage (RUMOUR labels, confirmation counting, lead ladder).
  Phase 9. `claim_status` stays 'unconfirmed'.
- Risk engine, alert tiers, market fetcher. v1.5 (Phase 11).
- Digest layout differences beyond the header marker. v1 is one message
  shape; tiered layout comes with scoring.
- X/Twitter. Deferred to v2 (CLAUDE.md).
- The settings-editing bot. v2.
