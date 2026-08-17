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
| `tests/…` | one test module per collector, all against **saved fixtures** |

`Item` fields: `source_id`, `url`, `title`, `body`, `published_at` (aware UTC datetime or
`None`), `lang`, `raw_hash`. Nothing else. Resist adding fields Phase 4 will need.

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
  says gzip. Three feeds returned 200 with a feed content-type and zero items purely
  because of this.
- **Serialise per host, parallelise across hosts.** Probing two paths on one host
  concurrently produced a self-inflicted 429. Several ids share a host — all three BBC
  feeds, both Ynet feeds, every `t.me` channel.

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

### 4. Untrusted input, from the first byte

Feed bodies are hostile until proven otherwise and will eventually reach an LLM prompt.
- Cap response size (the probe reads 400 KB; match or better) and cap items per source
  via `max_items` / `collection.max_items_per_source`.
- Strip HTML to text. Do not preserve tags, scripts, or comments.
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
   and will produce a false verdict). Expect **≥200 items** across ≥8 of 10 sources,
   with a per-source count table and non-null `published_at` on the telegram and Ynet
   rows.

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
