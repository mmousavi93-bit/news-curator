# Phase 3 Brief — Collectors (thin slice)

Written 2026-08-17 by the Architect. An Implementer runs only from this brief.

Read first, in order: `CLAUDE.md` (hard constraints), `ARCHITECTURE.md` §2 (the volume
table) and §Implementation phases, then `config/sources.yaml`. Do not start coding until
you can state the funnel volumes from memory.

## Why this phase is third

Phases 1 and 2 proved the pipe: a run exits 0 offline, and a real message reaches the
owner's phone from a US runner. Nothing yet *collects*. This phase fetches real items and
stops. It does not store them, cluster them, or summarise them — Phases 4 and 6 do that.

Deliberately thin. `ARCHITECTURE.md` gates this phase at **10 sources, 200 real items**.
`config/sources.yaml` carries 51 sources but only 10 have `enabled: true`. **Do not enable
more.** Phase 8 is "widen sources" and exists precisely so this phase stays small.

## Deliverable

Three collectors behind one interface, plus a fetch layer they share.

### Files (all under ~200 lines — split rather than exceed)

| Path | Contains |
|---|---|
| `src/agent/collectors/base.py` | `Collector` protocol, the `Item` dataclass, shared normalisation |
| `src/agent/collectors/fetch.py` | HTTP: headers, timeout, size cap, gzip, per-host serialisation |
| `src/agent/collectors/rss.py` | RSS/Atom parsing → `Item` list |
| `src/agent/collectors/telegram_web.py` | `t.me/s/<channel>` preview scraping → `Item` list |
| `src/agent/collectors/registry.py` | reads `sources.yaml`, dispatches by `type`, aggregates |
| `src/agent/run.py` | **edit** — add `--collect-only` and the per-source count table |
| `config/settings.yaml` | **edit** — `collection.user_agent` aligned to the probe UA (req 2) |
| `.github/workflows/collect-test.yml` | **new** — `workflow_dispatch` run of `--collect-only`, asserts the gate and exits non-zero on miss |
| `tests/…` | one test module per collector, all against **saved fixtures** |

The last three were missing from the first draft of this brief. Without them the phase
delivers collectors that nothing invokes and a gate that cannot be run: `run.py` parses
only `--dry-run` and `--send-test` today (lines 64–65).

`Item` fields: `source_id`, `url`, `title`, `body`, `published_at` (aware UTC datetime or
`None`), `lang`, `raw_hash`. Nothing else. Resist adding fields Phase 4 will need.

`raw_hash` is over the **raw entry bytes, pre-normalisation** — not over the stripped
text. Hashing post-strip means every future change to the strip rules silently changes
every hash, which breaks Phase 4's dedup against already-stored rows.

## Requirements

### 1. The join rule is a startup assertion, not a convention

Every `id` in `sources.yaml` must exist as a key in `credibility.yaml`. A missing key does
**not** error today — it silently resolves to `defaults: tier 3, group unlisted`. On
2026-08-17 thirteen usable sources were in exactly that state, so Reuters, AP, BBC Middle
East and Haaretz were all being treated as anonymous channels simultaneously. Nobody
noticed by reading the file; a ten-line join found it instantly.

So: on load, assert the id set difference is empty and **raise**, naming every offending
id. This is the same shape as the `signals_covered` coverage check that ships disabled
until Phase 7 — but this one ships **enabled**. Add a test that a sources entry with an
unknown id fails loudly rather than degrading.

**Check all 51 entries, including `enabled: false` ones.** The natural code path in
`registry.py` filters to `enabled: true` before doing anything, and checking only those is
a defensible misreading of this requirement — so it is spelled out here. Enabled-only
checking means a typo in any of the 41 staged entries survives until Phase 8's flag-flip,
which the non-developer owner performs, possibly during exactly the news event the
widening exists for, after which every unattended run crashes until someone edits YAML.
All-51 keeps the two files in lockstep continuously and turns the same typo into a red
suite today. The cost — an owner edit to a *disabled* entry can halt collection — is the
right direction under constraint 14: loud beats silent. Both files validate green as of
2026-08-17, so there is no migration cost to adopting this. Include a fixture with a bad
id on a **disabled** entry.

### 2. Send the same headers the probe sent, or the verdicts do not transfer

This is the trap most likely to burn this phase. All 51 urls were verified with the full
browser User-Agent in `tools/check_feeds.py` (`UA`, line 48), after the old
`Mozilla/5.0 (compatible; …)` crawler form was found to draw Cloudflare 403s.
`config/settings.yaml` line 47 still sets
`user_agent: "news-curator/1.0 (personal research agent)"`.

**A source that answered 200 to the probe may 403 the collector.** Either align
`settings.yaml` to the probe UA, or accept per-source breakage — the first is correct.
Change the default in `settings.yaml`, keep it configurable, and note in the file that it
is load-bearing and matched to the probe.

Carry over the other two probe lessons, both already proven:
- **Decode gzip you did not ask for.** Do *not* send `Accept-Encoding: gzip` (a truncated
  gzip stream cannot be decompressed), but *do* decode a body whose `Content-Encoding`
  says gzip. Correct instruction, but **do not inherit the diagnosis**: undeclared gzip was
  the *hypothesis* for three 200-with-zero-items feeds (radio_farda, rferl_iran,
  safeairspace), the decode shipped in `29d116e`, and ci4 left all three still EMPTY. They
  remain `NEEDS_BODY_DUMP`. So gzip is a real case to handle and a **wrong** first guess
  when a feed returns 200 and no items.
- **Serialise per host, parallelise across hosts.** Probing two paths on one host
  concurrently produced a self-inflicted 429. Several ids share a host — all three BBC
  feeds and every `t.me` channel. (Not the Ynet pair: `ynet` is `ynetnews.com`, `ynet_he`
  is `ynet.co.il` — different hosts. An earlier draft of this brief said otherwise.)

### 2b. BLOCKING DECISION — `respect_robots_txt`

`config/settings.yaml` line 46 sets `respect_robots_txt: true` and
`settings_schema.py` line 28 enforces it as a bool. **No probe round ever fetched a
robots.txt**, so five rounds of verdicts are silent on whether honouring it changes
anything, and the sandbox has no egress to find out.

Do not decide this alone inside the code. The three positions:

1. **Honour it.** Then the probe verdicts do not transfer — a source that answered 200
   may still be disallowed — and it adds an unbudgeted per-host request with its own
   403/timeout/missing-file modes. It is also incoherent with req 2, which deliberately
   presents a full browser User-Agent: robots.txt governs crawlers, and req 2 already
   declares this client is not one. Would likely require a sixth probe round, which
   session 5 closed.
2. **Set it `false`** with the justification written into the file: single-user personal
   reader, ≤20 items per source, ≥3h interval, and the RSS endpoints are published for
   machine consumption. Residual risk is a block, which surfaces as a 403 and is handled
   exactly like the existing `CUT_BOT_BLOCKED` sources.
3. **Leave `true` and ignore it in code.** Rejected. A config key that claims a behaviour
   the code does not implement is worse than either real choice, and this is a public repo.

Whichever the owner picks, `settings.yaml` and the code must agree, and the comment in
`settings.yaml` must say which. Do not start coding `fetch.py` before this is answered.

### 3. Dates — two known-broken cases, both in the thin slice on purpose

- **`t.me/s/` has no `pubDate` at all.** Post times live in a `<time datetime="…">`
  attribute. Parse it. The 30-minute near-duplicate window in `analysis/LEAD_HANDLING.md`
  depends on real post times, so a `None` here quietly disables lead handling later.
- **Ynet's date format is not matched by the probe's `DATE_RE`.** Both `ynet` and
  `ynet_he` return 30 items with an empty date. `ynet_he` is enabled in this slice
  specifically so this surfaces now.

`published_at` is `None` only when the source genuinely provides nothing. Never
substitute "now" for a missing date — that silently makes stale items look fresh, and
staleness is how IranWire was correctly cut.

### 3b. Preserve the Telegram forward-from attribution — it is destroyed otherwise

This is the one piece of information that exists **only** at fetch time and cannot be
recovered later. `analysis/LEAD_HANDLING.md` rev 2's load-bearing mechanism is
evidence-computed independence: `G` = origins surviving near-duplicate collapse **and
forward-from collapse**. That attribution lives only in the `t.me/s/` HTML, and the
preview page shows roughly the last 20 posts — so by the time Phase 6 wants it, the post
has scrolled off and re-fetching cannot get it back.

Requirement 4 says strip HTML to text, which would delete it. So: the telegram collector
**preserves the attribution as a text prefix on `body`** — `"Forwarded from <name>: "`.
Text, not a new `Item` field, so the shape in §Deliverable stands.

If this is lost, Phase 6 falls back to the config `group:` values — which is precisely the
unresolved `group: null` path CLAUDE.md flags as a future fabricated-signal vector, and
`tg_militarywave` (tier 3, `group: null`) is already in the enabled 10.

### 4. Untrusted input, from the first byte

Feed bodies are hostile until proven otherwise and will eventually reach an LLM prompt.
- **Cap bytes read from the wire, not bytes already in memory.** The probe reads 400 KB;
  match or better. `resp.content` buffers the entire body before any cap can apply, so the
  cap must be enforced on a streamed read.
- **Add a per-request wall deadline.** `per_source_timeout_seconds: 10` maps to requests'
  timeout, which is *between-bytes*, not wall-clock: a server dripping one byte every nine
  seconds never times out, and per-host serialisation then queues every sibling feed behind
  it. On an unattended job that ends at GitHub's 6-hour kill. Enforce a wall deadline per
  request and a ceiling for the whole collect stage.
- **Resolve charset explicitly.** Sources are fa / ar / he / ru. HTTP-header charset, the
  XML prolog encoding, and the actual bytes disagree routinely on regional CMSes
  (windows-1256 is live in the wild for Arabic). Fix and document the precedence order.
  A wrong decode is silent mojibake into `body` — wrong data into embeddings and prompts,
  an unstable `raw_hash`, and no error raised anywhere.
- Cap items per source via `max_items` / `collection.max_items_per_source`. **Truncate by
  `published_at` descending where dates exist, document order otherwise** — feeds in this
  set are not reliably date-sorted (that is the IranWire `DATE_RE` caveat), so "first 20 of
  87" can keep old State Dept advisories and drop today's.
- **Distinguish zero-parse from empty.** A 200 response carrying 30 `<item>` elements of
  which 0 parse must not look identical in the count table to a genuinely empty feed.
  Report raw entries seen, items parsed, and items kept after the cap, separately per
  source. `collection.degraded_after_empty_runs: 3` will need that distinction, and a
  per-item parse failure should be skipped and counted, never abort the source.
- Strip HTML to text. Do not preserve tags, scripts, or comments. One exception, §3b: the
  Telegram forward-from attribution is kept as a body prefix.
- No url in `sources.yaml` may be plaintext `http://`. `mee` was `http://` through all
  four probe rounds and was changed to `https://` **unverified** on 2026-08-17 — it is
  `enabled: false` and must be probed before Phase 8 turns it on. Assert the scheme.
- A malformed feed, a timeout, or a 403 on one source must never abort the run. Collect
  what you can, count the failures, return them.

### 5. Mock mode is mandatory

The sandbox has no network egress: proxy returns 403 for every host, retested
2026-08-16. Every test runs against **saved fixture files** committed under
`tests/fixtures/` — at minimum one real RSS body, one Atom body, one `t.me/s/` page, one
gzipped body, one malformed feed, one empty feed. No test may touch the network.

Add every new module to `OFFLINE_MODULES` in `tests/integration/test_no_requests.py`.
If this phase adds a dependency, verify with that dependency made unimportable
(`sys.modules["x"] = None`), not merely "it passed here" — the Phase 2 review round 3
found a break that only the owner's clean Windows Python could see.

## Gate

Run by the **owner on Windows**, not by any agent:

1. `python -m pytest -q` — all green, and the count is predicted before the run and
   matches. State the expected number in the PR text.
2. `python -m agent.run --dry-run` — still the single summary line, still zero network.
3. A new `python -m agent.run --collect-only` against the 10 enabled sources, from a US
   runner via `workflow_dispatch` (the owner's network in Iran cannot reach most of these
   and will produce a false verdict).

**The item floor is 160, not 200.** The first draft of this brief said "≥200 items across
≥8 of 10 sources" and those two clauses are mutually exclusive under the cap this brief
itself mandates. `max_items_per_source: 20` × 9 sources + `state_dept_travel`'s
`max_items: 30` = a post-cap ceiling of **210**. Losing one source to ordinary flake costs
20 and lands on 190, so the "≥8 of 10" tolerance could never be used without failing the
item floor — and probe history shows single-source flake is routine (`centcom` OK→TIMEOUT
between ci3 and ci4, `mee` EMPTY→OK). A correct implementation would fail this gate on an
average day, and the pressure resolves in the worst possible direction: raise the cap to
pass. The "~328 items/sweep" figure in `sources.yaml` is a **pre-cap** number and does not
apply here.

Pass conditions, all four:

- **≥160 items post-cap** (8 × 20, consistent with the tolerance below).
- **≥8 of 10 sources return >0 items**, and `ynet_he` plus all three telegram sources are
  among them. A source missing from the table does not pass by absence.
- **Every `published_at` predates the workflow start timestamp, and no source returns
  items that all share one identical timestamp.** Without this the gate is passed by the
  exact bug §3 forbids — stamping `datetime.now()` on every Ynet and telegram item
  satisfies "non-null" perfectly.
- **The workflow asserts these conditions itself and exits non-zero on a miss**, the same
  pattern as `send-test.yml`, which greps its own log because `run_send_test` always exits
  0. A table a non-developer owner has to eyeball is not a gate, and an inherited
  always-exit-0 turns a red result into a green checkmark.

Print both the raw-parsed and the post-cap count per source, so a cap doing the work is
distinguishable from a feed doing it.

## Out of scope — do not build

Storage, dedupe, SQLite, embeddings, clustering, LLM calls, risk scoring, market series.
Do not enable more than the 10 sources. Do not add `signals_covered`.

**Also out of scope but decided, so Phase 6 does not relitigate it:** the owner chose
"hard cap + topic prefilter" on 2026-08-17 as the volume control. Constraint 2's ~40
LLM calls/run is currently an *estimate in prose*, enforced by nothing — the same defect
class as constraint 15. Phase 6 must (a) enforce a cluster/call cap that truncates by
priority when the budget is reached, and (b) topic-gate the six general-interest feeds
flagged `TOPIC-GATE` in `sources.yaml`. Phase 3 does neither; it just must not make
either harder.
