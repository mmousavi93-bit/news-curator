# CLAUDE.md — News Curator / Personal Intelligence Agent

Read this before doing anything. Then read `ARCHITECTURE.md`. Do not start coding without both.

## Goal

A personal AI intelligence agent that monitors geopolitical, military and macroeconomic
events and delivers filtered intelligence to one private Telegram chat. Runs entirely on
free tiers, entirely unattended, on GitHub Actions. The owner's PC is always off. Owner is
non-developer: everything must be operable by editing YAML and pasting secrets.

Success is *reduced* information volume with *increased* situational awareness. A change
that produces more output is probably wrong.

---

## HARD CONSTRAINTS — do not violate, do not "improve"

These are decided. If you think one is wrong, argue it with the owner first. Do not
silently work around them.

1. **Zero cost. Zero credit cards.** Any dependency requiring payment or a card at signup is
   rejected, no exceptions.
2. **Cluster before you summarize.** Gemini free tier is 10 RPM / 1,500 RPD. Never call an
   LLM per article. The LLM sees clusters (~25–40 per run), never raw article lists.
   Budget: **max ~40 LLM calls per run**. Exceeding this breaks the system.
3. **Risk scores are deterministic Python, never LLM output.** The LLM extracts discrete
   signals only. `risk/engine.py` computes scores from `config/risk_weights.yaml`. Identical
   input must always produce an identical score, or trend deltas are meaningless.
4. **No OCR engine.** Gemini Flash vision handles images. Do not add Tesseract, EasyOCR, or
   PaddleOCR.
5. **No vector database.** SQLite + NumPy brute-force cosine. ~900 vectors. Do not add
   Chroma, Qdrant, FAISS, pgvector, Pinecone.
6. **No Telethon / MTProto.** Telegram channels are read via the public `t.me/s/<channel>`
   preview endpoint only. A session string in CI risks the owner's personal account.
7. **No agent framework in v1.** The pipeline is a linear sequence. No LangGraph, no
   LangChain, no CrewAI. Revisit only in v2 when conversational Q&A is added.
8. **Telegram messages are capped at 4,096 characters.** The composer must budget characters
   and truncate by priority. Never discover this at send time.
9. **Public repo.** Every log line is world-readable. All logging goes through the redaction
   filter in `util/logging.py`. Never print a raw environment variable.
10. **Never present unverified claims as fact.** Single-source events are labelled `RUMOUR`
    and are excluded from risk scoring entirely.
11. **Never invent content.** If nothing changed, the message says nothing changed.
12. **No file over ~200 lines.** If one grows past that it is doing two jobs — split it.
13. **Every new dependency needs a one-line justification comment** in `pyproject.toml`.
14. **State must never silently reset.** If decryption or DB integrity fails, halt and alert.
    Starting from an empty memory is a worse outcome than crashing.
15. **Any metered LLM provider needs a hard spend cap and a per-run call counter that
    halts.** A retry loop costs nothing on a free tier and a rent payment on a paid one.
    This is unattended software touching a billable API — `max_calls_per_run` and
    `max_spend_usd_per_month` in `settings.yaml` are enforced in `llm/router.py`, not
    advisory. Applies to Anthropic, DeepSeek, Moonshot, or anything added later.

## Decided facts (verified 2026-08-01)

- Gemini Flash free: **10 RPM, 1,500 RPD**, no card, vision included, may train on prompts →
  public news content only, never personal data.
- Groq free: 30 RPM, 14,400 RPD, no card. Second-tier fallback. No vision.
- OpenRouter free: 20 RPM but only **50 requests/day**, model roster rotates weekly.
  Emergency parachute only, not a real fallback.
- GitHub Actions: unlimited minutes on **public** repos; 2,000/mo private. Cache 10 GB.
  Cron auto-disables after 60 days of repo inactivity.
- Telegram Bot API: 1 msg/sec per chat, 4,096 char max.
- Paid-provider rate card, verified 2026-08-01, costed against THIS pipeline
  (~35 extraction calls/run at ~3,050 in / 400 out, 9 runs/day, vision stays on Gemini):

  | Provider | $/MTok in | $/MTok out | Cache in | Wholesale /mo | Cascade /mo | Vision |
  |---|---|---|---|---|---|---|
  | Gemini free | 0 | 0 | — | **$0** | — | yes |
  | DeepSeek V4-Flash | 0.14 | 0.28 | 0.0028 | **~$5** (~$2 cached) | ~$1 | no |
  | Claude Haiku 4.5 | 1.00 | 5.00 | 0.10 | ~$38–60 | ~$11 | yes |
  | Kimi K3 | 3.00 | 15.00 | 0.30 | ~$115–180 | ~$33 | yes |

  Output price dominates — extraction emits ~400 structured tokens per call, so a
  provider's output rate matters ~3x more than its input rate here.
  Kimi K3 is frontier-priced for what is mechanical classification. Rejected on cost.
  DeepSeek offers 5M free tokens/30d; with prompt caching the fresh-token load is
  ~3.3M/month, so the free allotment may cover extraction outright — UNVERIFIED
  whether cached reads count against it. Check before relying on it.
  DeepSeek processes in China and is text-only; Haiku 4.5 predates the tokenizer
  that emits ~30% more tokens, so do not "upgrade" without recosting.
- Estimated usage: ~36 LLM calls/run, ~288/day (5x margin); ~8 min/run, ~2,600 min/month;
  steady-state DB ~9 MB, repo ~15 MB.

## Owner's chosen configuration

- Public repo, state encrypted with `age`, force-pushed to an orphan `state` branch.
- Pipeline every 3 hours (8/day) **plus** a daily digest at 07:00 Asia/Tehran.
  Owner explicitly chose per-run messages over threshold-only delivery. Honour it, but keep
  no-change runs to a short honest one-liner.
- Output language: English. Sources in en / fa / ar / he.
- LLM order: Gemini → Groq → OpenRouter → degrade gracefully.

## Config decisions resolved 2026-08-01 (session 2)

Six gaps found when the scoring analysis was reconciled against the pipeline design.
All six are now written into `config/settings.yaml` and `config/credibility.yaml`.

1. **Canonical daily evaluation = the 07:00 digest run.** The rulebook is day-granular
   and Step 12 counts "7 consecutive *daily* evaluations", but the pipeline runs 8x/day —
   7 days or 56 runs was undefined, making WARTIME entry nondeterministic. Regime
   counters, trailing 7-day means and novelty cycles advance only at 07:00. Intra-day
   runs score normally (a signal dated today gets AGE 0 and registers within 3h) but
   never advance regime state.
2. **Retention split, no longer a flat 30 days.** H1's 30-day half-life means a state
   that ended 60 days ago still contributes 2.4; C5's is 21 days; Step 2 novelty needs
   fire history across multiple 14-day quiet cycles; D2/D3 need a 30-day baseline *per
   speaker* before they function. Signal rows are ~60 bytes → 180 days costs ~2 MB.
   New: `signal_events_days: 180`, `speaker_statements_days: 45`, `score_history_days: 365`.
   URL hashes cut 30→7 days.
3. **Persist the uncapped score.** Jun 11 2025 computes 176.4 and displays 100.0;
   Feb 27 2026 STRAT also pins at 100. Once saturated, Step 12 rule (i) — TACT ≥
   trailing mean + 10 — is mathematically dead. Store both; display capped, compute
   deltas on uncapped. Rule (i) works again inside WARTIME, which is when it is needed.
4. **Independence groups in `credibility.yaml`.** Step 1's ">=2 independent sources"
   would have been satisfied by Reuters + AP on identical wire copy, BBC English + BBC
   Persian, or IRNA + Tehran Times. Step 1 now counts distinct `group`, not distinct
   source. This was a live path to a fabricated signal entering a deterministic score.
5. **Market fetch split.** VIXCLS, T10Y2Y and BAMLH0A0HYM2 publish once daily — fetching
   per run was 64 requests/day for 8 useful values. Daily series fetch on the canonical
   run or if stale >12h; oil/gold/equities stay per-run for G1. Needs a free FRED API
   key (no card); the keyless `fredgraph.csv` endpoint works but is undocumented.
6. **`sources.yaml` gains `signals_covered: [A1, B4, ...]` per entry**, with a startup
   coverage check. A source covering no signal is dead weight; a signal covered by no
   source can never fire and nobody would notice. Reframes Phase 2 from "pick good
   outlets" to "cover 37 signals".

Not changed, deliberately: tier multipliers, half-lives, base weights, caps, convergence
and deception constants. They are backtested 5/5. Changing one without re-running
`analysis/backtest_weights.py` destroys the only validation the scoring has.

## Tone contract for generated output

Calm, knowledgeable friend. Lightly humorous. Never sensational, never dramatic, never
clickbait, never fearmongering. Alert level modulates urgency, not volume. Prompts live in
`config/prompts/*.txt` — edit those, never hardcode prompt text in Python.

## File map

| Path | Contains |
|---|---|
| `ARCHITECTURE.md` | Full design, diagrams, free-tier analysis, failure modes, phases. Source of truth. |
| `POSTMORTEMS.md` | Phase status, every defect forensic, probe-round findings, standing lessons, resolved items. Split out of this file 2026-08-19. Read on demand, never merged back. |
| `SETUP_ACCOUNTS.md` | Owner-facing signup walkthrough for every external service. |
| `config/sources.yaml` | The one file the owner edits to add a feed. **Written 2026-08-17: 51 staged. 9 enabled as of 2026-08-19** — `tg_ukmto_mirror` disabled for dormancy. Generated from probe verdicts — do not hand-edit a url without re-probing. |
| `config/sources_candidates_r6.csv` | 4 MARAD MSCI url guesses staged 2026-08-19 to replace the dead UKMTO mirror. UNVERIFIED, awaiting a probe round. |
| `config/source_prune_sheet.csv` | All 56 usable sources with tier, group, items/sweep and a keep/prefilter/cut call. Basis for session-5 decision 2. |
| `config/settings.yaml` | Thresholds, schedules, feature flags. |
| `config/credibility.yaml` | Source → credibility tier. Drives confidence scoring. |
| `config/risk_weights.yaml` | Signal → indicator weight matrix. Drives risk scoring. |
| `config/prompts/` | All prompt text, editable without touching code. |
| `config/settings.yaml` | Drafted 2026-08-01. Values marked `[BACKTESTED]` must not be changed without re-running `analysis/backtest_weights.py`. |
| `config/credibility.yaml` | Drafted 2026-08-01. `tier` = signal weight; `group` = independence. Two sources confirm only if groups differ. |
| `analysis/WAR_SIGNALS_PAPER.md` | Empirical I&W analysis of 2025-26 Iran wars; basis for risk weights. |
| `analysis/ESCALATION_SCORING.md` | STRAT/TACT scoring spec + signal catalog + free-source digestion strategy. Feeds Phases 7-8. |
| `analysis/ECONOMIC_SHOCK_SCORING.md` | Market-shock relevance matrix + MSTRESS score spec. |
| `analysis/AGENT_PROMPT.md` | Signal-extraction LLM prompt (migrate to `config/prompts/signals.txt` in Phase 7). |
| `analysis/LEAD_HANDLING.md` | Graded-assurance spec for untrusted OSINT leads. **Rev 2, 2026-08-16**, after adversarial review returned APPROVE WITH CHANGES on rev 1. Owner chose "leads visible". Load-bearing idea is **evidence-computed independence** (`G` = origins surviving near-duplicate + forward-from collapse; config `group:` is fallback only), NOT the `observability` enum — the enum was overstated in rev 1 and is now a modifier. Ladder population = tier 3 + `lead`. Demotion is `contradicted/(confirmed+contradicted)`; `aged_out` never demotes. v1 writes `lead_outcomes` silently so v1.1 has data. Zero extra LLM calls. |
| `analysis/backtest_weights.py` | Automated 5-scenario weight backtest + alert-decision tests (stdlib, deterministic). Phase 8 gate. Results in `analysis/backtest_results.csv`. |
| `analysis/SCORING_RULEBOOK.md` | Hand-calculable scoring algorithm: 12 numbered steps, all constants, 3 worked examples reproducing backtest numbers to the decimal, WARTIME alert regime. Human twin of future `risk/engine.py`. |
| `deep-research-report.md` | Other-AI report. Contains factual errors (see §7 of ESCALATION_SCORING.md); do not backtest against its timeline. |
| `agents/` | Build-agent roster: `implementer.md`, `verifier.md`, `scout.md` (Claude Code copies live in `.claude/agents/`), plus `PORTABLE_AGENT_PACK.md` — tool-agnostic prompts incl. the Architect role and the phase-brief template. |
| `agents/briefs/` | One Architect brief per phase. `PHASE_1_BRIEF.md`, `PHASE_2_BRIEF.md`, `PHASE_3_BRIEF.md`, **`PHASE_4_BRIEF.md` (Storage, 2026-08-19, 197 lines)**. An Implementer runs only from a brief. |
| `tools/` | Dev utilities, stdlib-only, never imported by the pipeline. `check_feeds.py` probes every URL in `sources_candidates.csv`. **`dump_body.py`** (180 lines, added 2026-08-19) answers *why* a live feed's data is shaped wrong: counts `DATE_RE` hits inside vs outside `<item>` slices and inventories item tag names, so cause (a) channel-level-date-only is distinguished from cause (b) collector-regex-miss mechanically. Its `ENTRY_RE`/`DATE_RE` are copied verbatim from `rss.py` and CAN drift — it prints both patterns every run so a diff is one glance. |
| `.github/workflows/probe-feeds.yml` | Manual `workflow_dispatch` run of `check_feeds.py --tag ci` from a US runner. Authoritative feed-liveness verdict. Uploads artifact, commits nothing. Already takes `candidates` + `tag` inputs, so a new probe round needs **zero code**. |
| `.github/workflows/dump-body.yml` | Manual `workflow_dispatch` run of `dump_body.py`. Diagnostic, asserts nothing, always exits 0. Also fetches `telegram.org/robots.txt` and greps it for `/s/`. Body returns as an artifact; `config/_body_dump.bin` is gitignored and must never be committed. |
| `config/sources_probe_<tag>.csv` | Probe output, one file per environment (`local` = owner's PC in Iran, `ci` = GitHub US runner). |
| `src/agent/collectors/` | One file per source type, all implement `base.py`. |
| `src/agent/pipeline/` | Linear stages: filter → vision → embed → cluster → understand → validate → compose. |
| `src/agent/memory/` | **Phase 4, built 2026-08-19, awaiting owner gate.** `schema.sql` (12 tables, `SCHEMA_VERSION = 1`), `db.py`, `models.py`, `dedup.py` (layers 1–3 only), `retention.py`, `crypto.py`. Journal mode is DELETE not WAL, on purpose. `open_db` refuses to create by default — that default IS constraint 14. Layer 4 (cosine) is Phase 6. Rationale in `POSTMORTEMS.md`. |
| `src/agent/risk/` | Deterministic scoring. No LLM calls permitted in this package. |
| `src/agent/llm/` | Provider router, backoff, circuit breaker, mock mode. |
| `src/agent/delivery/` | Telegram client and message formatter. |

## Working agreement for agents on this project

- **Architecture before code, always.** Explain the approach, get approval, then implement.
- **One phase at a time.** Phases are in `ARCHITECTURE.md` §Implementation phases. Do not
  jump ahead. Each phase ends tested and committed.
- **Challenge bad instructions.** If the owner asks for something that breaks a hard
  constraint or a free-tier limit, say so with numbers before implementing.
- **Mock mode is mandatory.** Every external call must be stubbable so tests run offline
  with no keys and no network.
- **Delegate mechanical work** (bulk file ops, wide greps, repetitive extraction, source-list
  research) to a Haiku subagent with a scoped brief. Note the routing in one line.
- **Data goes to CSV or files, not into chat.**
- **Update this file** whenever a fact is verified, an ambiguity resolved, or a phase
  completed. Keep it lean — facts, numbers, blockers, locations. No frameworks, no prose
  that belongs in `ARCHITECTURE.md`.
- **This file loads into context on EVERY turn of EVERY session. It is a per-turn tax, not
  a one-time read.** On 2026-08-19 it had reached 1,107 lines ≈ 22,600 tokens — ~340k
  tokens burned in a single 15-turn conversation before any work happened, ~600 of those
  lines being narrative that no turn needed. Split to `POSTMORTEMS.md` the same day:
  1,107 → 370 lines, ~7,400 tokens, **~15,200 saved per turn.**
  Standing rules from that:
  (a) **Narrative goes to `POSTMORTEMS.md`, not here.** Defect forensics, probe-round
      findings, review rounds, superseded decisions. Nothing is deleted — the standing
      lessons are the most valuable content in the repo, they just must not sit in the
      hot path. Add a one-line pointer here, never the story.
  (b) **A `- [ ]` item that closes moves to `POSTMORTEMS.md` immediately.** It does not
      get ticked and left behind. 24 resolved items were still loading every turn.
  (c) **Check the line count before appending.** Past ~400 lines, say the number out loud
      and split rather than append.
  (d) Bulk output — reports, tables, JSON — goes to a file in the repo, never pasted into
      chat. "The report is in the folder" costs ~200 tokens; the paste cost ~4,000.

## Build history

Phase-by-phase status, all defect forensics, probe-round findings and standing lessons
live in `POSTMORTEMS.md`. Read it before touching a phase or a subsystem it covers.
Do not re-add that content here — this file loads on every turn.

## Session 3 decisions (2026-08-12) — owner-confirmed, these override earlier text

1. **v1 scope = curator, not scorer.** Owner chose "curator now, scoring later". Ship
   collect → dedupe → cluster → filter noise → label rumours → send. STRAT/TACT/MSTRESS
   are NOT built in v1. The DB schema and extraction fields stay wired so Phases 7–8 bolt
   on with no rework, but no risk-engine code is written until the curator is running
   unattended. Rationale: the owner's stated goal is less noise, and none of the scoring
   machinery reduces noise. Specs stay on disk, unbuilt.
2. **Output = a private Telegram channel**, not a direct chat. Supersedes the "one private
   Telegram chat" line at the top of this file. Channel ID lives in an env var only, never
   a literal in any file. Gives scrollable, searchable history that survives phone changes.
3. **A settings-editing bot is v2, explicitly deferred.** v1 config is YAML edits. A bot
   that writes config needs auth, git write-back and validation — not worth it before the
   owner knows which settings he actually touches.
4. **Topic priority: Iran regional + military first.** Macro second. Other topics later and
   likely in separate channels or sub-topics, not mixed into the first feed.
5. **Clustering is a first-class v1 requirement**, not a nice-to-have. It is the mechanism
   the owner is actually asking for when he says "duplicates" and "not overwhelming".
6. **New `lead` source class — see DECISION 7 in `credibility.yaml`.** The owner will supply
   Telegram channels he does not trust but finds early-warning value in. `lead` = weight
   0.0, cannot corroborate, cannot score, never appears in output alone; it only marks a
   topic for verification against tier 1/2. Do not let a lead become a fact.
7. **Phase order re-cut for a thin end-to-end slice** — see the revised table in
   `ARCHITECTURE.md § Implementation phases`. Telegram delivery moves ahead of the
   40-source build so a real message lands on the phone in week 1.

## Session 5 decisions (2026-08-17) — owner-confirmed, these override earlier text

1. **Volume control = enforced hard cap + topic prefilter. NOT source pruning.** An earlier
   line in this file said "56 feeds cannot fit the ≤40-cluster LLM budget without pruning"
   and told the owner to cut to ~20–25. **That framing was wrong and is withdrawn.**
   Embedding is *local* (MiniLM on CPU, ARCHITECTURE.md line 238) — zero API cost, no rate
   limit — so raw item volume costs nothing at the embed stage. The chain that actually
   binds is narrower: more items → more distinct stories → more clusters → **one Gemini
   call per cluster**. The funnel assumes 800 raw → 120 unseen → ~25 clusters → 25 calls;
   at 1,984 raw it is ~300 unseen and plausibly 40–60 clusters, i.e. ~55–75 calls/run
   against a hard cap of 40.
   **The real defect: constraint 2's ~40 calls/run is an estimate in prose, enforced by no
   code.** Same class as constraint 15, which already demanded a hard counter for metered
   providers. Pruning does not fix it — it makes the overrun rarer, which is worse, because
   a fault that only fires on the busiest news day is discovered unattended during exactly
   the event this system exists for. Phase 6 must enforce a cluster/call cap that truncates
   by priority, and topic-gate the six feeds marked `TOPIC-GATE` in `sources.yaml`
   (`aawsat` 300, `dw` 137, `reuters_gnews` 100, `ap_gnews` 100, `state_dept_travel` 87,
   `france24` 29 — 753 items, 37% of the corpus, reduced reversibly instead of deleted).
2. **5 sources cut, owner-approved: `seeking_alpha`, `cnbc`, `oilprice`, `npr`, `wotr`.**
   Off-mission rather than merely noisy — market colour and commentary, not event detection.
   `oilprice` is redundant because the oil *number* comes from stooq; `wotr` is 100 items
   that comment on reports rather than witnessing anything. 185 items removed, zero unique
   regional or language coverage lost. Rationale per source in
   `config/source_prune_sheet.csv`. 56 usable → **51 in `sources.yaml`**.
3. **`mee` was plaintext `http://` through all four probe rounds.** Changed to `https://`
   **unverified** — it is `enabled: false` and must be probed before Phase 8 enables it.
   Matters because plaintext transport lets any network hop inject content into a body that
   is fed to an LLM, and this was the only source whose transport was unauthenticated.
   Failing to https is the safe direction: a wrong scheme errors loudly at Phase 8 instead
   of carrying the vector into production. `sources.yaml` now asserts the scheme.
4. **The probe UA and the collector UA do not match — this will burn Phase 3 if missed.**
   All 51 urls were verified with the full browser UA in `tools/check_feeds.py` line 48,
   after the `Mozilla/5.0 (compatible; …)` crawler form was found to draw Cloudflare 403s.
   `config/settings.yaml` line 47 still sends
   `user_agent: "news-curator/1.0 (personal research agent)"`. **A source that answered 200
   to the probe may 403 the collector.** Align settings.yaml to the probe UA in Phase 3.

## Provider economics — settled 2026-08-21 (session 7). Do not re-litigate.

- **Runtime is $0/day.** ~315 calls/day = 21% of Gemini's 1,500 RPD; Groq behind it has
  45x the daily volume. Volume model reproduces the rate card above to the dollar.
- **~97% of the noise reduction is LLM-free** (800 raw → ~120 unseen via SQLite dedup →
  ~25 clusters via local MiniLM). Provider loss costs summary prose, not the product.
- **No consumer subscription grants API access** — Claude Pro, ChatGPT, and notably
  Google AI Pro, whose higher quotas apply to AI Studio Playground/Build while raw API
  keys follow Cloud Billing tiers. Asked and answered; do not revisit.
- **A paid account is worse for stability here**: international card (the standing
  blocker) plus a KYC/sanctions surface tied to the owner. Free key from a US runner wins.
- **Decision: buy nothing for runtime.** Run the Phase 7 accuracy gate before paying any
  adjudicator. Rationale and the estimate post-mortem are in `POSTMORTEMS.md § Session 7`.

## Pending / unresolved

- [ ] **Ask DeepSeek whether cached reads count against the 5M free tokens/30d.** The
      cached fresh-token load is ~3.3M/month, so the answer decides whether DeepSeek is a
      **second zero-cost provider** or a $5/mo one. One support ticket. Highest
      value-per-effort item on this list.

- [ ] **Phase 4 owner gate — run `python -m pytest -q` on Windows. Expected `274 passed`
      (183 baseline + 91 new). Reconcile before accepting.** Baseline verified as
      167 unit + 16 integration, so a mismatch means phantom collection, not rounding.
      Also run `python -m agent.run --collect-only --db state.db --init-db` once, then
      the same command WITHOUT `--init-db` against a path that does not exist: it must
      exit 1 and create no file. That second run is the constraint-14 gate.
- [ ] **Phase 6 decision deferred by Phase 4: does the `items` table survive?** It is not
      in `ARCHITECTURE.md` §11 — it exists because gate 1 needed somewhere to write raw
      items. If clustering makes `events` the owner of raw text, delete `items`; do not
      write a second raw tier alongside it.
- [ ] Owner to approve `ARCHITECTURE.md`.
- [ ] **Answer whether `t.me/robots.txt` disallows `/s/`. Round 1 checked `telegram.org`
      by mistake — robots.txt is per-host and the collector fetches `t.me`. Fixed in
      `dump-body.yml`; rides along with the g1 re-dispatch.** The one genuinely open fact behind the decision above. Cheap:
      a one-line fetch added to the probe tool on whatever CI round happens next. Kept out
      of the pipeline deliberately — a robots fetch failing on that host would take out 23
      of 51 sources. If it *is* disallowed, that is a real input to whether `t.me/s/`
      scraping stays the Telegram path, and constraint 6 leaves no alternative, so the
      answer would be a scope question, not a code change.
- [ ] ~~NEXT ACTION — harden the gate against what finding (g) proved it cannot see.~~
      Workflow-file only, no `src/` change, so it costs one commit and one dispatch:
      (a) extend the null-`published_at` assertion from `required_source_ids` to **every**
      source — g1 slipped through purely because that check was scoped to four ids;
      (b) add a staleness assertion failing any source whose newest item is **>14 days**
      old — g2 slipped through because no staleness check exists. 14 days, not 7: State
      Dept advisories are legitimately infrequent and a tighter bound manufactures false
      failures, which is how a gate gets ignored.
      Verify with a **fresh `workflow_dispatch`**, never "Re-run jobs" — see finding (f).
      Expect it to FAIL on `state_dept_travel` (g1) and `tg_ukmto_mirror` (g2) on first run.
      **That failure is the acceptance test for the assertions themselves** — per the
      standing lesson, a gate that is only read passes; it must be attacked with the
      specific bug it exists to catch, and here both bugs are already known and live.
- [ ] **`date_only` has two unbuilt consumers. Both are REQUIRED, not optional.**
      (a) The composer must print the date and state that the time was not given — never a
      clock time. Midnight UTC renders as **03:30 Tehran**, so printing it invents a
      publication moment (constraints 10 and 11). Owner chose Tehran display 2026-08-19;
      `dates.to_tehran()` exists, fixed UTC+3:30, no tzdata.
      (b) Phase 6's 30-minute near-duplicate window must not treat same-day date-only items
      as simultaneous.
- [ ] **PREDICTED gate false-positive: condition 4, `all_timestamps_identical`.** Now that
      date-only items resolve to midnight, a run where all 30 kept `state_dept_travel`
      items share one date makes them byte-identical, and the gate reports "substituting
      `datetime.now()`" when nothing of the sort happened. Low odds while advisories span
      days, certain eventually. Fix is to carry a `date_only` count into the JSON report and
      exempt those sources from condition 4. Not built — flagged before it fires.
- [ ] **`src/agent/collectors/dates.py` is 211 lines, over the ~200 cap in constraint 12,
      and unlike the two dev tools this IS pipeline code.** It is now doing two jobs:
      timezone rules (Israel DST, Tehran) and date parsing. Suggested split: move `TEHRAN`,
      `to_tehran`, `israel_*` and `ISRAEL_WALL_CLOCK_SOURCE_IDS` to
      `src/agent/collectors/tz.py` (~70 lines), leaving `dates.py` ~165. Owner decision —
      it touches gate-green Phase 3 code, so it is flagged rather than done unilaterally.
- [ ] **Staleness-audit the other 20 Telegram sources before enabling them at Phase 8.**
      Never checked by any probe round — the probe cannot read `t.me/s/` post times (g2).
      Mechanical, delegate to a Haiku subagent once a collector run can emit per-source
      newest for all 51.
- [ ] **Re-scope or close "owner to approve ARCHITECTURE.md".** Open since session 1 while
      three phases were built against it. Stale bookkeeping at the top of the list masks
      real blockers like the robots.txt one, which was on no list anywhere.
- [ ] **Owner decision — the `group: null` contradiction** (session-5 facts, last bullet).
      Inert today, live the moment any owner-channel goes tier 2. Three options on record.
- [ ] **Make the credibility join a test, not a habit.** The 13-missing-entry defect was
      invisible to reading and took one 10-line join to find. Phase 3 must add a check that
      every id a collector loads exists as a `credibility.yaml` key, failing loudly instead
      of degrading to tier 3. Same shape as the `signals_covered` coverage check that ships
      disabled until Phase 7.
- [ ] ~~**Phase 3 collector — `DATE_RE` does not match Ynet's date format.**~~ Both `ynet` and
      `ynet_he` return 30 items with an empty `newest`, so neither can be staleness-checked.
      Related and already known: the `t.me/s/` preview has no `pubDate` at all — post times
      live in a `<time datetime=...>` attribute, and the 30-minute near-duplicate window in
      `LEAD_HANDLING.md` depends on parsing it.
      Deferred, not blocking: the 7 CUT_BOT_BLOCKED tier-1 mechanical feeds (ISW, UKMTO ×2,
      CENTCOM alt, Times of Israel, Trading Economics) are reachable only via the Google
      News `site:` proxy already used for Reuters/AP — same `USE_CAVEAT`, decide at Phase 6.
      The 3 NEEDS_BODY_DUMP rows (radio_farda, rferl_iran, safeairspace) need a probe flag
      that saves raw bytes; one round, worth it only for safeairspace.
- [ ] **Two dev tools now exceed the ~200-line cap in constraint 12: `tools/check_feeds.py`
      at 217 and `tools/dump_body.py` at 213.** One owner decision covers both. Overage in
      each is comment, not logic, and neither is pipeline code. Overage
      is comment, and it is a dev tool not pipeline code. Owner to decide: trim comments,
      split the fetch layer into `tools/_fetch.py`, or grant an explicit exception.
- [ ] Owner to create accounts per `SETUP_ACCOUNTS.md` and supply secrets.
- [ ] Owner to create the bot via @BotFather, create a private channel, add the bot as
      admin, and put `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHANNEL_ID` in GitHub Secrets.
      Never in chat, never in a commit — public repo.
- [ ] **Metis (api.metisai.ir) — owner pays in rial and it may expose API keys.** This
      partially defuses the payment blocker below, but it is NOT wired in and should not be
      without an explicit owner decision. Open question is jurisdictional, not technical:
      the prompt stream is a continuous record of an Iran-resident systematically monitoring
      Iranian military/regime topics. Content is public news; the *query pattern* is not.
      Gemini puts that outside Iranian jurisdiction, Metis puts it inside. Assumption on
      record: **Metis stays out of v1**, chain remains Gemini → Groq → degrade. Revisit at
      Phase 5 only if Gemini is revoked. Constraint 15 (hard spend cap, per-run call
      counter) applies before any metered provider goes live unattended.
- [ ] Owner to paste his own Telegram channel handles (esp. the untrusted `lead` ones)
      and prune `config/sources_candidates.csv` → then it becomes `sources.yaml`.
- [ ] Initial 40-source list not yet compiled → Phase 2.
- [ ] Credibility tiers and risk weight matrix not yet populated as YAML → Phases 7 and 8.
      Draft catalog/weights/tiers in `analysis/ESCALATION_SCORING.md` §2 and §5. Backtest
      automated 2026-08-01 (`analysis/backtest_weights.py`): 5/5 targets pass with tier
      multipliers 1.0/0.8/0.5 and the posture-persistence rule (stateful signals decay from
      state end, not first report — without it Feb 21 2026 scores 40 vs target 55–70).
      Both rules now in ESCALATION_SCORING.md §2–3. Scenario signal sets in the backtest are
      reconstructions from WAR_SIGNALS_PAPER.md, not exhaustive — refine in Phase 8 if needed.
      Stateful decay counts from state END date (Feb 21 state score: 63.9).
- [ ] Clustering similarity threshold needs empirical tuning on real data → Phase 6.
      Placeholder 0.62 in settings.yaml is a guess, not a measurement.
- [ ] **BLOCKER — payment and geo access not yet confirmed.** Owner is in Iran
      (Asia/Tehran). Every paid provider (Anthropic, DeepSeek, Moonshot) requires an
      international card or Chinese payment rails, and several US providers geo-block
      Iranian signups outright. Pipeline *execution* is safe — API calls originate from
      GitHub's US runners, not the owner's IP — but ACCOUNT CREATION and PAYMENT happen
      from Iran. Confirm the owner can actually sign up and pay before any paid provider
      enters the design. This also applies to Gemini/Groq free tiers at signup time.
      Resolve before Phase 5, not at Phase 9.
- [ ] **Phase 7 gate — measure Gemini extraction accuracy before paying anyone.**
      Hand-label signal sets for the 5 backtest scenario dates (they are already
      specified in `backtest_weights.py`), run Gemini extraction against the same
      source text, compute per-signal precision/recall. Only if F1 is poor does a paid
      adjudicator get switched on. Cascade is wired but disabled in settings.yaml.
- [ ] FRED free API key needed for the daily market series → add to SETUP_ACCOUNTS.md.
- [ ] `sources.yaml` not written. Must carry `signals_covered` per entry → Phase 2.
- [ ] X/Twitter: no legal free path as of 2026-08. Deferred to v2. Collector interface must
      accommodate it without changes elsewhere.
