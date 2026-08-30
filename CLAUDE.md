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

**Revised 2026-08-29 by the owner** (supersedes the earlier "lightly humorous" line):
Persian output, Jalali dates. Informative, never news-anchor drama: the headline is a
complete factual one-liner the reader can judge from; the detail is 1–2 sentences beyond
it; category icons for scannability (⚔️ نظامی / 🛡️ امنیتی / 🏛️ سیاسی / 💰 اقتصادی /
🌐 سایر); «شایعه» labels visible; digest sorted by a deterministic importance score
(`pipeline/rank.py` -- digest ranking, NOT the Phase-11 risk engine) with a min-score
threshold and multi-message splits (`digest_rank:` in settings.yaml, owner-editable);
repeat follow-ups dropped by local semantic matching (`validate` stage, zero LLM calls).
Prompts live in `config/prompts/*.txt` — edit those, never hardcode prompt text in Python.

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
| `config/prompts/` | All prompt text, editable without touching code. **Created 2026-08-29 (Phase 6):** `understand.txt` (cluster summarisation, JSON contract), `vision.txt` (inert until collectors extract images). |
| `config/topics.yaml` | **Created 2026-08-29 (Phase 6):** per-language keyword lists gating the six `topic_gate: true` sources. Owner-editable; `pipeline/filter.py` validates shape. |
| `config/risk_weights.yaml` | **Created 2026-08-29 (Phase 8):** 36-signal catalog + [BACKTESTED] weights + stateful list + markets-fetcher exemptions, copied from `analysis/backtest_weights.py`. Consumed by the Phase 11 scorer and the startup coverage check. |
| `docs/` + `RUNBOOK.md` | **Phase 10:** OPERATIONS.md, ADDING_SOURCES.md, TROUBLESHOOTING.md; RUNBOOK.md is the go-live sequence (commit → secrets → state bootstrap → send-test → collect-test-expects-failure → pipeline → 3-run gate → 1-week gate). |
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
| `agents/briefs/` | One Architect brief per phase. `PHASE_1_BRIEF.md`, `PHASE_2_BRIEF.md`, `PHASE_3_BRIEF.md`, `PHASE_4_BRIEF.md` (Storage), **`PHASE_5_BRIEF.md` (LLM router, 2026-08-21, 199 lines)**. An Implementer runs only from a brief. |
| `tools/` | Dev utilities, stdlib-only, never imported by the pipeline. `check_feeds.py` probes every URL in `sources_candidates.csv`. **`dump_body.py`** answers *why* a live feed's data is shaped wrong: counts `DATE_RE` hits inside vs outside `<item>` slices and inventories item tag names, so cause (a) channel-level-date-only is distinguished from cause (b) collector-regex-miss mechanically. Its `ENTRY_RE`/`DATE_RE` are copied verbatim from `rss.py` and CAN drift — it prints both patterns every run so a diff is one glance. **`pytest_shim.py`** (465 lines, added 2026-08-29): stdlib pytest-substitute because the sandbox has no PyPI — runs the literal suite, reproduced the 361 total exactly. |
| `.github/workflows/probe-feeds.yml` | Manual `workflow_dispatch` run of `check_feeds.py --tag ci` from a US runner. Authoritative feed-liveness verdict. Uploads artifact, commits nothing. Already takes `candidates` + `tag` inputs, so a new probe round needs **zero code**. |
| `.github/workflows/pipeline.yml` | **Phase 7, written 2026-08-29.** THE unattended run: cron `0 */3 * * *` + `30 3 * * *` (07:00 Tehran digest, canonical), decrypt (halt on failure) → run → encrypt → force-push orphan `state` branch (`git add -f` — *.age is gitignored) → 90-day artifact backup. Digest cron sets `NEWS_CURATOR_DIGEST`. `permissions: contents: write`. |
| `.github/workflows/ci.yml` | Tests on push/PR. `test` job deliberately installs NO [embeddings] extra (offline guard stays meaningful); `embeddings` job installs it, caches ~/.cache/huggingface, smokes real MiniLM to 384 dims. |
| `.github/workflows/dump-body.yml` | Manual `workflow_dispatch` run of `dump_body.py`. Diagnostic, asserts nothing, always exits 0. Also fetches `telegram.org/robots.txt` and greps it for `/s/`. Body returns as an artifact; `config/_body_dump.bin` is gitignored and must never be committed. |
| `config/sources_probe_<tag>.csv` | Probe output, one file per environment (`local` = owner's PC in Iran, `ci` = GitHub US runner). |
| `src/agent/collectors/` | One file per source type, all implement `base.py`. |
| `src/agent/pipeline/` | **Phases 6–7 built 2026-08-29:** `filter.py` (topic gate), `embed.py` (Embedder protocol + MiniLM + FakeEmbedder), `cluster.py` (greedy cosine + priority rank + enforced cap), `understand.py` (one router call per cluster + clickbait/irrelevance filter + events write), `collect.py` (fetch → dedup → store → ctx.items), `compose.py` (events → one budgeted message; date_only respected), `deliver.py` (Telegram or mock), `build_stages()` in `__init__.py` wires them; vision/validate/score stay no-ops until their phases. Full pipe: `python -m agent.run --db state.db`. |
| `src/agent/memory/` | **Phase 4 closed 2026-08-21; extended Phases 6–10.** `schema.sql` (13 tables, `SCHEMA_VERSION = 2` — lead_outcomes added additively), `db.py` (additive-only upgrades), `models.py`, `event_models.py` (Phase 6), `lead_models.py` + `source_health.py` (Phase 9/10), `dedup.py` (layers 1–3), `retention.py`, `crypto.py`. Journal mode DELETE not WAL, on purpose. `open_db` refuses to create by default — constraint 14. |
| `src/agent/risk/` | Deterministic scoring. No LLM calls permitted in this package. |
| `src/agent/llm/` | **Phase 5, built + gate-green 2026-08-29.** `errors.py` (typed outcomes, LlmResult), `transport.py` (lazy requests + recording mock), `limits.py` (CallBudget, ProviderBudget, RpmPacer), `breaker.py` (backoff + circuit breaker), `call.py` (one attempt + structured logging), `providers.py` (Gemini/Groq/OpenRouter adapters), `router.py` (failover loop), `wiring.py` (build_router + build_adapters). Clock/sleep injected everywhere. |
| `src/agent/delivery/` | Telegram client and message formatter. |

## Working agreement for agents on this project

- **Architecture before code, always.** Explain the approach, get approval, then implement.
- **One phase at a time.** Phases are in `ARCHITECTURE.md` §Implementation phases. Do not
  jump ahead. Each phase ends tested and committed.
- **Challenge bad instructions.** If the owner asks for something that breaks a hard
  constraint or a free-tier limit, say so with numbers before implementing.
- **Mock mode is mandatory.** Every external call must be stubbable so tests run offline
  with no keys and no network.
- **Routing: follow the `claude-agent-routing` skill** (canonical since 2026-08-29).
  Mechanical work (bulk file ops, wide greps, repetitive extraction, source-list research) →
  Haiku scout with a scoped brief; note the routing in one line.
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

## Session 8 decisions (2026-08-29) — owner-confirmed, these override earlier text

1. **`llm.max_calls_per_run = 51`.** Owner's rule, in his words: "keep the project free,
   use free tools' caps." Worst case (40 clusters + 10 vision + 1 compose) never
   truncates; 9 runs/day × 51 = 459 calls vs Gemini's 1,500 RPD; RPM 10 paced
   proactively. Enforcement is real, not advisory: `CallBudget` refuses call N+1 before
   a request is built, refusal logged once, run continues degraded. Priority-ordered
   spending: the pipeline can `reserve(1, "compose")` so the final message can never be
   starved. Arithmetic comment lives in `settings.yaml` next to the value — do not
   change the number without re-checking it.
2. **Phase 5 BUILT and gate-green 2026-08-29.** Suite **361** (274 baseline + 87 new).
   Predicted 362, reconciled: one test miscounted (providers file has 20, not 21), none
   missing — stated before running, per gate item 10. Two test-expectation bugs of mine
   found by the shim and fixed (first-call-never-sleeps; OpenAI-shaped body given to the
   Gemini adapter). All 10 brief gates pass, incl. the redaction trap where a simulated
   provider error echoes the request URL with `?key=` in it.
3. **New modules, all under the 200-line cap:** `llm/wiring.py` (build_router +
   build_adapters — adapter factory moved out of providers.py when it crossed the cap),
   `llm/breaker.py` (backoff + CircuitBreaker), `llm/call.py` (one attempt + structured
   logging), `settings_llm.py` + `leaf_types.py` (nested `llm:` block validation; leaf
   type checks shared between settings.py and settings_llm.py — no import cycle).
   `settings_schema.py` and `settings.py` edited to use them; the generic `_check` now
   passes nested dataclass annotations through to the section builder.
4. **`dates.py` split shipped** (was 211, the flagged pending item): `tz.py` (98 lines:
   Israel rule, Tehran constant, display helper) + `dates.py` (133 lines, re-exports for
   compatibility). Suite green after; `tz.py` added to OFFLINE_MODULES.
5. **Gate false-positive fixed before it fired:** `report.py` now carries
   `date_only_count` per source and computes `all_timestamps_identical` over REAL
   timestamps only. `collect-test.yml` condition-4 comment documents the exemption.
   The every-source null-date and 14-day staleness assertions were already shipped
   2026-08-19 — the CLAUDE.md pending item was stale; only the fresh-dispatch
   verification remains (still in Pending).
6. **`tools/pytest_shim.py` (465 lines) is now permanent** — replaces the throwaway /tmp
   shim from session 7; same falsifiable-count discipline. Joins the tools line-cap
   pending decision (see Pending).
7. **OpenRouter `model` placeholder added** (`meta-llama/llama-3.1-8b-instruct:free`):
   the adapter refuses to run without a model line and the roster rotates weekly —
   owner-editable, loud if wrong.
8. **Anthropic budget guardrails wired in config** (`input/output_usd_per_mtok` from the
   rate card, per-run enforcement in `ProviderBudget`); adapter still not implemented,
   per brief.

## Phases 6–10 (2026-08-29) — v1 CODE COMPLETE. Suite 489, 0 failed, shim-verified

- Owner's mandate this session: push to done. Built per briefs: Phase 6 Understand
  (suite 415), Phase 7 Actions (429), Phase 8 Widen sources (438), Phase 9 Validate
  (464), Phase 10 Hardening (docs + RUNBOOK). Full pipe: `python -m agent.run --db
  state.db`. Details, decisions, defects and the count reconciliations: POSTMORTEMS.md
  §Status (one entry per phase) — the narrative lives there, not here.
- Standing decisions that override earlier text: `circuit_breaker_failures: 5 → 2`
  (max_retries=3 caps the loop at 4 attempts); sentence-transformers is an OPTIONAL
  `[embeddings]` extra (CI test job must NOT install it); `--dry-run` implies mock
  wiring; `items` table survives (events = post-understand store); SCHEMA_VERSION 2
  (additive lead_outcomes); 50/51 sources enabled (tg_ukmto_mirror dormant);
  accuracy gate re-filed to v1.5/Phase 11 (session-3 scope cut).
- **v1 CODE IS COMPLETE.** Remaining gates are owner/clock-bound — RUNBOOK.md §0–§8:
  commit+push, accounts/secrets/bot, state bootstrap, send-test, collect-test
  (EXPECT g1/g2 failures — they are the gate's acceptance test), first pipeline run,
  3-run gate, 1-week gate, 60-day cron reset.

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

- [ ] **2026-08-30 defect (FIXED, owner to push): validate self-match.** Every run
      since the Persian rework shipped the nothing-new one-liner: understand writes this
      run's events to the DB, then validate's anti-repetition read them back as "recent"
      and every event dropped as its own repeat (self-cosine 1.0). Fixed in
      `pipeline/validate.py` (this run's event keys excluded from the repeat window) +
      regression test; suite 489. Forensic: POSTMORTEMS.md top entry.
- [ ] **Residual wrinkle from that fix (design, not defect):** repeat matching compares
      against ALL recent stored events, including ones the owner never saw (dropped
      below min_score or as repeats). A never-seen story can suppress its own follow-ups
      within the 72h window. Clean fix = `delivered` flag on events (additive schema,
      v1.5 candidate). Live DB written by the broken runs self-corrects by ~2026-09-02.
- [ ] **Phase 8: the `date_only` composer consumer.** The understand prompt already
      prints the date + "time not stated" for date-only items (Phase 6); the composer
      must do the same when events render (pending item (a) from before Phase 6).
      Midnight UTC renders as **03:30 Tehran** — printing it invents a publication
      moment (constraints 10 and 11).
- [ ] **Ask DeepSeek whether cached reads count against the 5M free tokens/30d.** The
      cached fresh-token load is ~3.3M/month, so the answer decides whether DeepSeek is a
      **second zero-cost provider** or a $5/mo one. One support ticket. Highest
      value-per-effort item on this list. Checked 2026-08-30: NOT answerable from the
      agent sandbox — official docs (api-docs.deepseek.com) blocked by the egress
      allowlist, web search returns content-farm guides only. Two-minute owner check
      from any browser: `api-docs.deepseek.com/quick_start/pricing` fine print on free
      credits vs cache hits — or a support ticket. Do not re-search from here.

- [ ] ~~Phase 6 decision: does the `items` table survive?~~ **RESOLVED 2026-08-29:
      `items` survives** as the intra-run raw tier (pruned on `events_days`);
      `events` is the post-understand store written by Phase 6's understand stage
      (`memory/event_models.py`). No second raw tier. (Moved to POSTMORTEMS.)
- [ ] Owner to approve `ARCHITECTURE.md`.
- [ ] **Answer whether `t.me/robots.txt` disallows `/s/`.** Round 1 checked `telegram.org`
      by mistake — robots.txt is per-host and the collector fetches `t.me`. Fixed in
      `dump-body.yml`; the fetch rides along with the next CI dispatch. Tried from the
      agent sandbox 2026-08-29: blocked by the sandbox egress allowlist (only
      api.metisai.ir), so the answer still must come from a GitHub runner. Kept out of
      the pipeline deliberately — a robots fetch failing on that host would take out 23
      of 51 sources. If it *is* disallowed, that is a real input to whether `t.me/s/`
      scraping stays the Telegram path, and constraint 6 leaves no alternative, so the
      answer would be a scope question, not a code change.
- [ ] **Verify the hardened gate with a fresh `workflow_dispatch`, never "Re-run jobs".**
      The every-source null-`published_at` assertion and the >14-day staleness assertion
      are in `collect-test.yml` (added 2026-08-19, CLAUDE.md pending item was stale); the
      `all_timestamps_identical` date-only false-positive was fixed 2026-08-29
      (`date_only_count` + real-timestamps-only in `report.py`). Expect the dispatch to
      FAIL on `state_dept_travel` (g1) and `tg_ukmto_mirror` (g2) on first run — that
      failure is the acceptance test for the assertions themselves.
- [ ] **Phase 9: the 30-minute near-duplicate window must skip date-only items.**
      LEAD_HANDLING.md's lead collapse uses a 30-min window; all-midnight UTC is not
      simultaneity, so the window must not treat same-day date-only items as
      simultaneous. `Item.date_only` round-trips through memory (Phase 4) and is
      respected by the understand prompt (Phase 6) — the lead collapse is the last
      consumer.
- [ ] ~~Staleness-audit the other Telegram sources~~ DONE BY CONSTRUCTION 2026-08-29:
      the collect-test gate now asserts the >14-day staleness rule over ALL 51 sources
      on every dispatch -- the gate IS the audit. Per-source health counters
      (`memory/source_health.py`, written by the collect stage every run) make
      dormancy visible in logs between dispatches.
- [ ] **Re-scope or close "owner to approve ARCHITECTURE.md".** Open since session 1 while
      three phases were built against it. Stale bookkeeping at the top of the list masks
      real blockers like the robots.txt one, which was on no list anywhere.
- [ ] **Owner decision — the `group: null` contradiction** (session-5 facts, last bullet).
      Inert today, live the moment any owner-channel goes tier 2. Three options on record.
- [ ] **Tools line-cap decision now covers THREE files.** `tools/check_feeds.py` (217),
      `tools/dump_body.py` (213) and `tools/pytest_shim.py` (465, added 2026-08-29).
      Overage in the first two is comment/docstring, not logic; the shim is a single-job
      test runner. Owner to decide: trim, split (`tools/_fetch.py`, shim core/fixtures
      split), or grant the dev-tool exception explicitly.
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
- [ ] ~~sources.yaml signals_covered~~ DONE 2026-08-29 (Phase 8): all 51 entries carry
      `signals_covered`; the startup coverage check (`agent/coverage.py`) runs in
      build_stages against `risk_weights.yaml` (generated from the backtest's canonical
      values) -- 0 gaps on the real config; warns by default, can fail the build.
- [ ] ~~Credibility tiers and risk weight matrix as YAML~~ DONE 2026-08-29:
      `config/risk_weights.yaml` = 36-signal catalog copied from
      `analysis/backtest_weights.py` (machine-verified at generation). [BACKTESTED]
      values -- changing one invalidates the only validation the scoring has.
- [ ] **Re-filed to v1.5 (Phase 11): the extraction accuracy gate.** Hand-label the 5
      backtest scenario dates, measure Gemini extraction precision/recall BEFORE paying
      any adjudicator. Not buildable in v1: the extraction prompt and scorer do not
      exist (session-3 scope cut). The paid-adjudicator cascade stays disabled.
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
