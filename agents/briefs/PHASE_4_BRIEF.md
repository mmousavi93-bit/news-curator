# Phase 4 Brief — Storage

Architect brief, written 2026-08-19 after Phase 3 closed green. An Implementer runs only
from this file plus `CLAUDE.md` and `ARCHITECTURE.md`. Do not read `POSTMORTEMS.md` unless
a requirement below points you at it.

Model routing: Sonnet Implementer, fresh context. Verifier must be a different agent than
the Implementer.

---

## Why this phase is fourth

Phase 3 produces ~184 items per run and throws every one of them away when the process
exits. Until state persists, the pipeline cannot answer the only question the owner
actually asked it to answer — *is this new?* Deduplication is the mechanism behind "not
overwhelming"; storage is the mechanism behind deduplication.

This phase is also the first that can lose data rather than merely fail. Everything before
it was idempotent. From here on, a bug can destroy accumulated memory, which is why
constraint 14 is the spine of this brief.

---

## Deliverable

`python -m agent.run --collect-only` continues to work unchanged, plus a new path that
persists what it collected, and a second run of the same input sends nothing new.

### Files (all under ~200 lines — split rather than exceed)

```
src/agent/memory/schema.sql      DDL only, no logic
src/agent/memory/db.py           connection, migration check, integrity check, halt-on-fail
src/agent/memory/models.py       row <-> dataclass mapping
src/agent/memory/dedup.py        the three cheap layers
src/agent/memory/retention.py    clock-injected pruning
src/agent/memory/crypto.py       age encrypt/decrypt of the db file
tests/unit/test_memory_*.py      offline, no keys, no network
```

Constraint 12 is real and was breached this week — `src/agent/collectors/dates.py` reached
211 lines and is an open owner decision. Do not add a seventh file to that list.

---

## Requirements

### 1. Constraint 14 is the whole phase. Failure must halt, never reset.

If the database cannot be decrypted, fails an integrity check, or reports a schema version
this code does not understand, the process **exits non-zero and alerts**. It must not
create a fresh database, must not fall back to empty state, and must not "repair" anything.

Starting from empty memory is a worse outcome than crashing, because a crash is visible and
an empty memory is not: the agent would simply re-send weeks of already-seen stories and
look like it was working.

This is the single most likely place for a well-meaning implementer to add a `try/except`
that does the wrong thing. There must be a test that corrupts the database file and asserts
the process exits non-zero **and leaves no new database behind**.

`PRAGMA integrity_check` on open. Schema version in a one-row `meta` table, compared against
a constant in `db.py`.

### 2. `date_only` must round-trip, or a defect from this week silently returns

`Item.date_only` was added 2026-08-19. `state_dept_travel` emits date-only RFC-822, so its
`published_at` is midnight UTC as a **placeholder, not an observation**. SQLite has no
boolean — store INTEGER 0/1 and map it back explicitly.

If the schema drops this column the information is unrecoverable: once the raw string is
gone, `00:00:00Z` is indistinguishable from a real midnight publication, and the composer
will eventually print an invented "03:30 Tehran" as fact. Test that it survives a write/read
cycle in both states.

### 3. Store UTC. Always. `to_tehran` must not appear anywhere under `memory/`.

`dates.to_tehran()` exists and is display-only. Converting at storage time makes dedupe
windows, retention boundaries and trend deltas depend on a display preference — that is how
a timezone bug becomes a data bug. Grep your own diff for `tehran` before committing.

### 4. Dedup: build layers 1–3 only. Layer 4 is Phase 6 and needs embeddings.

Cheapest first, each a plain indexed SQLite lookup:

1. exact URL hash
2. normalised URL hash — strip tracking params
3. normalised title hash — casefold, collapse whitespace, strip punctuation

**The trap is layer 2, and it is live in your source list.** `reuters_gnews` and `ap_gnews`
reach Reuters and AP through a Google News `site:` proxy, so their URLs carry the real
target inside a query parameter. Strip parameters naively and every Google News item
normalises to the same URL and you dedupe the entire feed down to one story. Strip nothing
and the same article under two tracking suffixes counts twice.

Required behaviour: strip a **known allow-list** of tracking keys (`utm_*`, `fbclid`,
`gclid`, `ref`, `ref_src`, `at_medium`, `at_campaign`), lowercase the host, drop a trailing
slash and any fragment, and **preserve every other parameter**. Never strip by wildcard.
Test it against a real Google News style URL and assert two different targets stay different.

Do not invent a fourth layer. Do not import NumPy in this phase.

### 5. Create the scoring tables. Write no logic against them.

Session-3 decision 1 makes v1 a curator, not a scorer — `STRAT`/`TACT`/`MSTRESS` are not
built. But `CLAUDE.md` requires the schema stay wired so Phases 7–8 bolt on with no rework.

So `schema.sql` **creates** `signal_events`, `speaker_statements`, `risk_history`,
`scheduled_events`, `market_metrics` and `source_health` with their retention columns, and
**no Python touches them this phase** beyond retention pruning. Row shapes and retention
windows are in `ARCHITECTURE.md` §11 and `config/settings.yaml` `retention:`.

Both failure modes here are real: skip the tables and Phase 7 needs a migration on
encrypted state; write logic against them and you have built the scorer the owner deferred.

### 6. Retention is clock-injected, never `datetime.now()` inside the pruner

Windows differ per table and are already decided in `settings.yaml`: `url_hashes_days: 7`,
`events_days: 30`, `embeddings_days: 30` (must equal `events_days`), `signal_events_days:
180`, `speaker_statements_days: 45`, `score_history_days: 365`, `scheduled_events_days: 365`.

Do not hardcode any of them. Read them from settings, and pass `now` in as a parameter so a
test can assert the boundary at 6 days versus 8 days without sleeping. A pruner that reads
the wall clock internally cannot be tested and will be trusted anyway.

Assert `embeddings_days == events_days` at load; an orphaned embedding with no event is a
silent corruption.

### 7. `crypto.py` does encrypt/decrypt only. No git.

`age` encrypt the db file to a recipient, decrypt with a key from an env var. The state
branch, force-push, artifact backup and retry-on-conflict are **Phase 7**. Building them
here means building them before the workflow that uses them exists.

Key comes from an environment variable and never appears in a log line — route everything
through the redaction filter in `util/logging.py` (constraint 9, public repo).

Mock mode: if `age` is unavailable, tests must still run. Stub the binary call.

### 8. Offline, keyless, deterministic

Every test runs with no network, no `age` binary required, no secrets. Same input twice
produces the same database bytes modulo timestamps you inject. Use a temp directory, never
a fixture db committed to the repo.

---

## Gate

The Implementer does not decide when this is done. All of the following, asserted by tests
in CI, not by inspection:

1. **Restart survival.** Write N items, close, reopen, read N items back with identical
   field values including `date_only` and `published_at` tzinfo.
2. **The same story twice is sent once.** Feed the same collected batch twice; second pass
   yields zero new items. Then feed it with a tracking parameter appended and assert it
   still yields zero.
3. **Two different Google-News-proxied targets stay distinct.** The layer-2 trap, tested
   explicitly.
4. **Corruption halts.** Truncate/scribble the db file; process exits non-zero, emits an
   alert, and **no new database file exists afterwards**.
5. **Unknown schema version halts.** Same assertion, different cause.
6. **Retention prunes at the boundary**, per table, with an injected clock — 6 days kept,
   8 days gone for a 7-day window.
7. **`date_only` round-trips** in both states.
8. **No `tehran` and no `datetime.now()`** anywhere under `src/agent/memory/`. Assert by
   grep in the test suite; this is cheap and it is the kind of thing that creeps back.
9. Full suite green on the owner's Windows. Current baseline **183** — state the new
   expected number in the PR description before running it, then reconcile.

A green suite that the Implementer wrote is not the gate. The Verifier must attack items 4
and 5 specifically, because they are the ones that pass by doing nothing.

---

## Out of scope — do not build

- Embeddings, clustering, cosine similarity, NumPy. Phase 6.
- Any risk score, any signal extraction, any LLM call. Phases 5–9.
- Git state branch, force-push, backups, workflow changes. Phase 7.
- New sources, `sources.yaml` edits, probe rounds. Phase 8.
- The composer's `date_only` rendering. Phase 6 — but do not break the flag.
- Fixing gate condition 4 (`all_timestamps_identical`) in `collect-test.yml`. Known open
  item, owner decision, unrelated to storage.

---

## Open owner decisions that touch this phase

Neither blocks the build; both are recorded so the Implementer does not "fix" them.

- `src/agent/collectors/dates.py` is 211 lines, over the constraint-12 cap. A split into
  `tz.py` is proposed. **Do not perform it as a drive-by** — it touches gate-green Phase 3
  code.
- Gate condition 4 will eventually false-positive now that date-only items resolve to a
  shared midnight. Flagged, not fixed.
