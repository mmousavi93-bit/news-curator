# POSTMORTEMS.md — News Curator build history

Split out of CLAUDE.md 2026-08-19. CLAUDE.md loads into context on every turn of every
session; this file does not. Nothing here is deleted or condensed — it is the forensic
record of what broke, why, and what rule came out of it. Read it when working on the
phase or subsystem it covers. CLAUDE.md keeps the operational core and points here.

## 2026-08-30 (session 9b) — owner's local run reviewed end-to-end; 4 fixes shipped

Owner ran `python -m agent.run --db state.db` manually (13:26 Tehran) and pasted log +
rendered digest + the 4 observability CSVs. Second cascade of the day: Gemini 503 from
call #1, breaker open after 2 failures (shipped threshold 2, working as designed);
Groq carried exactly 13 calls then 429x2 — the ~13-call ceiling confirmed a second
time, independent of the morning run. 27 of 40 clusters skipped `unavailable`. Note:
today's Gemini failures are fast 503s, not hangs — the session-9 20s read timeout
would not have helped today; it only shortens the dead-primary path for hangs.

Four real defects found in the CSVs + digest, all fixed, suite 528 -> 532 (predicted
532 before running):

1. **9-day-old item delivered as news (Lebanon advisory, Aug 21 -> Aug 30).** Root
   cause: `seen_urls` hashes prune at `url_hashes_days=7`, but the State Dept feed
   lists advisories for weeks — so every date-only item loops back into the digest
   every ~7 days forever. Fix: collect drops date-only items older than
   `date_only_max_age_hours` (72h, owner-editable) BEFORE dedup — a date-only item
   that old is by construction a re-surface, never news. Handles naive datetimes
   without crashing. Settings schema + fixture + 2 tests.
2. **summaries.csv `rank` column meant nothing.** The writer numbered events in
   creation order while the digest delivers score-desc — in this run rank 0 was NOT
   the first message item. Fix: shared `event_order_key` in rank.py, writer sorts
   with it; rank 0 now IS the reader's first line. +1 test.
3. **Two relevance misses in one digest** (Groq/qwen was the only understand model
   all day): a police-blotter crime story and a "leaders face domestic political
   wars" commentary piece. The prompt already said commentary should drop — the
   model ignored it. Prompt hardened (understand.txt, contract unchanged): no-new-
   event clusters are `irrelevant` explicitly; routine crime is `irrelevant` unless
   terror/organised-violence/weapons; "when unsure between keeping and dropping,
   drop — a digest earns trust by omission."
4. **54 identical "skipping (circuit breaker open)" log lines** — one per cluster
   per provider. Router now logs the skip once per provider per run. +1 test.

Standing observations, no code: the 0.62 clustering threshold fragmented 67 items
into 51 clusters (1.31 items/cluster; 11 dropped by the cap) — on a cascade day that
fragmentation directly becomes lost coverage. The threshold tuning session now has
its evidence. And with 2 cascade days in a row, the OpenRouter parachute key is no
longer optional bookkeeping — 50 req/day ≈ 1 run of third-rung capacity.


## 2026-08-30 (batch 2) — Persian gate + delivered flag + lead fix, adversarially reviewed

Built from the owner's live-sample review (first real digest after the self-match fix):
3 items, one fully Arabic (LLM mirrored the Arabic source), one scenario-analysis piece
(commentary, not an event), one clean. Owner approved three improvements plus a deep
scan. Suite 489 -> 516, shim-verified, count predicted before each run.

What shipped:
1. `pipeline/langgate.py` (NEW): deterministic Persian output gate, zero LLM calls.
   Arabic-only codepoints ة ى ي ك إ (NOT أ -- see review) and the Hebrew block drop an
   event from the MESSAGE only; events stay stored. New honest one-liner
   `lang_dropped` when all events drop; counter `compose_lang_drops` for log visibility.
2. `delivered` table (SCHEMA_VERSION 3, additive CREATE-IF-NOT-EXISTS, live DB gains it
   on first open): the anti-repetition window now matches events the owner actually
   RECEIVED. Never-seen events (below min_score, repeats, gate-dropped) no longer
   suppress their own follow-ups -- the residual wrinkle from the self-match fix is
   CLOSED, and the ~2 days of ghost events in the live DB stop blocking immediately.
   Markers are written by DELIVER only after real sends succeed; leads never marked.
3. `understand.txt`: output-language rule hardened (never mirror the source, banned
   letters, «علی الرغم» -> «علیرغم» example) and clickbait extended to drop pure
   commentary/analysis/scenario speculation.
4. Found while building: a lead-only run never built the lead message (compose's early
   return skipped it) -- leads are now built FIRST, pinned by name in a test.

Adversarial review (fresh-context Pro agent, read-only): ACCEPT WITH FIXES. The catches,
all fixed before shipping:
- **أ U+0623 was wrongly a marker.** Persian productively uses it (تأیید، تأثیر،
  مأموریت). Dropping events on it would have silently lost exactly the stories the
  digest exists for. Removed from the gate and the prompt; pinned by test.
- **One-script-per-key upgrade stamped v1 DBs to 3 without creating `delivered`**, and
  the lied-about meta row blocked every retry. Now: stepwise loop applies every script;
  version > SCHEMA_VERSION halts (that halt was briefly broken by the first rewrite --
  the suite caught it; the guard is back and tested).
- **Mark-before-send would re-create ghost suppression on send failure** (send failure
  is non-fatal by contract). Markers moved to the deliver stage: all-real-sends-ok only.
  Bonus: dry-run and the no-credentials mock path never mark, so local `--db` runs stay
  clean.

Accepted edges, documented in CLAUDE.md Pending: first run after deploy has no markers
for the last 72h (one near-duplicate may re-surface, self-correcting by run 2);
format_split truncation over-marks the lowest-priority items for <=72h.

Standing lessons:
(a) The fresh-context reviewer caught a factual claim of mine (أ is Arabic-only) that I
    had asserted from pattern-matching instead of checking. Orthography claims need a
    native-speaker check, not a codepoint-table glance.
(b) A schema-version loop needs TWO halts: unknown-future (version > current) AND
    missing-step (no script for a step). Writing the first while forgetting the second
    is what the suite is for -- the unknown-version test caught it within one run.
(c) "Mark what was received" means mark AFTER the send, not after the compose -- the
    same defect as the original ghost-suppression, one stage later.

## 2026-08-30 — first automated run: provider cascade, degradation held, CSVs live

Owner pasted the first post-push cron log (12:21 Tehran). Three verifications at once:
the self-match fix held (9 events → real 2,351-char message), the Persian gate caught
its first live drop (`a5220106`), and all 4 observability CSVs were written and
uploaded. The new code executing in production IS the content check for the push.

The incident: Gemini failed call #1 with a 60s timeout, got a 503 on #3, and the
breaker opened after 2 consecutive failures (shipped setting) -- Gemini skipped for
the run. Groq (qwen/qwen3.8-27b) carried calls #2 and #4-#14, then 429'd twice and
its breaker opened too. Both providers dead → 26 of 40 clusters skipped
`status=unavailable`. 10 events from 40 clusters, one message sent.

Load-bearing finding: **Groq free ≈ 13 calls/run before 429.** The pacer ran at
Groq's 30 RPM (2s gaps, visible in log timestamps), so this was not a pacing bug --
it is the free tier's token-per-minute limit (~10K tokens in ~30s). Consequence: on a
Gemini-outage day every run degrades to ~1/3 cluster capacity, permanently, on free
tiers. OpenRouter free (50/day) was already rejected as a third provider.

Decisions finalized 2026-08-30 (owner's instruction: "finalize decisions based on
understanding"). Suite 522 → 528, shim-verified.

1. **Primary read timeout 60s → 20s, implemented as config.** The 60s default cost a
   dead Gemini 60s per attempt; with max_retries=3 and the shipped breaker threshold
   of 2, the failover path could burn ~8 min of an ~8-min run before Groq got the
   wheel (the 13-min figure above assumed the old 5-failure breaker). At 20s the same
   path is ~160s, and a merely-slow Gemini still survives. Mechanics: new
   `read_timeout_seconds` key on any `providers:` entry (int, negative/str/bool
   rejected); `providers.py` keeps DEFAULT_TIMEOUT (10, 60) as the fallback default;
   wiring maps overrides to `timeout_by_provider`; the timeout now rides on
   `Provider.timeout` and MockHttpTransport records it, so the tests assert the REAL
   value handed to requests. Tests: 3 validation in test_settings_schema_llm.py +
   3 flow tests in new `tests/unit/test_llm_timeout.py` (settings → wiring → router →
   call → transport). Connect timeout stays 10s everywhere.
2. **Groq's ~13-call free ceiling ACCEPTED as the degradation floor.** Not a pacing
   bug -- 30 RPM pacing was correct; it is the free tier's token-per-minute wall. On a
   Gemini-outage day each run covers its priority-ordered top ~14 of 40 clusters, and
   because cluster.py ranks by tier → recency → size, the stories that survive are
   the right ones. Documented next to groq's rpm/rpd in settings.yaml ("a 429 here is
   NOT a pacing bug"). OpenRouter stays a last-rung parachute (50 req/day ≈ 1 run)
   and is not counted as capacity. Adding a real third provider is a v1.5 decision
   taken with failure-rate data, not a knee-jerk patch after one bad day.

Tuning note: that run's CSVs are polluted for analysis (26 unavailable clusters) --
the analysis session uses a clean run only.

Provider-addition question (DeepSeek V4-Flash / V4-Pro), researched 2026-08-30 with
parallel research agents. Full dossier: `analysis/deepseek_v4_eval.md`. Verdict:
REJECTED for now, on three independent findings.
(1) Cost: DeepSeek repriced 2026-08-17 to peak/off-peak (peak = 2x; the repo's
Aug-1 rate card is superseded). V4-Flash wholesale ~$4.4/mo, V4-Pro ~$13/mo;
cascade position (3rd rung, after Groq's 13 free calls) ~$0.09/outage-day for
Flash, and that usage fits under the 5M-free-token allotment -- so cost is not the
blocker. (2) Quality: family-level disqualifiers for THIS task -- topic-selective
censorship on war/geopolitics (a softened military development is a silent
intelligence failure, constraint 10), documented JSON-discipline defects (this
pipeline has zero retry budget), Chinese-first behavior on Persian prompts, RTL
issues. V4-Pro buys ~1 AA index point of hard reasoning and fixes none of these.
(3) The free-5M question is MOOT: wholesale fresh load is ~7.4M tokens/mo (3.45M
input + 3.9M output) > 5M in every branch -- the repo's 3.3M claim counted input
only. Revisit only via the v1.5 accuracy-gate pilot in cascade position, if
Gemini flakiness recurs across the next week of clean runs.

## 2026-08-30 — every post-rework run shipped "nothing new": validate self-match

Symptom (owner-reported): after the Persian/Jalali output rework, every Telegram message
was the Farsi `چیز تازهای نسبت به اجرای قبلی نیامده` one-liner — never actual news.
Mechanically reproduced before fixing (`tools/repro_validate_selfmatch.py`, now
redundant — the regression test below is the permanent guard).

Root cause: the production stage sequence is understand INSERTING this run's events into
the events table, THEN validate's anti-repetition (`_drop_repeats`) reading
`read_recent_events(db, 72h)` from that same table. The fresh events' own rows were in
the window, so every event cosine-matched ITSELF (1.0 ≥ 0.55) and was dropped as its own
repeat. Zero events → compose → honest one-liner. Every run, every cluster, silently.

Why the suite stayed green at 488: `test_repeat_follow_up_is_dropped` seeds the DB with
only a PRIOR run's event — it never simulated the production sequence of
insert-then-read-within-one-run. The test tested the intent, not the wiring. The trap is
the familiar one (a green suite that never executed the real sequence) but this time it
shipped to production and the failure mode was silence, not a crash.

Fix: `_drop_repeats` excludes this run's event keys from the recent window (a set
subtraction, no schema change). Same-key re-formation within 72h cannot happen in
production (items are URL/hash-deduped for 7–30 days), so the exclusion costs nothing.
Regression test added: `test_this_runs_own_rows_are_not_self_repeat_dropped` — runs the
real insert-then-validate sequence and asserts the fresh event survives while a prior-run
follow-up still drops. Suite 488 → 489, shim-verified, count predicted before running.

Standing lessons:
(a) Any stage that READS a table another stage just WROTE in the same run must exclude
    its own writes, or prove the exclusion is unnecessary. Grep for `read_*` calls in
    `pipeline/` whenever a stage pair changes order.
(b) Anti-repetition tests must model the real stage sequence (insert this run's rows
    first), not just the idealised old-events state.
(c) Known residual wrinkle, filed in CLAUDE.md Pending: repeat matching compares against
    ALL recent stored events, including ones the owner never saw (dropped below
    min_score or as repeats). A never-seen story can block its own follow-ups for up to
    72h. Clean fix is a `delivered` flag (additive schema, v1.5 candidate). Live-state
    consequence after this fix ships: events written by the broken runs are in the DB
    and can suppress matching stories until ~2026-09-02 — self-correcting, accepted.

## g1 SOLVED 2026-08-19 — date-only RFC-822, plus the Tehran display decision

**PHASE 3 CLOSED. collect-test GREEN 2026-08-19 after this fix.** All six gate conditions
pass: >=160 items, >=8 sources, required ids present, every timestamp predates run start,
no all-null date source (condition 5, added same day), no source stale >14 days
(condition 6, added same day). Conditions 5 and 6 were each proven by failing on the
real defect they were written for before going green.

**Cause: `state_dept_travel` emits `'Wed, 19 Aug 2026'` — a valid RFC-822 date with NO
time component — on 95 of 95 items.** Reproduced against the real code:
`parsedate_to_datetime` RAISES `ValueError`, `parsedate_tz` returns `None`,
`fromisoformat` raises, `_US_STYLE_DATE_RE` does not match. `parse_date` returned `None`
for the entire feed, correctly.

Not a regex bug — `DATE_RE` matched all 95. A `parse_date` gap.

**The channel-level date on that same feed is full** (`'Sun, 16 Aug 2026 14:30:06 GMT'`).
That single fact explains the whole three-round confusion: `check_feeds.py` reads the
first date anywhere in the body and got the channel one; the collector reads per-item and
got time-less strings. Neither tool was wrong; they were reading different elements.
**Standing lesson, now twice-earned: the probe's `newest` column proves a feed has A
date. It proves nothing about the items.**

**Fix and the decision inside it.** Parsing date-only to midnight UTC is the only sensible
choice, but it creates a fiction that is invisible downstream — and the owner's request to
display Tehran time is what made that unacceptable rather than merely untidy.
**00:00Z renders as 03:30 IRST.** An advisory whose publication time is genuinely unknown
would print as "19 Aug, 03:30", presenting an invented moment as fact — hard constraints
10 and 11. So `date_only: bool` is carried on `Item` from collection, because it cannot be
recovered later: once the raw string is gone, `00:00:00Z` is indistinguishable from a real
midnight publication.
Two consumers must honour it: the composer (print the date, say the time was not stated)
and Phase 6's 30-minute near-duplicate window (which would otherwise treat every same-day
advisory from such a feed as simultaneous).

`israel_local` is deliberately NOT applied to a date-only value — shifting a placeholder
midnight by −3h lands on 21:00 the *previous day*, inventing a date error on top of an
unknown time. No Israeli source is date-only today; the guard exists so that stays true by
construction. Covered by a test.

**Tehran: fixed UTC+3:30, no tzdata needed.** Iran abolished DST 2022-09-21 (Parliament
2022-03-15, communicated 2022-05-22) and has rejected reinstatement bills since; verified
2026-08-19. So it is a constant, not a rule — which matters because `dates.py` deliberately
carries no `zoneinfo` dependency. Residual risk recorded in the code: if Iran ever restores
DST this goes silently wrong by one hour for half the year, showing up as skewed digest
timestamps rather than a crash.
**Storage stays UTC; conversion happens at composition only.** Converting earlier makes
dedupe windows and trend deltas depend on a display preference, which is how a timezone
bug becomes a data bug.

**robots.txt CLOSED, favourably.** No `robots.txt` exists on `t.me` *or* `telegram.org` —
both 404. Nothing is disallowed, so `t.me/s/` conflicts with no directive and
`respect_robots_txt: false` is vindicated rather than assumed. Open since session 4.
Round 1 asked `telegram.org` and got a meaningless 404; robots.txt is per-host and the
collector fetches `t.me`. CLAUDE.md had named the wrong host from the start.

Tests: 9 added to `tests/unit/test_dates.py`, all 23 in that file executed and passing,
including the pre-existing 14 (no regression). Expected full suite **174 → 183**.


## Round 1 of dump-body, 2026-08-19 — two tool defects, one real answer

Three questions asked, one answered, and **two of the three failures were in the
diagnostic tool, not in the thing being diagnosed.**

**g1 — INCONCLUSIVE, and the tool is why.** Verdict came back "per-item dates DO exist
and DATE_RE matches them ... check the value format below". There was no value format
below: `analyse()` printed channel-level values and item tag *names*, never the per-item
date *values*. The verdict named a next step the tool did not supply.
Worse, the count was misleading. `DATE_RE`'s value group is `(.*?)`, which matches the
**empty string**, so `<pubDate></pubDate>` registers as a hit while `parse_date`
correctly returns None. "Dates exist" was therefore not a safe reading of that number.
Fixed both: non-empty values are counted separately, up to 5 raw per-item values print
via `repr()` (so CDATA wrappers, stray whitespace and empty strings are visible), and a
new **CAUSE (c) — EMPTY DATE TAGS** verdict fires when hits exist but all are blank.
Re-verified offline against four synthetic bodies: empty tags → (c); CDATA-wrapped →
"dates exist, check format" with the wrapper visible in the output; ordinary → same
verdict with a clean value; no date tag → (a). All four correct.
**Standing lesson: a regex hit count is not evidence that a value is present. Any
diagnostic that reports "N matches" must also report what matched.**

**robots.txt — asked of the wrong host, answer worthless.** The step fetched
`telegram.org/robots.txt` and got 404. robots.txt is **per-host**, and the collector
fetches `t.me`, not `telegram.org`. CLAUDE.md had named the wrong host since the question
was first raised and it was copied into the workflow unexamined. Now checks
`t.me/robots.txt` first as authoritative, `telegram.org` as context only, and states
explicitly that a 404 means no robots.txt exists, i.e. nothing disallowed — which is a
real answer rather than a failed check.

**GovDelivery 406 — CONCLUSIVE, and negative.** `Accept: */*` still returned 406. `*/*`
satisfies any genuine content negotiation, so a 406 against it is a WAF using a
nonstandard status code, not media-type refusal. This closes r6: **all four MARAD paths
are dead** (3 × 403 IP-level, 1 × 406 WAF). MSCI has no machine-readable route from a
GitHub runner. Maritime coverage is confirmed ZERO with no substitution path found.
Remaining options are a Google News `site:` proxy (existing USE_CAVEAT pattern) or an
owner-supplied replacement mirror channel.

Side note: `dump-body.yml`'s header claimed the job "always exits 0". False — the 406 run
went red because `dump_body.py` returns 1 on a fetch failure. Comment corrected rather
than the behaviour: a red run here means "could not fetch", never "the feed is wrong".

## Status

- Phase 0 — architecture drafted; scoring analysis (STRAT/TACT/MSTRESS, event registry,
  markets.py, posture-persistence) integrated into ARCHITECTURE.md 2026-08-01.
  **Awaiting owner approval.** Scope narrowed by session-3 decision 1: the scoring half is
  deferred out of v1.
- **Phase 1 — BUILT and hardened 2026-08-12.** Skeleton: `pyproject.toml`, CI workflow,
  `src/agent/{config,settings,settings_schema,run}.py`, `util/logging.py`, pipeline no-op
  stages, package stubs for collectors/memory/risk/llm/delivery, 21 pytest cases.
  Gate `python -m agent.run --dry-run` exits 0 with one line: `run summary: items=0
  clusters=0 messages=0`, with zero env vars and zero network (verified with
  `socket.connect` patched to raise).
  Adversarial verification found 1 CRITICAL + 3 MAJOR, all now fixed:
  (a) CRITICAL — the log redactor replaced secrets sequentially, so a secret that is a
      prefix of another secret caused the longer one's suffix to leak verbatim into a
      world-readable log. Now a single cached alternation regex, longest-first.
      `_MIN_SECRET_LEN` 8 → 16 so short common words cannot be registered as secrets.
  (b) MAJOR — `Settings.from_dict` checked key presence but never leaf types;
      `max_items_per_source: "twenty"` and `: true` were both accepted silently. Now
      type-checked against `settings_schema.py`, bool explicitly rejected for numerics,
      no string coercion, negatives rejected where definitionally invalid.
  (c) MAJOR — `tier: true` and `tier: 1.0` passed the credibility check because
      `True == 1` in Python. Now type-identity checked.
  (d) MAJOR — duplicate YAML keys were silently last-write-wins. Now a
      `SafeLoader` subclass raises `ConfigError` naming the key and line.
  **Gate confirmed on real hardware 2026-08-12** by the owner on Windows:
  `python -m pytest -q` → `41 passed in 0.23s`; `python -m agent.run --dry-run` → the single
  expected line. 41 = 33 test functions, one of them parametrized into 9 cases. Reconciled,
  no skips, no phantom collection.
- **Phase 2 (Telegram delivery) — BUILT and gate-green 2026-08-13.** Brief:
  `agents/briefs/PHASE_2_BRIEF.md`. Files: `src/agent/delivery/{message,budget,formatter,
  credentials,transport,telegram}.py` + tests. Brief decisions held, do not relitigate:
  `parse_mode=HTML` not MarkdownV2; the 4,096 cap counted in **UTF-16 code units**;
  `429` honours `retry_after`, non-429 `4xx` never retried; delivery failure returns a
  result object and never crashes the run; `--send-test` verifies the live path.
  **Gate confirmed on owner's Windows 2026-08-13:** `python -m pytest -q` → `95 passed`
  (41 → 95); `--dry-run` → the single expected line; `--send-test` → mock mode, no network.
  95 = 87 unit + 10 integration with parametrize expanded; predicted before the run and
  matched exactly, so no phantom collection.
  Deviations from the brief's file table, both deliberate: `telegram.py` split into
  `credentials.py` + `transport.py` (combined = 247 lines, over constraint 12);
  `test_telegram.py` split into `test_telegram_retry.py` for the same reason.
  **Three review rounds were needed. The builder's own suite passed clean before each.**
  Round 1 (independent review) found 2 CRITICAL + 3 MAJOR:
  (a) CRITICAL — `budget.py` dropped the overflow marker whenever the header consumed the
      budget, so truncated items vanished with no trace, breaking "never silently discard".
  (b) CRITICAL — `retry_after: -5` reached `time.sleep(-5)` → uncaught `ValueError` out of
      `send()`, killing an unattended run. Now clamped to `[0, MAX_RETRY_AFTER_SECONDS=60]`;
      absent/null/non-numeric falls back to normal backoff. The 60s cap also stops a
      server-supplied huge `retry_after` hanging the run to GitHub's 6h kill.
  (c) MAJOR — `.get()` on a parsed JSON body assumed a dict; a list/string/number/null body
      raised `AttributeError`. Now `isinstance` guarded in both `telegram.py` and `transport.py`.
  (d) MAJOR — `run_send_test` only caught `TelegramConfigError` around client construction;
      an exception from `.send()` escaped. Now caught, logged redacted, exit 0.
  Round 2 (review of the fixes) found a NEW defect inside fix (a): the marker was appended
  unconditionally, so a budget smaller than the marker itself produced output *over* the cap.
  Exceeding the cap is worse than losing the marker — a 4,097-unit message fails the send
  outright. Marker is now itself truncated via `utf16_truncate`; `max_units=0` → `""`.
  Round 3 (owner's real gate) found the one no sandbox could: **`requests` was imported at
  module top in `telegram.py` (only to catch `requests.exceptions.*`) and in `transport.py`,
  and `run.py` imports `telegram.py` unconditionally — so `--dry-run`, mock mode and the
  entire offline suite required an HTTP library they never call.** It passed in the agents'
  sandbox purely because `requests` happens to be installed there. Owner's clean Windows
  Python 3.12 has no third-party packages beyond pytest and PyYAML, and all five new test
  modules plus both CLI commands died at import. Fixed by layering, not by installing:
  `transport.py` owns `TransportError`/`TransportTimeout` and translates requests' exceptions
  at its boundary; `import requests` moved inside `RequestsTransport.__init__`; `telegram.py`
  no longer knows requests exists; tests use the transport-agnostic types.
  That fix exposed a third latent bug: `from_env()` built a real `RequestsTransport` even
  with no credentials — i.e. on the exact mock path `--send-test` uses. It now only builds
  one when both credentials are present.
  **Standing lesson, applies to every remaining phase:** the sandbox has PyYAML, requests,
  bs4 and numpy pre-installed; the owner's machine and a fresh CI runner do not. Any phase
  adding a dependency must be verified with that dependency made unimportable
  (`sys.modules["x"] = None`), not merely "it passed here".
  **That lesson is now enforced, not remembered.** Owner installed `requests` on Windows
  2026-08-13, which destroyed the accident that caught the bug. Replaced by
  `tests/integration/test_no_requests.py` (5 cases): a sentinel test that the block itself
  works, all 6 offline modules importable, `--dry-run` exit 0, `--send-test` mock path
  exit 0, and `RequestsTransport()` failing at construction with a `pip install requests`
  hint. **Gate re-confirmed on owner's Windows 2026-08-13: `100 passed`** (95 → 100).
  Any future phase adding a dependency adds its module to `OFFLINE_MODULES` there.
- **Phase 3 (collectors) — BUILT 2026-08-18, AWAITING OWNER GATE. Not yet gate-green.**
  Brief: `agents/briefs/PHASE_3_BRIEF.md`. New: `src/agent/collectors/{base,fetch,rss,
  telegram_web,registry,report}.py`, `run.py --collect-only`,
  `.github/workflows/collect-test.yml`, 5 fixtures, 5 test modules.
  Routing: Sonnet Implementer from the brief → fresh Sonnet Verifier → fix round → second
  fresh Verifier → Architect (Opus) gate review. No agent reviewed its own output.
  **Pytest 165 — predicted then CONFIRMED on the owner's Windows 2026-08-18: `165 passed`.**
  (100 Phase-2 baseline + 65.) Predicted independently three times by different routes,
  agreeing each time, and matched the real run exactly, so no phantom collection. `pytest` is NOT installed in the agent
  sandbox (contradicts the session-3 note claiming it is) — so 165 is a prediction, and
  the owner's Windows run is the only real verification. Reconcile against 165 exactly.
  Verified mechanically here: `Item` is exactly the 7 specified fields; zero
  `bs4`/`numpy`/`lxml`/`feedparser` anywhere in `src/`; no top-level `import requests`;
  no `datetime.now`/`utcnow` in `collectors/`; `.content` appears only in a docstring
  explaining why it is not used; all 6 collector files under 200 lines; the
  sources→credibility join asserts across all 51 including `enabled: false` and raises.
  **`settings.yaml` `user_agent` is now byte-identical to `tools/check_feeds.py:48`**,
  verified by AST-parsing the probe constant and comparing to the loaded YAML — not by
  eye. This was the brief's "most likely to burn this phase" item.
  Review findings, all fixed: (a) MAJOR — `telegram_web.py` hashed a decode→slice→re-encode
  round trip, so `raw_hash` was a function of charset-resolution behaviour rather than of
  the wire bytes, which would have silently invalidated Phase 4's dedup on any future
  decode change. Now slices post blocks out of the raw bytes like `rss.py` already did,
  with a regression test that fails against the old path. (b) Constraint 12 — `registry.py`
  was 207 lines; report/table formatting split into `report.py` (165 + 61).
  (c) **Found by the Architect after three agents missed it — the gate could not detect the
  one failure `ynet_he` was enabled to force.** `build_json_report` drops `None` dates, so
  a source whose parser yields `None` for every item emits `published_at: []`; the
  predates-workflow-start loop then never executes and `all_timestamps_identical` is
  False, so total date failure passed as green. `collect-test.yml` now asserts that each of
  the four `REQUIRED_SOURCE_IDS` (`ynet_he` + the three `t.me` sources) with `kept > 0` has
  at least one non-null timestamp. **Standing lesson, third instance of the same class:
  a gate that is only read passes; a gate must be attacked with the specific bug it exists
  to catch.**
  (d) **Found by the owner's real pytest run 2026-08-18 — `1 failed, 164 passed` — after
  the build, two Verifier rounds and an Architect review all passed it.** `fetch.py`'s
  `_maybe_gunzip` caught `except OSError`, but `gzip.decompress` on a stream truncated at
  `MAX_BYTES` raises **`EOFError`, which derives from `Exception`, not `OSError`** — so the
  handler missed the exact case its own comment said it existed for, and the exception would
  have escaped `fetch()` and killed a source on the path the 400 KB cap makes routine. Now
  `except (OSError, EOFError, zlib.error)`; all four arms verified by hand against truncated,
  bad-magic, corrupt-deflate and CRC-mismatch bodies plus the happy path. Note `zlib.error`
  is also not an `OSError`; only `gzip.BadGzipFile` is.
  **Why every sandbox missed it: the agent sandbox has no `pytest`, so the Implementer's
  stdlib fallback harness covered only `test_telegram_web/report/registry/collectors_base`
  — `test_fetch.py` was written and never executed by anyone.** The test was correct and
  the production code was wrong. **Standing rule: an unexecuted test is not evidence. When
  reporting a phase as built, state explicitly which test modules were actually run and
  which were only written**, because "my harness passed" silently excluded 4 of 9 modules
  here. Fourth consecutive phase in which the owner's real gate found what no sandbox could.
  (e) **Found by the first real `collect-test.yml` run 2026-08-19 — the CI gate did its job
      on the first attempt.** It failed with `ynet_he: published_at 2026-08-19T17:11:47+00:00
      does not predate workflow start 2026-08-19T14:20:12+00:00` — an item published 2h51m
      in the FUTURE. **Ynet emits Israel wall-clock time while declaring GMT**; true
      publication was 14:11:47Z, nine minutes before the run. This also settles the
      long-standing open question in this file: **Ynet's date format is ordinary RFC-822 and
      parses fine — only its declared zone lies.** `_YNET_DATE_RE` (now `_US_STYLE_DATE_RE`)
      is therefore dead code against the live feed and is kept only for regression.
      Fix: source-id-keyed `ISRAEL_WALL_CLOCK_SOURCE_IDS = {ynet, ynet_he}`; for those the
      declared tzinfo is discarded and the wall-clock digits reinterpreted as Israel local.
      Format-agnostic by construction — naive or falsely-GMT, the digits and the corrected
      instant are identical, so this did not require knowing the wire format.
      **Deliberately NOT a generic "reject future timestamps" guard:** silently repairing any
      future-dated item would mask exactly the class of feed lie the gate exists to surface.
      A fresh adversarial Verifier then found 2 real defects **in that fix**, both accepted:
      **CRITICAL** — the US-style fallback branch applied the Israel offset *unconditionally*,
      so any source emitting `M/D/YYYY H:MM:SS AM/PM` (plausible for `state_dept_travel` or
      `the_war_zone`) was silently shifted 2–3h, falsifying the fix's own "keyed on source id,
      never sniffed" guarantee. Now gated on the flag; untagged → naive UTC.
      **MAJOR** — the month-based DST approximation (+3 for months 4–9) was wrong for ~30
      days/year (**Mar 27–31 and Oct 1–25**) and wrong in the dangerous direction: one hour
      too little subtracted, i.e. a timestamp up to 1h in the future, re-creating this very
      gate failure at reduced magnitude. Replaced with the real Israeli rule (IDT from 02:00
      the Friday before the last Sunday of March to 02:00 the last Sunday of October),
      computed per year in ~10 lines of stdlib — **no zoneinfo/tzdata**, which is not an
      approved dependency and is not guaranteed on the owner's Windows or a bare CI runner.
      Date logic moved to new **`src/agent/collectors/dates.py`** (141 lines) with tests in
      `tests/unit/test_dates.py`; the DST rule pushed `rss.py` past the 200-line cap, and
      `rss.py` is now 103 lines. `dates.py` added to `OFFLINE_MODULES`.
      **Count 165 → 171 → 174**, verified by AST parametrize-expansion, not by counting `def`s.
      Every test in `test_dates.py` and `test_rss.py` was EXECUTED here under a stubbed
      `pytest` (27 funcs, 0 failures, 1 fixture-skipped) — per the standing rule from (d).
      **Standing lesson, and the first time the loop caught itself: the fix for a gate failure
      is exactly as likely to contain a defect as the original code, and here it contained two.
      Never ship a gate fix without an adversarial pass on the fix itself.**
  (f) **GATE GREEN 2026-08-19 14:55 UTC. Phase 3 is complete.** `174 passed` on the owner's
      Windows (165 → 171 → 174, matched the prediction exactly) and `collect-test.yml`
      printed `GATE PASSED`: `total_kept: 204`, **10/10** enabled sources returned items,
      all four `required_source_ids` present with real timestamps, no source stamping one
      identical time. The Israel correction is confirmed against the live feed —
      `ynet_he` newest is now `2026-08-19T14:11:47+00:00`, i.e. exactly the raw
      `17:11:47` minus the 3h IDT offset that finding (e) predicted.
      **This also closes the last UNVERIFIED item in `telegram_web.py`'s header:** the
      `t.me/s/` class names and `<time datetime=...>` selectors DO match the live page.
      All three Telegram sources parsed real, distinct, plausible post times.
      Per-source kept: `state_dept_travel` 30, `ajar`/`bbc_en_me`/`bbc_persian`/`irna`/
      `the_war_zone`/`ynet_he`/`tg_padeshah_fxn`/`tg_ukmto_mirror` 20 each,
      `tg_militarywave` 14 (15 raw, 1 unparsed — media-only post, not an error).
      9 of 10 sources hit their cap, so 204 sits just under the 210 post-cap ceiling and
      raw volume (356 entries) far exceeds what is kept. Confirms the session-5 volume
      finding at thin-slice scale.
      **The green run was reached only after a wasted round: the 14:43:55 UTC run failed
      with the IDENTICAL ynet timestamp because "Re-run jobs" was clicked on the failed
      14:20 run. `actions/checkout` then replays the run's ORIGINAL recorded SHA
      (`92dbf33`), not the branch head — so the fix, pushed at 14:39:10, was in origin and
      still did not execute.** Diagnosed by timeline, not by reading logs: the item
      timestamp was byte-identical across both failures, which correct code cannot produce.
      **Standing lesson, fifth instance of the verify-the-artifact-not-the-intent class,
      and the one the existing ci3 rule does NOT cover.** That rule says `HEAD ==
      origin/main` proves nothing, verify the fix is inside origin. Here the fix WAS inside
      origin and old code ran anyway. Extend to: **verify the SHA the workflow actually
      executed. Never validate a gate fix with "Re-run jobs" — always a fresh
      `workflow_dispatch` on `main`.** `collect-test.yml` now echoes `${{ github.sha }}`
      immediately after checkout so its log is self-identifying and this is one grep, not
      a timeline reconstruction.
  (g) **Two real defects were INSIDE the green report, and the gate could not see either.**
      Green means the assertions passed, not that the data is sound — read the JSON.
      **g1 — `state_dept_travel`: 122 parsed, 30 kept, `published_at: []`. Every timestamp
      is null.** It is the only tier-1 source in the thin slice and 30 of 204 items, so
      **15% of the corpus is undated and the gate said PASSED.** Cause of the blind spot:
      the null-date assertion added by the Architect as finding (c) was scoped to
      `required_source_ids` only, so it caught the four sources it was aimed at and missed
      the fifth. Same defect class, one source to the left.
      **The feed is not dateless** — the probe read `"Sun, 16 Aug 2026 14:30:06 GMT"` off it
      in ci2, ci3 and ci4, ordinary RFC-822 that `parsedate_to_datetime` handles. Two
      candidate causes, UNRESOLVED and not distinguishable without a body dump: channel-level
      `<pubDate>` only with genuinely undated `<item>`s, or a per-item tag `_DATE_RE` misses.
      **Standing lesson: the probe's `newest` column proves a feed has A date, not that its
      ITEMS do.** The probe takes the first `pubDate` in the whole body; the collector takes
      one per entry slice. Never read `newest` as evidence of per-item dates again.
      Impact if unfixed: `rss.py` sorts undated items last in original feed order, so
      "first 30 of 122" is taken blind — the IranWire failure its own comment warns about,
      on the one source that exists to deliver timely advisories.
      **g2 — `tg_ukmto_mirror` is DORMANT. Newest post 2026-07-14T14:59:57Z.** The gate
      reports its age as 35 or 36 depending on the hour the run fires — age is elapsed
      time truncated to whole days, not a date subtraction. A changed number is not a
      changed feed. All 20
      items are June–July. IranWire was CUT at 31 days; this is worse and passed, because
      the gate had no staleness check at all.
      **Corrects the session-5 claim at line ~670: maritime coverage is not "single-sourced
      through an unofficial mirror", it is ZERO.** Both official UKMTO URLs are
      CUT_BOT_BLOCKED and the only remaining path has published nothing in five weeks.
      Systemic part: **the probe returns an empty `newest` for EVERY Telegram row, so none
      of the 23 Telegram sources have ever been staleness-checked.** The collector can now
      read their dates; the first three tested split 2 live / 1 dead. The other 20 are
      unaudited and must be checked as they are enabled at Phase 8.
- **Phase 4 (storage) — BUILT 2026-08-19, AWAITING OWNER GATE.** Brief:
  `agents/briefs/PHASE_4_BRIEF.md`. Files: `src/agent/memory/{schema.sql,db,models,dedup,
  retention,crypto}.py` (198/165/140/199/105/151 lines, all under the cap) + five
  `tests/unit/test_memory_*.py`. `run.py` gains `--db` and `--init-db` and stays at 200.
  **Expected count on the owner's Windows: 274 = 183 baseline + 91 new.** State that
  number before running, then reconcile — the baseline itself was reconciled here as
  167 unit + 16 integration, which is what makes 274 falsifiable rather than a guess.
  Decisions made inside the brief's silences, each one a place a later phase could be
  broken by a different choice:
  (a) **An `items` table exists that ARCHITECTURE §11 does not list.** Gate 1 says "write
      N items, read N items back", and there was nowhere to write them — §11 jumps
      straight to `events`, which is Phase 6. `items` is the raw tier, pruned on the same
      `events_days` window. If Phase 6 decides clusters own the raw text, this table is
      the thing to delete, not to work around.
  (b) **Journal mode left at DELETE, not WAL.** WAL is the reflex and it is wrong here:
      `-wal`/`-shm` sidecars would break both the "no new file exists after a halt"
      assertion and Phase 7's single-file encrypt. A `.db.age` restored without its WAL
      is a database missing its most recent committed writes — silent, and exactly the
      class of loss constraint 14 exists to prevent.
  (c) **`open_db(create_if_absent=False)` by default.** The one line that makes constraint
      14 real: a failed Phase 7 decrypt leaves no file, and any default that creates one
      turns a restore failure into a silent fresh start. Creation requires the explicit
      `--init-db` flag, and the two gate tests deliberately pass `create_if_absent=True`
      while still asserting the halt — permission to create is not permission to discard.
  (d) **A zero-byte file is a VALID empty SQLite database and `PRAGMA integrity_check`
      passes on it.** So integrity is not the guard; the missing `schema_version` row is.
      Tested explicitly, because this is the failure that looks healthiest.
  (e) **`market_metrics` has no retention key in `settings.yaml`** but §11 specifies 30
      days. Bound to `events_days` rather than hardcoded, per "do not hardcode any of
      them". If the two ever need to differ, that needs a new key, not a literal.
  (f) **`source_health` is deliberately absent from the prune table** and asserted so.
      ~51 rows, one per source; pruning it erases the only record that answers "how long
      has this feed been dead?" — the exact question g2 took three probe rounds to reach.
  (g) **The age private key never enters argv.** Written to a `mkstemp` file at 0600,
      passed via `-i`, unlinked in `finally`. `/proc/<pid>/cmdline` is world-readable and
      this is a shared runner. `age` stderr is scrubbed before it becomes an exception
      message, because the binary echoes what it failed to parse.
  Three test failures on the first run, all real and all fixed on the source side rather
  than by relaxing the assertion:
  (h) two hygiene greps caught their own docstrings — `models.py` named the display
      timezone and `retention.py` spelled out the forbidden wall-clock call while
      explaining that it must not appear. Reworded to describe rather than name. A grep
      rule that its own file violates gets deleted by the next person; this was worth two
      minutes to keep strict.
  (i) `read_schema_version` raised "no readable meta table" where the test expected the
      message to name `schema_version`. The test was right: all four halt messages now
      name the thing that could not be read, so a world-readable log line is
      self-explanatory to an owner who is not holding this file open.
  Verified beyond the unit suite: the CLI creates all 12 tables with `schema_version=1`
  and **no `-wal`/`-shm` sidecars**, logs one storage line, and — run without `--init-db`
  against an absent path — exits 1, creates nothing, and says why. Semantic dedup
  (layer 4, cosine) is NOT here; it is Phase 6, and `embeddings` is created empty so that
  phase needs no migration against encrypted state.
  **GATE GREEN, owner-run on Windows 2026-08-21. Phase 4 CLOSED.** `python -m pytest -q`
  passed; `--collect-only --db state.db --init-db` returned `TOTAL kept=184
  sources_with_items=9/9`; the same command WITHOUT `--init-db` against an absent path
  exited 1 with the constraint-14 message and created no file. Shipped in `56e3dda`,
  content-verified on `origin/main` via `git show origin/main:<file> | findstr` — not by
  comparing HEAD, per the ci3 lesson below.
- **Phase 5 (LLM router) — BUILT and gate-green 2026-08-29 (session 8).**
  Brief: `agents/briefs/PHASE_5_BRIEF.md`. Files: `src/agent/llm/{errors,transport,limits,
  breaker,call,providers,router,wiring}.py` (all under the 200-line cap) +
  `src/agent/settings_llm.py` + `src/agent/leaf_types.py` + six test modules.
  **Owner decision landed: `llm.max_calls_per_run = 51`** — his rule: keep it free, use
  the free tools' caps (9 runs × 51 = 459/day vs Gemini 1,500 RPD; worst case never
  truncates). One global per-run counter in `CallBudget`, refusal BEFORE the request is
  built, logged once, run degrades. Compose can `reserve(1)` so the digest can never be
  starved. Provider-level constraint-15 guards (`max_calls_per_run`,
  `max_spend_usd_per_month`, `halt_on_budget_exceeded`, per-MTok prices) enforced
  generically against a fake metered provider; per-run scope is deliberate — the threat
  constraint 15 names is a retry loop, a within-run event, and cross-run accounting would
  have needed a Phase-4-frozen-schema migration.
  **Suite: 361 (274 baseline + 87 new), predicted 362.** Reconciled before claiming green:
  one new test miscounted by hand (providers file has 20, not 21) — prediction stated
  first, so the diff was investigated, per the discipline the baseline itself established.
  The sandbox has no pytest and no PyPI, so `tools/pytest_shim.py` (stdlib, now a
  permanent tool) ran the literal suite. Two of my own test-expectation bugs surfaced
  that way, not router bugs: a provider's FIRST call never sleeps (pacer test expected
  a sleep), and a canned OpenAI-shaped body given to the Gemini adapter correctly parsed
  as schema_error (backoff test expected success). Both fixed in the tests.
  All 10 brief gates pass. The two worth naming: (i) the redaction trap — a simulated
  provider error echoing the request URL with `?key=<secret>` in it, secret absent from
  every log line, `REDACTED` present; (ii) `see()` structurally incapable of reaching a
  text-only provider, asserted by attempting the illegal selection with zero transport
  calls. No `datetime.now`/`time.time()` under `src/agent/llm/`; no prompt literals;
  all `agent.llm.*` in `OFFLINE_MODULES`.
  Structural splits made mid-build, all for constraint 12: router.py (339 draft) →
  router + call.py (one attempt + structured logging) + wiring.py (construction);
  limits.py → limits + breaker.py; providers.py → adapters only, factory to wiring.py.
  Nested llm validation lives in settings_llm.py; the leaf-type checks shared with
  settings.py moved to leaf_types.py (no import cycle).
  **Known interaction flagged for Phase 6 wiring: `max_retries: 3` caps the loop at 4
  attempts, so the breaker at `circuit_breaker_failures: 5` never opens at shipped
  defaults.** Mechanism is gate-tested; the two numbers must be reconciled when the
  pipeline is wired. Also: OpenRouter got a placeholder model (roster rotates weekly;
  the adapter refuses to run without a model line — loud if wrong).
- **Phase 6 (Understand) — BUILT and gate-green 2026-08-29 (session 8, same day as
  Phase 5).** Brief: `agents/briefs/PHASE_6_BRIEF.md`. Files:
  `src/agent/pipeline/{filter,embed,cluster,understand}.py` + `build_stages()` in
  `pipeline/__init__.py` + `memory/event_models.py` + `config/topics.yaml` +
  `config/prompts/{understand,vision}.txt` + six test modules.
  **Suite 415, 0 failed.** Predicted 409 before running; reconciled: the pre-Phase-6
  baseline was 367, not the 363 I had recorded (my bookkeeping error, +4), and the 48
  new tests were exactly as predicted. File-level collection table sums to 415
  exactly, so nothing is phantom and nothing is missing.
  Decisions that landed, each closing a long-pending item:
  (a) **Cluster cap enforced** (session-5 decision 1): rank tier→recency→size,
      truncate at max_clusters_per_run, dropped keys logged once, no LLM call.
  (b) **Topic gate enforced**: `topic_gate: true` is now a real sources.yaml field on
      the six general-interest feeds; topics.yaml is owner-editable; unknown-language
      items fail open.
  (c) **`circuit_breaker_failures: 5 → 2`** — max_retries=3 caps the loop at 4
      attempts, so 5 could never open. Mechanism already gate-tested in Phase 5.
  (d) **`items` table survives**; events are the post-understand store
      (INSERT OR IGNORE on UNIQUE event_key; claim_status 'unconfirmed' until Phase 9).
  (e) **sentence-transformers is an optional [embeddings] extra**, lazy-imported.
      CI's test job does NOT install it (installing it would hide the lazy-import
      bug, the exact requests lesson); a new `embeddings` CI job installs it, caches
      the 470 MB model on ~/.cache/huggingface, and smokes the real MiniLM to 384
      unit dims. NumPy deliberately NOT added: the greedy clusterer over ~120 unseen
      items runs in well under a second in pure Python.
  (f) **--dry-run implies mock wiring** (empty router + FakeEmbedder) so the owner's
      machine exercises the full stage sequence with zero keys and zero downloads.
  (g) **Vision stays a passthrough** — no collector extracts images in v1.
  Two build defects found by the suite (mine, both test-side): filter test ctx
  dataclass missing a default; understand loop continued after REFUSED_CAP instead of
  breaking (the stage contract said break — fixed in code, test asserted the break).
  One more: 5 "distinct" 2-D test vectors were not distinct enough ((1,1) merges with
  (1,0) at threshold 0.62) — moved to 3-D. And a real wiring bug: `_load_prompt`
  resolved the config dir with parents[2] (src/config) instead of parents[3] (repo
  root) — caught by the dry-run integration test.
  Remaining Phase 6 debt, explicit: `cluster_similarity_threshold: 0.62` is still the
  unmeasured placeholder — Phase 10 tunes it on real data (LEAD_HANDLING.md's own
  warning about guessing thresholds applies).
- **Phase 7 (Actions) — BUILT 2026-08-29 (session 8). Suite 429, 0 failed.**
  Brief: `agents/briefs/PHASE_7_BRIEF.md`. Files: `pipeline/{collect,compose,deliver}.py`,
  `.github/workflows/pipeline.yml`, four test files. The full pipe now runs as
  `python -m agent.run --db state.db`: collect (fetch→dedup→store→ctx.items; raises
  without a db in a real run; skips in dry-run/mock), compose (budgeted message via
  Phase 2's formatter; honest one-liner when empty; Tehran display; date_only
  clusters print "time not stated" — the pending composer consumer, closed;
  digest marker via NEWS_CURATOR_DIGEST env, no new CLI flag), deliver (Phase 2
  client, mock path without credentials, `deliver` counter honest).
  Workflow traps caught while writing pipeline.yml, both the class the phase exists
  to catch: (a) `git add state.db.age` on the state branch would be silently
  swallowed by the repo's own `*.age` gitignore — fixed with `git add -f`;
  (b) the digest-vs-pipeline distinction uses `github.event.schedule == '30 3 * * *'`,
  which is false on workflow_dispatch — documented in the file.
  Gate (3 consecutive unattended runs) is owner-clock-bound: needs secrets +
  bootstrapped state branch; runbook is Phase 10's deliverable.
- **Phase 8 (Widen sources) — BUILT 2026-08-29. Suite 438.** 50/51 sources enabled
  per the prune sheet (ukmto_mirror stays off -- dormant, MARAD candidates staged);
  `signals_covered` on every entry (initial judgement mapping; the coverage check
  itself found the two uncovered signals -- C1 and D4 -- and the mapping was patched);
  `config/risk_weights.yaml` generated from backtest_weights.py's canonical CAT/TIER/
  STATEFUL dicts (36 signals, machine-consistent by construction);
  `agent/coverage.py` startup check (warn by default, promotable to halt).
- **Phase 9 (Validate) — BUILT 2026-08-29. Suite 464, 0 failed.** Independence by
  GROUP (decision 4 made executable -- BBC en+fa collapse to one, AP+Reuters share
  wire_west); claim_status arithmetic (likely/unconfirmed/rumour); lead-only clusters
  split out of the main feed; [RUMOUR] visible in the digest; lead_outcomes written
  silently. Two defects found and fixed, both the class the phase exists to catch:
  (a) **lead_outcomes was promised by LEAD_HANDLING.md rev 2 but absent from the
  Phase 4 schema** -- fixed with SCHEMA_VERSION 2 + an additive-only upgrade path in
  db.py (CREATE IF NOT EXISTS only; the rule that keeps it safe is stated in the
  code). Done pre-production: no encrypted state exists yet, so this is the last
  moment an additive fix is free. (b) **INSERT OR IGNORE silently dropped events
  whose first_seen_at was NULL** (undated clusters) -- the understand stage now
  writes the observation time (ctx.now), which is a fact; a regression test asserts
  the row persists. (c) Single tier-2 source = unconfirmed, not rumour -- my test
  expectation was wrong, the rulebook (>=2 independent NON-tier-3) was right.
- **Phase 10 (Hardening) — BUILT 2026-08-29.** source_health writes + degraded
  warnings (the IranWire/UKMTO precedent, now persisted instead of remembered);
  docs/OPERATIONS, ADDING_SOURCES, TROUBLESHOOTING; RUNBOOK.md (go-live sequence,
  exact Windows commands, index.lock + content-verify traps, cron kill-switch).
  **v1 CODE COMPLETE. Suite 464, 0 failed, shim-verified.** The remaining gates are
  owner/clock-bound: 3 consecutive unattended runs (needs secrets + bootstrap) and
  one week unattended. Threshold tuning (0.62) waits on that week's real data.
- **v1.5 (Phase 11, risk engine) — NOT STARTED, per session-3 scope cut.** The
  extraction accuracy gate is re-filed there: the extraction prompt and scorer do
  not exist in v1, and a gate for code that does not exist measures nothing.
- **First red CI run (owner's push, 2026-08-29): 6 failures, all in
  test_llm_redaction.py — `'LogCaptureFixture' object does not support the
  context manager protocol`.** The suite was shim-green 464/464. Root cause: the
  shim's Caplog implemented `__enter__`/`__exit__`, so `with caplog:` passed in
  the sandbox; real pytest's LogCaptureFixture has no such protocol, so CI
  rejected it. The shim had become a SUPERSET of pytest — the exact failure mode
  the shim discipline exists to prevent, now proven able to happen. Fix on both
  sides: tests use `with caplog.at_level(...)` (the pytest-supported form),
  and the shim's Caplog deliberately implements NO context-manager protocol,
  with a comment saying why. **Standing rule: the shim may only implement what
  the suite uses AND real pytest supports. When in doubt, the shim must be
  STRICTER than pytest, never looser.**
- **First live-run outage (2026-08-29): both LLM model ids were dead.** The
  first real pipeline run 404'd on every call: gemini-2.5-flash is LISTED by
  the models endpoint but generateContent 404s (discontinued June 2026 per
  the changelog -- listed-but-dead), and llama-3.3-70b-versatile is gone
  from Groq's roster entirely. The gemini-3-flash replacement guess did not
  exist (only -preview). Fixed by: (a) the router now ROTATES on 404
  (provider-specific, not request-level -- the old fatal treatment cost a
  full run with Groq sitting unused); (b) model ids replaced with
  `gemini-flash-latest` (self-healing alias; pin only when v1.5
  determinism matters) and `qwen/qwen3.8-27b`; (c) the pipeline workflow
  gained "List available models" probe steps for both providers -- ground
  truth in the log on every run. **Standing rule: model ids ROT. A pinned
  model id in an unattended system is a scheduled outage. Aliases for
  unattended systems; probe steps as the backstop; read them before
  touching anything else after an LLM outage.** Meanwhile that same run
  proved the whole pipe: 891 fetched, 875 dup-killed, 13 new, 6 clusters,
  message delivered (the honest "AI unavailable" notice, 92 chars).
  Also: HF Hub warns about unauthenticated requests -- it worked, but an
  HF_TOKEN secret is optional hardening if rate limits ever bite.
- **Output rework (owner decisions, 2026-08-29) -- Persian, ranked, anti-repetitive.**
  Built and shim-green (suite 488) in one local iteration per the owner's
  explicit "make things local, iterate, then update" instruction. Shipments:
  (a) Persian output with JALALI dates via `util/jalali.py` -- a stdlib port of
  jalaali-js; **found and fixed the classic porting trap: Python's % and // are
  floor-semantics, jalaali-js's are truncation-semantics, and the divergence
  silently broke mid-year dates to month 18 (Nowruz anchors passed by
  short-circuit -- anchor tests must not all sit on the early-return path).**
  (b) Deterministic digest ranking (`pipeline/rank.py`): category weight
  (military 6/security 4/politics 3/economy 2/other 0) + corroboration + best
  tier + recency decay + size, split at `digest_rank.min_score`, split across
  up to `max_messages` Telegram messages. Explicitly NOT the Phase-11 risk
  engine; weights owner-editable. (c) Anti-repetition: validate embeds new
  event summaries with the LOCAL model and drops matches >= event_match_
  threshold against the last repeat_window_hours of stored events -- zero LLM
  calls. (d) The LLM's own `headline` field is now the message title; the
  summary is detail-only -- the pre-rework composer repeated the same sentence
  twice per item. (e) Category/headline fields are in-memory only (schema
  frozen at additive CREATE IF NOT EXISTS; persist with Phase 11 if needed).
- Build-agent roster written 2026-08-01 (`agents/`). Four roles: Architect (strongest model,
  brief + gate review only, never writes code), Implementer (mid-tier for phases 4–8,
  light for 1/2/3/9/10, fresh context per phase), Verifier (light, adversarial, never the
  agent that wrote the code), Scout (light, mechanical research, CSV output only).
  Loop: brief → build → attack → gate review → commit → discard Implementer context.
  Estimated one-time build cost ~$50 at these tiers; cost is driven by context reuse, not
  model tier.

## Session 7 (2026-08-21) — provider economics, and an estimate that was wrong

Owner asked what the project costs to finish and to run. Four findings, one of which
closes a recurring question permanently.

**1. Runtime is $0/day and the number has 4.8x headroom.** ~35 calls/run x 9 runs =
~315 calls/day at ~3,050 in / 400 out, i.e. ~0.96M in / 0.13M out daily. That model
reproduces the rate card in CLAUDE.md exactly (DeepSeek $5.09/mo vs the recorded "~$5";
Haiku 4.5 $47.70 vs "~$38-60"), so the volume figures are sound. 315 against Gemini's
1,500 RPD is 21%. Embedding is local, Actions is free on public repos, Telegram is free.
The LLM is the only variable cost line and it is zero.
Minor inconsistency found in the verified-facts block: it states "~288/day" (36 x 8) while
the rate card assumes 9 runs. 324 is the right figure. Immaterial to dollars.

**2. The noise reduction is LLM-free, and this is the strongest fact in the design.**
800 raw -> ~120 unseen (SQLite dedup, Phase 4) -> ~25 clusters (MiniLM on CPU) is a **97%
reduction with zero LLM calls**. The LLM writes summary prose and extracts signals. If
every provider went dark the owner still receives deduplicated, clustered, source-tiered,
rumour-labelled items. **Provider loss is a quality-of-prose risk, not an availability
risk.** Reach for this whenever provider anxiety resurfaces; it reframes the whole
purchasing question.

**3. Consumer AI subscriptions do not provide API access. Closed, do not re-litigate.**
Owner asked about Claude Pro, ChatGPT Pro, Google AI Pro, DeepSeek and Kimi. Claude Pro
and ChatGPT: no API, separate billing product. **Google AI Pro is the trap** — its higher
quotas apply to the AI Studio Playground and Build mode, while raw API keys follow Cloud
Billing tiers, a separate system. Gemini CLI has exactly this friction, reading the key's
billing status (Free) rather than the account's subscription. DeepSeek and Kimi have no
relevant subscription tier, only metered API.
Two further reasons a subscription is the wrong instrument here: driving one from CI needs
consumer session credentials in a public repo's Actions, which is the same failure class
constraint 6 already rejected for Telethon but with a payment relationship attached; and
the owner's PC being always off rules out the legitimate local path.

**4. A paid account is WORSE for stability in this jurisdiction, not better.** Every paid
option needs an international card (the standing blocker) and creates a KYC and sanctions
surface tied to the owner's identity — an account terminable with money in it. A free key
called from a US runner has the smaller attack surface. On the axis the owner actually
cares about, free is the more stable configuration.

**Decision on record: buy nothing for runtime.** Chain stays Gemini (1,500 RPD) -> Groq
(14,400 RPD, 45x daily volume) -> OpenRouter -> degrade. Two independent free providers,
either sufficient alone.

**New high-value open question.** DeepSeek gives 5M free tokens/30d and the cached
fresh-token load is ~3.3M/month, so it may be a **second zero-cost provider** rather than
a $5/mo one. UNVERIFIED whether cached reads count against the allotment — already flagged
in the rate card, now promoted to an action. One support ticket, worth more than any
provider comparison in this section.

**The estimate I got wrong, recorded because the error is systematic.** First remaining-
build figure was $70-125. It modelled clean phases: build once, gate once, done. The owner
asked whether it was real and whether it included AI-caused bugs and wrong turns. It did
not, and the evidence against it was already in this file: Phase 2 took three review
rounds plus an owner gate that found a fourth defect and two cascading ones; Phase 3 took
four probe rounds, two gate failures and a diagnostic tool built mid-phase; Phase 4 had
two constraint-12 violations, a frozen-dataclass error and a self-inflicted tooling
failure that cost calls to diagnose. **Observed rework on this project is 2-4x nominal on
every phase without exception.** Revised remaining: **~$160-290**, total ~$230-390 at
Opus 5 list ($5/$25 per MTok, thinking billed as output, ~30% heavier tokenization on
4.7+).
Also missed on the first pass: **phases 7 and 10 are clock-bound, not effort-bound.**
Their gates are "three consecutive unattended runs" (9h minimum per attempt at a 3-hour
cadence) and "one week unattended". No amount of spend compresses those, and each failed
attempt costs a full cycle.
Standing rule from this: any estimate for this project states base, rework multiplier with
its evidence, and which components are clock-bound. Never the happy path alone.

**Build-cost restructure recommended, not yet adopted.** The roster in `agents/` already
specifies Implementer = mid-tier and Architect = strongest model for briefs and gate
reviews only. Phases 1-4 ran the Implementer role on Opus in chat, which is roughly 3x the
plan the repo already wrote down (`~$50 at these tiers`). Proposal: Opus writes the brief
and reviews the gate, Sonnet builds from it in Claude Code, Haiku does source audits.
Claude Pro at $20/mo includes Claude Code and is a defensible BUILD purchase — never a
runtime one.

## Session 6 (2026-08-18) — Fable review of the committed state and the Phase 3 brief

Two independent Fable verifiers, one on the committed config, one on the forward plan.

**State: clean. All 10 attacked claims VERIFIED, 0 CRITICAL, 0 new MAJOR.** 73 credibility
entries, tier dist 1:9/2:34/3:22/lead:8, 0 duplicate YAML keys; the sources→credibility
join is empty-diff across all 51; 51 staged / 10 enabled with real bools; 0 non-https urls;
all 23 telegram urls in `/s/` form; the enabled-10 matrix does span rss+telegram, en/fa/ar/he
and tier 1/2/3/lead; 328 items/sweep recomputed exactly. Independent checks it added and
passed: duplicate-url scan, duplicate-channel-under-different-id scan, same-host-different-
group scan, and a group-name near-duplicate scan across all 35 group values (no
`israel_press` vs `israeli_press` typo class). 22 credibility ids have no sources.yaml row
— safe by construction, an id nothing collects cannot fire.
**Upgraded in urgency, not severity: `tg_militarywave` is `group: null` AND in the enabled
10.** So the `group: null` contradiction is on the critical path, not hypothetical — it goes
live with a source that is actually collecting the moment Phase 6/7 reads `group`.

**Plan: 1 CRITICAL + 5 MAJOR in the brief. All fixed 2026-08-18. The brief was wrong, the
config was not.**

1. **CRITICAL — the gate's two clauses were mutually exclusive.** It demanded "≥200 items
   across ≥8 of 10 sources", but `max_items_per_source: 20` × 9 + `state_dept_travel`'s
   `max_items: 30` = a post-cap ceiling of **210**. Losing one source to ordinary flake
   costs 20 → 190 → fails the floor, so the ≥8/10 tolerance could never fire. A *correct*
   implementation fails on an average day, and the pressure resolves the worst way: raise
   the cap to pass. Floor is now **160** (8×20). The ~328 figure is **pre-cap** and was
   being quoted as a gate number in two files.
2. **MAJOR — "non-null `published_at`" was passed by the exact bug the brief forbids.**
   Stamping `datetime.now()` on every Ynet/telegram item satisfies it verbatim. Gate now
   requires every timestamp to predate workflow start and rejects a source whose items all
   share one identical timestamp.
3. **MAJOR — nothing machine-checked the gate.** `run_send_test` deliberately always exits
   0; an inherited pattern turns a red result into a green tick, and a count table a
   non-developer eyeballs is not a gate. The workflow must assert and exit non-zero.
4. **MAJOR — the gate was unrunnable from the deliverables.** `run.py` parses only
   `--dry-run`/`--send-test` (lines 64–65) and there is no collect workflow, yet the gate
   required `--collect-only` from `workflow_dispatch`. The file table omitted `run.py`, the
   workflow, and the `settings.yaml` UA edit req 2 itself demands — i.e. collectors nothing
   invokes. Added.
5. **MAJOR — `respect_robots_txt: true` (settings.yaml:46, schema-enforced at
   settings_schema.py:28) is live config the brief never mentioned.** No probe round ever
   fetched a robots.txt, so five rounds of verdicts are silent on it, and it is incoherent
   with req 2's deliberate browser UA. Now a BLOCKING owner decision in the brief, §2b.
   **This was the highest-risk item and it was on no list anywhere.**
6. **MAJOR — the telegram forward-from header is destroyed by "strip HTML to text" and is
   unrecoverable.** It is the only fetch-time-only datum in the phase: `t.me/s/` shows ~20
   posts, so once a post scrolls off, re-fetching cannot get it back. LEAD_HANDLING rev 2's
   evidence-computed `G` depends on forward-from collapse; losing it forces the fallback to
   config `group:` — the exact fabricated-signal path above. Fix costs no `Item` field:
   preserve as a `"Forwarded from X: "` body prefix.

Also specified because they were absent, each a build round: streamed 400 KB cap (not
`resp.content`, which buffers first) plus a **wall** deadline (requests' timeout is
between-bytes — one byte per 9s under a 10s timeout never fires, and per-host serialisation
then queues every sibling behind it, ending at GitHub's 6h kill); explicit charset
precedence (windows-1256 is live for Arabic; a wrong decode is silent mojibake into
embeddings and prompts plus an unstable `raw_hash`); zero-parse distinguishable from empty
(30 items, 0 parsed must not read as an empty feed — `degraded_after_empty_runs: 3` needs
it); truncation by `published_at` desc (feeds are not reliably date-sorted, so "first 20 of
87" can drop today's State Dept advisory); `raw_hash` over **raw bytes, pre-normalisation**
(post-strip hashing means changing a strip rule invalidates every stored hash); and the
join check explicitly covering `enabled: false` entries.

Two false claims in the brief, corrected: the undeclared-gzip diagnosis for the three
0-item feeds was a **hypothesis that ci4 falsified** (decode shipped in `29d116e`, all
three still EMPTY, still `NEEDS_BODY_DUMP`) — a wrong first guess to hand an Implementer;
and `ynet`/`ynet_he` are **different hosts** (`ynetnews.com` / `ynet.co.il`), so they are
not a per-host serialisation case. `ARCHITECTURE.md` line 404 said the Phase 3 gate is
"200 real items collected **locally**" — impossible from Iran, now corrected to the CI
assertion.

**Standing lesson: the config was verified by joins and held; the brief was verified by
reading and did not.** Prose with numbers in it needs the same mechanical check as YAML.

## Verified facts (session 3, 2026-08-12)

- **The "server" question is closed. There is no server.** GitHub Actions US runners do all
  fetching, so Telegram's filtering inside Iran is irrelevant to collection, and no host,
  domain or card is ever purchased. The owner touches the system only via a browser to
  paste secrets. Do not reopen this.
- **The local dev sandbox has no PyPI access and no general network egress.** Proxy returns
  403 on pypi.org / files.pythonhosted.org (no root for apt), and the egress allowlist
  contains exactly one host, `api.metisai.ir` — a live RSS feed fetch was refused. Retried
  2026-08-12, still blocked; this is policy, not a glitch. Consequences for every phase:
  agents cannot `pip install`, cannot run `pytest`, and cannot test collectors against live
  feeds. Tests must be written as real pytest files, their assertions additionally verified
  by exercising production functions through a stdlib-only harness, and collectors must be
  testable against **saved fixture files**, never live HTTP.
  Already present in the sandbox and usable offline: **PyYAML 6.0.3, requests, bs4, numpy**,
  Python 3.10.12, stdlib `unittest`. That covers config, HTTP client, HTML parsing and
  cosine clustering — so phases 3/4/6 are still developable here.
- **The owner can run the real gate himself on Windows** (Python installed 2026-08-12,
  repo is on his disk). Escalate to him for any true `pytest` run. Do not let an agent claim
  a green suite it never ran — this is now cheap to falsify.
  **The owner's shell is PowerShell, not cmd.exe.** `set PYTHONPATH=src` is cmd syntax and
  fails SILENTLY in PowerShell — it sets nothing, and every test module then dies at import
  with `ModuleNotFoundError: No module named 'agent'` before a single test runs. That
  happened 2026-08-18 and cost a gate round: 16 collection errors that looked like a Phase 3
  break but hit `test_budget`/`test_config`/`test_telegram` too, i.e. modules that had
  already passed 100/100. **Diagnostic rule: if ALL test modules fail at import, it is the
  path, not the code.**
  Correct invocation, and the one to use from now on: **`pip install -e ".[dev]"` once**,
  then plain `python -m pytest -q` and `python -m agent.run --dry-run` with no env var.
  This is exactly what `ci.yml:20` and `collect-test.yml:33` do (`pip install -e .`), so
  local and CI stop diverging. PowerShell fallback if the install is ever unavailable:
  `$env:PYTHONPATH = "src"`. Never write `set PYTHONPATH=src` in an instruction to the owner.
- `config/sources_candidates.csv` written 2026-08-12 (38 rows: 11 tier-1, 13 tier-2,
  10 tier-3, 4 lead; 35 RSS, 3 Telegram, 4 UNVERIFIED). **Candidates only, not pruned, not
  yet `sources.yaml`.** Telegram coverage is thin at 3 — the owner is supplying his own
  channel list, which is where that gap closes.
- **A feed probe measures the network it runs on, not the feed.** Local probe 2026-08-13
  from the owner's PC in Iran: 38 rows → OK 16, HTTP_ERROR 8, NOT_FEED 3, **TLS 11**.
  The 11 TLS failures returned no HTTP status at all — handshake never completed. That is
  Iran's filtering (Reuters, BBC ×3, AFP, Tasnim, PressTV, Tehran Times, Mehr ×2,
  Financial Tribune), not dead feeds, and the pipeline never runs from Iran. **The
  asymmetry runs both ways: IRNA English answered 200/30 items from Tehran and may
  geo-block a US runner.** Rule: `--tag ci` (GitHub US runner) is the only verdict that
  decides `sources.yaml`; `--tag local` is kept because a disagreement between the two is
  itself the finding. Never cut a source on a TLS/DNS/TIMEOUT verdict.
- **11 of 38 candidate URLs are simply wrong** (server answered, feed absent): 404 on AP
  hub, BBC Persian, Al Jazeera English, Al Jazeera Middle East, Al-Monitor Iran, Gulf News;
  503 AFP latest; 403 Trading Economics (UA block, may be recoverable); HTML-not-XML on
  Radio Farda `/rssfeeds`, GlobalSecurity `.htm` (an HTML page mislabelled `type: rss`),
  IRNA `/rss/politics` (bad subpath — IRNA `/rss` works). IranWire returns 311 items but
  newest is 2026-08-02, i.e. stale. The scout wrote these URLs with no network access;
  treat the whole CSV as unverified until a `ci` probe says otherwise.
- **`sources_candidates.csv` does not join to `credibility.yaml`.** The CSV keys on `name`
  ("Reuters World"); credibility.yaml keys on id (`reuters`, `bbc_en`, `tg_flight_osint`).
  ~27 of 38 CSV rows have no credibility entry, and **19 of the 30 credibility sources have
  no CSV row at all** — including every tier-1 mechanical feed (UKMTO, CENTCOM, IAEA,
  safeairspace, ISW, FRED, Stooq) and all 4 OSINT Telegram slots. A missing id does not
  error; it falls through to `defaults: tier 3`, which would score a Reuters wire report as
  an anonymous channel. `sources.yaml` therefore carries an explicit `id` per entry that
  must already exist in `credibility.yaml`. Schema + 3 worked entries:
  `config/sources.yaml.example`.
- **`signals_covered` is OPTIONAL in v1**, against the earlier line in File map. Hand-mapping
  33 signals (A1–G2) across ~40 sources is hours of owner time for the scoring half that
  session-3 decision 1 removed from v1, and it would block Phase 3 for nothing. The startup
  coverage check ships disabled and turns on in Phase 7.

## Verified facts (session 4, 2026-08-16) — probe round 2

- **A 403 is not a wrong URL, and reading it as one nearly cut six good sources.** 9 of the
  15 round-2 "broken" rows were 403 Forbidden, including **both** URL variants for ISW and
  **both** for UKMTO. Two different paths on one host returning the same 403 is a host-level
  bot filter, not a bad guess. Cause: the probe UA was
  `Mozilla/5.0 (compatible; news-curator-feedcheck/1.0)` — the `compatible;` crawler form
  Cloudflare rejects. Classify by **status code**, never by the `broken` bucket the script
  prints: `403` → retest with real headers, `404`/`NOT_FEED` → URL genuinely wrong.
- ~~**The header fix belongs to the collector, not the probe.**~~ **FALSIFIED by ci4,
  2026-08-16.** The full browser UA + per-host serial + gzip decode shipped in `29d116e`
  and changed **0 of 15** target rows: all 7 403s still 403, all 3 EMPTY still EMPTY, both
  amwaj still 500/429. Only diffs vs ci3 were flake (`mee` EMPTY→OK 20, `centcom` OK 25→
  TIMEOUT, `state_dept_travel` 125→87 items). ci4 provably ran the new code — artifact
  written 14:30 UTC, push landed 14:28 UTC. **Therefore the block is IP-level, not
  header-level:** GitHub Actions runs on Azure datacenter ranges that Cloudflare/Akamai
  reject regardless of headers. No header, cookie or retry in `rss.py` will fix it. The new
  UA/serial/gzip code is still correct and stays — it just is not the cure. Do not spend a
  fifth probe round on request-shaping.
- **The 429 was self-inflicted.** `amwaj.media/feed` → 500 and `amwaj.media/rss` → 429
  because 8 workers probed both variants of one host at once. Every a/b variant pair shares
  a host by definition, so this corrupted exactly the rows hardest to read. Fixed: probe
  buckets by host, serial within a host, parallel across hosts.
- **EMPTY at HTTP 200 with a feed content-type is usually undeclared gzip.** SafeAirspace
  returned `application/rss+xml`, Radio Farda `text/xml`, both 0 items. urllib does not
  decode gzip it did not ask for, so the body stays binary and the item regex misses.
  `fetch()` now decodes it. Do **not** send `Accept-Encoding: gzip` — the 400 KB partial
  read cannot be decompressed.
- **Tier-1 mechanical sources confirmed alive from CI: CENTCOM** (the
  `DesktopModules/ArticleCS/RSS.ashx` variant, 25 items — `centcom_alt` 403s, delete it)
  **and State Dept Travel Advisories** (82 items). ISW, UKMTO, SafeAirspace, IAEA still
  unresolved pending ci3.
- **Reuters and AP are only reachable via the Google News `site:` proxy** (100 items each,
  fresh). Marked `USE_CAVEAT`: items are Google snippets, not full text, which is thin for
  embedding/clustering, and the endpoint can break without notice. Decide before Phase 6.
- **IranWire cut on staleness, not reachability.** It is not geo-blocked — round 1 timed out
  transiently. The corrected `iranwire.com/en/feed/` returns 493 items but newest is
  2026-07-13, 31 days old. Caveat: `DATE_RE` takes the *first* `pubDate` in the body, which
  is only the newest if the feed is sorted — verify before cutting a source on date alone.
- **Press TV and Tasnim marked `CUT_UNREACHABLE_CI`, deliberately against the "never cut on
  TLS/DNS" rule.** That rule exists to stop a feed being killed because *Iran* blocks it.
  Here the runner cannot reach them in either round, on two different URLs each. CI is the
  pipeline's own network; unreachable there is unusable. Not a blanket US-IP block on
  Iranian media — IRNA, Mehr and Tehran Times all answer fine from CI. Owner may overrule.
- **Sandbox egress is still blocked** (proxy returns `Tunnel connection failed: 403` for
  every host, browser UA included). Retested 2026-08-16. No agent can verify a feed URL
  from here; only a CI run decides. Unchanged since session 3.

## Verified facts (session 5, 2026-08-17) — Telegram probe + live delivery

- **`tg1` probe: 20 of 21 owner channels are readable via `t.me/s/`** (7–20 posts each).
  Verdict file `config/sources_probe_merged_tg1.csv`. Only cut: `tg_parvaz_capital`,
  HTTP 200 with **zero posts** — web preview disabled or join required. Constraint 6
  forbids MTProto, so there is no path to it. Not a URL error, do not retry.
  `osint613.com/feed` works (70 items) and replaces the uncollectable @Osint613 Twitter
  account; `/rss` and `/feed.xml` 404.
- **A `_Bot` handle cannot be collected.** Owner offered `@FlightAlerts_Bot` for the
  reserved `tg_flight_osint` slot. Bots have no `t.me/s/` preview page, so there is
  nothing to fetch, and MTProto is forbidden. **Airspace is now the largest coverage
  hole** — safeairspace is CUT_BOT_BLOCKED and this slot stays a placeholder.
- **The probe returns an empty `newest` for every Telegram row.** The preview page has no
  `pubDate`; timestamps live in a `<time datetime=...>` attribute. The Phase 3 collector
  must parse that — the 30-minute near-duplicate window in `LEAD_HANDLING.md` depends on
  real post times.
- **`credibility.yaml` backfilled and validated 2026-08-17.** 53 entries, 26 `tg_`,
  8 `lead`, 0 invalid tiers. Verified by loading through the real `agent.config.load_all`,
  not by eye. **`defaults.group` changed `null` → `unlisted`:** null resolved to "own id",
  so every unlisted source counted as fully independent and twelve reposts of one original
  would have registered as twelve confirmations — DECISION 4's exact failure arriving
  through the defaults block. Unlisted sources now share one group and cannot corroborate
  each other. `tg_ukmto_mirror` carries `group: ukmto` for the same reason.
- **Live Telegram delivery CONFIRMED from a US runner 2026-08-17** via new
  `.github/workflows/send-test.yml`. Owner's local `getMe` returned 404 on three separate
  fresh tokens because `api.telegram.org` is filtered from Iran — **his machine cannot
  test any Telegram credential and never will.** CI is the only valid test bed. The
  workflow greps its own log and fails on `send failed` or `mock mode`, because
  `run_send_test` deliberately always exits 0.
- **A private channel's ID cannot be read in the Telegram app and `getUpdates` is
  unreachable from Iran.** Working method: forward one channel message to `@JsonDumpBot`
  and read `forward_from_chat.id`. Entirely in-app, no API, no VPN.
- Owner's secrets were initially invisible to Actions — they had not been saved as
  **repository** secrets under Settings → Secrets and variables → Actions → Secrets.
  Resolved. `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHANNEL_ID` confirmed working.
- **Composition gaps, from adversarial review:** zero Arabic and zero Hebrew sources
  against the project's own en/fa/ar/he spec. `config/sources_candidates_r5.csv` (13 rows:
  3 owner Telegram + 6 Arabic RSS + 4 Hebrew RSS) addresses it.
- **`tag=r5` RUN AND MERGED 2026-08-17. The ar/he hole is closed.** 13 rows → OK 10,
  HTTP_ERROR 3. Raw `config/sources_probe_r5.csv`, verdict
  **`config/sources_probe_merged_r5.csv`**: USE 9, USE_CAVEAT 1, CUT_BOT_BLOCKED 3.
  **10 of 13 blind URL guesses were correct** — a far better hit rate than round 1's 11-of-38
  wrong, because these were guessed against known publisher CMS conventions rather than
  invented. Confirmed a CI verdict, not a local one: the candidates CSV is committed so the
  runner checked it out, and ynet/walla/maariv answered 200, which never happens from Iran.
  New live: `ajar` 25, `sky_news_arabia` 66, `al_manar` 10, `aawsat` 300, `ynet_he` 30,
  `walla` 30, `maariv` 20, `tg_tanker_trackers` 17, `tg_ukmto_mirror` 20, `tg_mornovosti` 14.
- **Cut on 403: `al_arabiya`, `al_mayadeen`, `israel_hayom`.** Classified CUT_BOT_BLOCKED per
  the session-4 rule, not URL_WRONG. Weaker evidence than ISW/UKMTO had (one URL variant
  each, not two) but ci4 already falsified header-shaping, so a sixth round buys nothing.
  Editorial coverage survives the cuts: Gulf counterweight via `aawsat` + `sky_news_arabia`,
  resistance-axis framing via `al_manar`, three Hebrew feeds remain. **Only the right-leaning
  Israeli line has no substitute left.**
- ~~**`tg_ukmto_mirror` is now the only live path to UKMTO advisories**~~ — **SUPERSEDED
  2026-08-19 by Status finding (g2): the mirror is DORMANT, newest post 2026-07-14, 36 days
  stale. Maritime coverage is ZERO, not single-sourced.** Both official URLs remain
  CUT_BOT_BLOCKED. Original note kept for the reasoning: it is tier 3 and carries
  `group: ukmto`, so even when it was live it could never corroborate UKMTO or score.
  The staleness was invisible because the probe emits an empty `newest` for every Telegram
  row — see (g2). Owner decision open: cut per the IranWire precedent, or keep as a
  placeholder. Either way maritime is an open coverage hole and must be recorded as one.
- **56 unique usable sources** (r4 28 + tg1 21 + r5 10, minus 3 ids appearing in both r4 and
  tg1). Against a 10-source thin slice that is 5.6x. **The source problem is now oversupply,
  not scarcity** — see the volume risk below.
- **VOLUME RISK, new and unbudgeted.** Constraint 2 caps ~40 LLM calls/run and the LLM sees
  25–40 clusters. 56 feeds at observed item counts is **1,984 items per full sweep**; at
  10–15% new per 3-hour run that is **~200–300 items** to cluster into ≤40 clusters.
  `aawsat` alone returned **300 items** — 15% of the entire corpus from one general pan-Arab
  daily, not a security wire. It needs a topic prefilter before Phase 6 or it dominates the
  cluster budget by itself; marked USE_CAVEAT for that reason. Next worst: `dw` 137,
  `wotr` 100, `reuters_gnews` 100, `ap_gnews` 100, `state_dept_travel` 87.
- **`credibility.yaml` extended and re-validated 2026-08-17: 73 entries** (53 → 60 for r5,
  → 73 after the gap below), 0 invalid tiers, tier dist 1:9 / 2:34 / 3:22 / lead:8. Verified
  by loading through the real `agent.config.load_all`, not by eye. r5 assignments: `ajar`
  tier 2 `group: al_jazeera`; `sky_news_arabia` tier 2 own group; `aawsat` tier 2
  `group: saudi_press`; `al_manar` **tier 3** (party organ, not a newsroom — nominates,
  never corroborates); `ynet_he` / `walla` / `maariv` tier 2 `group: israeli_press`.
- **DEFECT FOUND AND FIXED 2026-08-17 — 13 usable sources had no `credibility.yaml` entry
  and were silently resolving to `defaults: tier 3, group unlisted`.** This voids the earlier
  claim in this file that the round-2 backfill was complete; it was not, and eyeballing the
  file would never have caught it. Affected: `reuters_gnews`, `ap_gnews`, `bbc_en_me`,
  `haaretz`, `mehr`, `dw`, `france24`, `npr`, `cnbc`, `mee`, `wotr`, `oilprice`,
  `seeking_alpha` — i.e. Reuters, AP, BBC Middle East and Haaretz were all being scored as
  anonymous channels at once. **Root cause is the session-3 name-vs-id finding recurring in a
  new form:** the probe CSVs invent per-URL ids (`reuters_gnews`, `bbc_en_me`) that do not
  match the newsroom ids in `credibility.yaml` (`reuters`, `bbc_en`). A missing key does not
  error, it degrades.
  **Standing rule, now proven twice: never verify this file by reading it. Join every USE row
  across all merged probe CSVs against the loaded keys and assert the difference is empty.**
- **All six Israeli outlets share `group: israeli_press`, deliberately.** Consequence: an
  Israel-sourced event can never clear `min_independent_sources: 2` on Israeli reporting
  alone and stays RUMOUR until a non-Israeli group confirms. Correct on the merits — on
  military matters these outlets relay the same IDF Spokesperson copy under the same military
  censor, so treating them as independent would manufacture confirmations. Accepted cost: the
  Israeli civilian press leads international wires by 30–120 min on home-front events
  (impact sites, mobilisation, hospital surge), and those now wait for pickup.
  **`haaretz` was the sixth and was missing** — had it stayed unlisted it would have landed in
  group `unlisted`, a *different* group, so `haaretz` + any one of the other five would have
  cleared the two-group bar on Israeli press alone and voided this entire grouping. The
  documented consequence was only true by accident until 2026-08-17.
- **UNRESOLVED CONTRADICTION, surfaced 2026-08-17, currently inert — owner must decide.**
  Two blocks of `credibility.yaml` written the same day take opposite positions on the same
  question. The `defaults` block says an unknown source shares `group: unlisted` because
  "being unknown must fail toward silence, not toward confidence." The tg1 block sets
  `group: null` on 17 listed channels, and null resolves to own-id = fully independent,
  which fails toward confidence. **Inert today** because `tier3_can_corroborate: false` and
  all 17 are tier 3 or `lead`, so none can satisfy Step 1 regardless of group. **More inert
  than first written: no code consumes `group` at all yet** — grep of `src/agent/` finds it
  only in `config.py` (load, validate, store) — so this is currently a contradiction between
  two comments, and "null resolves to own id" is itself only a comment, not code. Two effects
  once Phases 6–7 build: (a) the RUMOUR/watchlist display would count one rumour echoing
  across five channels as five groups, which the tier-3 section header of that same file says
  groups exist to prevent; (b) it is the fallback path when LEAD_HANDLING rev 2's
  evidence-computed `G` fails to detect a paraphrased repost with no forward header.
  Cheapest fix is therefore one line in the future loader (apply `defaults` per-field for
  listed sources), not 17 edits. **It becomes a live
  fabricated-signal path the moment any of the 17 is promoted to tier 2** — and the file
  records that the owner already wanted exactly that for `tg_exciton_missile` and
  `tg_rnintel`, both overridden to 3. Options: leave as-is; give the 17 a shared
  `unlisted_tg` group; or make the loader apply `defaults` per-field for listed sources.
  Not changed unilaterally — the `group: null` choice was argued explicitly last session.

## Resolved items (moved from CLAUDE.md Pending, 2026-08-19)

- [x] Phase 2 built and gate-green 2026-08-13. See Status. Three review rounds, 2 CRITICAL
      + 3 MAJOR + 1 new-defect-inside-a-fix + 1 dependency-layering break found after the
      builder's suite was green. The loop is load-bearing; do not shorten it for a later phase.
- [x] **Repo IS on GitHub — `origin` = `github.com/mmousavi93-bit/news-curator`, public,
      2 commits (`3121ad4` Phase 1+2, `7622414` probe r1 merge + round-2 candidates).**
      Supersedes the earlier "no git repo exists" line. Steps 1–4 below are done.
- [x] **Probe round 1 complete and merged, 2026-08-13.** `config/sources_probe_merged_r1.csv`,
      38 rows: **17 USE, 12 URL_WRONG, 8 DEAD_OR_BLOCKED, 1 GEOBLOCKED_FROM_CI**.
      CI verdict distribution: OK 18 / HTTP_ERROR 8 / DNS 7 / NOT_FEED 4 / TLS 1.
      Confirmed by a second identical CI run the same day — only IranWire changed
      (TIMEOUT → OK 311 items), so it is **not** geo-blocked, it timed out transiently.
      Cut it on **staleness** instead: newest item 2026-08-02, 11 days old. Round-2 already
      carries the corrected `iranwire.com/en/feed/` URL; that one decides.
      **Reuters and AP are DNS-dead from CI, not blocked — both killed public RSS.** The only
      candidate paths are the Google News `site:` workarounds in round 2; licence-check
      before shipping either.
- [x] **Probe round 2 run 2026-08-16, `tag=ci2`.** 47 rows: OK 30 / HTTP_ERROR 11 /
      EMPTY 3 / NOT_FEED 1 / DNS 1 / TLS 1. Raw at `config/sources_probe_ci2.csv`,
      classified at **`config/sources_probe_merged_r2.csv`** (decision, id, http, why, url).
      Decisions: USE 27, USE_CAVEAT 2, RETEST_UA 7, INSPECT_BODY 3, RETEST_SERIAL 2,
      URL_WRONG 3, CUT_UNREACHABLE_CI 2, CUT_STALE 1.
- [x] **Line-ending churn fixed.** `.gitattributes` (`* text=auto` + per-type `eol=lf`)
      committed in `c72cc9e`. Probe diffs are readable again.
- [x] **Probe rounds 3 and 4 run 2026-08-16.** ci3 was wasted — a stale `.git/index.lock`
      on Windows silently blocked the commit, so CI ran the old script (`git push` said
      "Everything up-to-date"). Lesson: `HEAD == origin/main` proves nothing; verify the
      **fix is inside origin** — `git show origin/main:<file> | findstr <marker>`.
      ci4 ran the real fix and falsified the header hypothesis (see session 4 facts).
      Merged verdict: **`config/sources_probe_merged_r4.csv`** — USE 26, USE_CAVEAT 2,
      CUT_BOT_BLOCKED 7, NEEDS_BODY_DUMP 3, NEEDS_URL_SCOUT 3, CUT_SERVER_ERR 2,
      CUT_UNREACHABLE_CI 2, CUT_STALE 1, RETEST_FLAKY 1.
- [x] **Probe round 5 run and merged 2026-08-17, `tag=r5`.** Closes the Arabic/Hebrew hole.
      13 rows → USE 9, USE_CAVEAT 1, CUT_BOT_BLOCKED 3. See session-5 facts.
      ~~**Source discovery is now CLOSED at 56 usable feeds. No further probe rounds.**~~
      **One narrow exception granted 2026-08-19: round r6, MARAD MSCI only, because the
      UKMTO mirror died and maritime coverage went to zero. See the r6 item below. Not a
      general reopening.**
- [x] Owner supplied his Telegram handles (tg1, 21 usable incl. `lead`) and the r5 additions.
      Supersedes the old "owner to paste his own channel handles" and "initial 40-source list
      not compiled" items — the list is oversupplied, not missing.
- [x] `credibility.yaml` backfilled for round-2 and r5 ids. 60 entries, validated through
      `agent.config.load_all` 2026-08-17.
- [x] **`config/sources.yaml` WRITTEN 2026-08-17. 51 sources staged, 10 enabled.**
      Generated from the merged probe verdicts, not hand-typed. Validated: join rule holds
      (every id exists in `credibility.yaml`), all urls https, telegram urls all `/s/` form,
      no `signals_covered`. The thin slice deliberately spans every axis the collectors must
      handle — rss + telegram, en/fa/ar/he, tier 1/2/3/lead — and includes two known-broken
      sources on purpose (`ynet_he` for the `DATE_RE` miss, two telegram feeds for the
      missing `pubDate`) so Phase 3 is forced to fix them:
      `state_dept_travel`(1,en) `bbc_en_me`(2,en) `irna`(2,en) `bbc_persian`(2,fa)
      `ajar`(2,ar) `ynet_he`(2,he) `the_war_zone`(2,en) `tg_militarywave`(3,en)
      `tg_ukmto_mirror`(3,en) `tg_padeshah_fxn`(lead,fa) — ~328 items/sweep, clears the
      200-item gate. **Do not enable more before Phase 8.**
- [x] **`agents/briefs/PHASE_3_BRIEF.md` written 2026-08-17.** Ready for an Implementer.
- [x] **`respect_robots_txt` RESOLVED 2026-08-18 → `false`.** Owner delegated the call.
      Reasoning is written into `settings.yaml` inline and summarised in
      `PHASE_3_BRIEF.md` §2b; it is not "we ignore robots.txt", it is that this client is a
      feed reader, not a crawler — fixed curated URL list, no link discovery, no traversal,
      RSS endpoints published expressly for machine reading, and a browser UA that says so.
      **The obligation moved rather than vanished, and the replacement is binding:** 20
      items/source, ≥3h interval, per-host serialisation, and **a 403/429 is a definitive
      refusal — mark the source dead, never retry with different headers, a different path,
      a proxy, or an archive mirror.** Already demonstrated behaviour: ci4 cut seven sources
      instead of working around them. Rejected honour-everywhere (sixth probe round,
      reopens closed source discovery, drops the UA that earned the verdicts) and rejected
      the honour-only-for-`t.me` split (adds a branch plus a per-host robots request on the
      one host carrying 23 of 51 sources, so a robots timeout on a CI runner either kills
      all Telegram collection or falls open — the same setting with extra failure modes).
      `fetch.py` implements no robots fetch.
- [x] **Phase 3 gate PASSED 2026-08-19 14:55 UTC — `174 passed` local + `GATE PASSED` in CI,
      `total_kept: 204`, 10/10 sources. See Status finding (f). Phase 3 is closed.**
      The `t.me/s/` selector question is answered (they match the live page) and the Ynet
      date question is answered (finding (e)). Both are off the open list.
- [x] **DONE 2026-08-19 15:08 UTC — gate hardened and the hardening itself gate-tested.**
      Conditions 5 and 6 shipped and the first fresh dispatch failed on **exactly** the two
      predicted sources and nothing else: `state_dept_travel: 30 items kept but every
      published_at is null` and `tg_ukmto_mirror: newest item 2026-07-14T14:59:57+00:00 is
      36 days old`. Predicted red, came back red, correct two lines — the assertions were
      attacked with the bugs they exist to catch before being trusted, per the standing
      lesson. Also pre-verified locally against four fixtures (real defect shape, both
      repaired, exactly-14-days, 15-days) by extracting the script back out of the YAML
      rather than reading it.
      **`collect-test.yml` is now RED ON `main` BY DESIGN.** It is tracking two known open
      defects, g1 and g2 below. It is not a Phase 3 regression — Phase 3 is closed and its
      own gate passed at 14:55 UTC. Do not "fix" the red by relaxing conditions 5 or 6.
- [x] **RESOLVED 2026-08-19 — `tg_ukmto_mirror` DISABLED, MARAD MSCI staged to replace it.**
      Owner chose substitution over repair. **The staleness was NOT a quiet-event-feed false
      positive, which was the live alternative hypothesis and was checked before acting.**
      UKMTO published continuously through the dormant window: WARNING 080-26 (tanker hit
      8NM E of Limah, 6 Jul), the 20 Jul Houthi blockade declaration against Saudi-affiliated
      shipping, an explosion 95nm SE of Aden 5 Aug, WARNING 110-26 (cargo vessel struck off
      Al Mokha, 11 Aug), JMIC advisory updates 082/083, and the 14 Aug VRA overview assessing
      Hormuz SEVERE. **The mirror's last post is 2026-07-14 — the same day the US naval
      blockade of Iranian ports took effect at 2000Z.** It went dark on the day the maritime
      picture became most active, while still returning 20 well-formed items.
      **This is the strongest argument yet for the staleness check: it would have fired on
      2026-07-28, three weeks before the defect was found by hand.** First real firing is a
      true positive on a tier-1 blind spot, not a nuisance failure.
      Changes: `sources.yaml` → `enabled: false` with the full reasoning inline, row kept so
      a revival is one flag; **`report.py` `REQUIRED_SOURCE_IDS` cut from four ids to three**
      — leaving it in would have made the gate demand items from a source deliberately
      switched off, i.e. a permanent red with no defect behind it. Two telegram ids still
      force the `<time datetime=...>` parsing the list exists for. Enabled sources 10 → 9,
      expected `total_kept` ~184 against the 160 floor, so the floor still tolerates one
      source flaking.
      **`credibility.yaml` now has `marad_msci` (74 entries, tier dist 1:10/2:34/3:22/lead:8,
      validated through `agent.config.load_all`, join to `sources.yaml` empty-diff).** Entered
      before it is ever collected, deliberately — the 2026-08-17 defect was 13 sources
      silently degrading to `defaults: tier 3` because a missing key does not error.
      `group: us_govt`, matching the existing `state_dept_travel`/`centcom` convention.
      **Recorded open risk rather than silently created:** MSCI relays UKMTO/JMIC copy, so if
      a UKMTO path is ever restored, `marad_msci` + `ukmto` would read as two independent
      groups on partly identical copy — the DECISION 4 failure. Not put in `group: ukmto`
      because MARAD also carries independent US Navy/ONI assessment. Inert today: no UKMTO
      path exists. Decide at Phase 7.
- [x] **r6 RUN 2026-08-19. All 4 MARAD urls FAILED. Verdicts in
      `config/sources_probe_merged_r6.csv`: CUT_BOT_BLOCKED 3, RETEST_ACCEPT 1.**
      **Zero 404s** — so read by status code, per the session-4 rule: the three DOT paths
      (`maritime.dot.gov/msci-advisories/rss.xml`, `maritime.dot.gov/rss.xml`,
      `marad.dot.gov/rss.xml`) all returned **403**, the same multi-path signature as ISW
      and UKMTO, which ci4 established is IP-level rejection of Azure ranges and NOT
      fixable by request-shaping. The urls may be perfectly correct. Do not spend a round
      on headers.
      **Sharpens the session-4 finding: it is not "US government blocks Azure".
      `travel.state.gov` answers 200 from the same runner. The filter is DOT-wide.**
      **`marad_govdelivery` returned 406 Not Acceptable, which is a different animal** —
      the server parsed the request and refused the media type, i.e. content negotiation,
      not a bot block and not a wrong URL. GovDelivery is MARAD's documented MSCI
      distribution channel, so it is the only live thread left. One retest with
      `Accept: */*` via `dump-body.yml --accept`, which is why that flag exists.
      **Maritime remains at ZERO.** If the 406 retest also fails, MSCI has no
      machine-readable route from a GitHub runner and the realistic remaining options are
      a Google News `site:` proxy (the existing USE_CAVEAT pattern) or an owner-supplied
      replacement mirror channel. Record it as an open hole rather than pretending
      otherwise — that is the mistake g2 already cost.
- [x] ~~Probe round r6 — `config/sources_candidates_r6.csv`, 4 MARAD variants, ALL UNVERIFIED.~~
      **This reopens source discovery, which session 5 declared CLOSED at 56 feeds.** The
      exception is deliberate and narrow: a tier-1 domain went to ZERO coverage during an
      active maritime conflict, which is not the same as wanting more feeds. Do not treat it
      as licence for a general round.
      **No MARAD RSS endpoint is documented anywhere** — MSCI is distributed by GovDelivery
      email and NGA broadcast. All four urls are CMS-convention guesses (Drupal view feed,
      site-wide Drupal, GovDelivery per-account bulletins, alternate MARAD host), i.e. the r5
      class that hit 10 of 13, not the r1 class that missed 11 of 38. Still guesses.
      `marad_msci` and `marad_msci_b` share a host — probe serially, per the self-inflicted
      429 lesson. If all four fail, MSCI has no machine-readable route and maritime stays at
      zero until a replacement mirror channel is found.
      Optional one-row add-on, not yet staged: retest `ukmto.org` on the **static** path class
      (`/-/media/ukmto/products/*.pdf`). ci4 proved host-level 403s but only on `/feed` and
      `/rss`, both dynamic; static assets are sometimes served under different rules. Low
      odds, one row, and it is the only route to the primary source.
- [x] ~~build Phase 3 from the brief~~ (revised 2026-08-18 after the Fable
      review: 1 CRITICAL + 5 MAJOR fixed). Collectors only. Gate is owner-run pytest on
      Windows plus a `--collect-only` CI run asserting **≥160 post-cap items**; the owner's
      Iran network cannot verify most of these feeds and would give a false verdict.
      Deliverables now include `run.py --collect-only`, `.github/workflows/collect-test.yml`
      and the `settings.yaml` UA edit — without them the gate cannot be run at all.
- [x] **RESOLVED 2026-08-19 by the failed CI gate — the Ynet date premise below was WRONG.**
      `DATE_RE` matches Ynet fine and the date parses fine; the feed declares GMT and emits
      Israel local time. See Status finding (e). Kept below for the record because the wrong
      premise shaped `_YNET_DATE_RE`, which turned out to be dead code against the live feed.
      ~~**Still open from the original item: the `t.me/s/` `<time datetime=...>` parsing is
      UNVERIFIED**~~ — **CLOSED 2026-08-19 by the green gate run.** All three Telegram
      sources returned real, distinct, plausible post times, so the class names and the
      `<time datetime=...>` selector match the live page. Remove the UNVERIFIED note from
      `telegram_web.py`'s header comment. Nothing about Ynet or `t.me` date parsing is open.
- [x] **Untracked strays — already gone, item was stale.** Checked 2026-08-17: no CSV in the
      repo root, no `config/sources_probe_sandboxnet.csv`. All 12 probe CSVs are in `config/`.
- [x] **The agent VM CAN delete files in the mounted folder** — it needs delete permission
      granted once per folder, which was done 2026-08-17. The earlier "cannot unlink" note was
      wrong. Relevant because `git commit` from the VM leaves stale `.git/index.lock`,
      `.git/HEAD.lock` and `.git/objects/maintenance.lock` behind, and a stale `index.lock`
      is what silently ate probe round ci3. Clear them after any VM-side commit and confirm
      with `git status -sb` before trusting the result.
      **Sharpened 2026-08-21: it is not only `git commit`. A read-only `git status` from the
      VM creates `index.lock` too** (status refreshes the index), and the VM cannot always
      unlink it — the output says `unable to unlink ... Operation not permitted`. This
      recurred live this session: the owner cleared the lock, an agent ran `git status` to
      check the remote, and the owner's next `git add` failed with "File exists".
      **Standing rule: agents run NO git command in this repo, not even status. Read state
      with `ls`/`cat`; the owner runs git.** Given this, ci3's cause was almost certainly
      the agent VM rather than Windows.
- [x] Owner installed `requests` on Windows 2026-08-13. Offline guarantee is now asserted by
      `tests/integration/test_no_requests.py`, not by the package being absent.
- [x] Warning-fatigue under sustained conflict — RESOLVED 2026-08-01: WARTIME alert regime
      (SCORING_RULEBOOK.md Step 12, ESCALATION_SCORING.md §4). Enter: TACT ≥75 for 7
      consecutive days; exit: <55 for 7 days. In-regime, daily message is a one-liner;
      full alert only on delta ≥ mean+10, category silent ≥14d firing, or STRAT tier rise.
      Scores never suppressed — message layer only. Regression-tested in backtest_weights.py.
- [x] Extraction prompt hardened 2026-08-01: AGENT_PROMPT.md catalog rewritten as per-signal
      FIRES/NOT tests + state_update field (Rule 10) for stateful posture re-confirmations.
- [~] **BLOCKER — PARTIALLY RESOLVED 2026-08-01.** Owner holds a working Gemini API key,
      AI Studio project, **no billing attached** → free tier confirmed: 10 RPM / 1,500 RPD,
      prompts may be used for training (public news content only, never personal data).
      Capacity is sufficient: 35 calls/run (10 vision + 25 understanding) × 9 runs/day =
      315 RPD vs 1,500 cap, 4.8x headroom; 3.5 min floor at 10 RPM vs the 240s budgeted for
      the LLM stage. One key is enough — a second Gemini account is not needed.
      Key handling: GitHub Secrets only. Public repo — a key in any commit is compromised
      permanently (fork network retains the blob); rotate, never delete the line.
      Residual risk is termination, not throttling: AI Studio is not offered in Iran, so the
      account may be revoked without warning. Fallback chain is the mitigation.
      Groq key obtained and proven live 2026-08-30 (carried calls #2 and #4-#14 in the
      first automated run's cascade; free-tier ceiling ~13 calls/run -- see the
      2026-08-30 cascade entry). OpenRouter and FRED still unowned. Paid providers
      remain unconfirmed and unneeded.
