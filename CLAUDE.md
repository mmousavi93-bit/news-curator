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
| `config/relevance.yaml` | **Created 2026-08-30 (session 9c):** Iran-relevance keyword tiers for digest ranking — `iran_direct` 8 / `strategic` 4 / `economy` 3, highest match wins. Owner-editable; `pipeline/relevance.py` validates shape and scores. |
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

## Session 9 decisions (2026-08-30) — owner-confirmed ("finalize decisions based on understanding")

Closes the two open decisions from the first automated run's provider cascade. Forensic: POSTMORTEMS.md top entry.

1. **Per-call read timeout 60s → 20s for the primary** — `read_timeout_seconds: 20`
   on the `providers.gemini` block in settings.yaml. Connect stays 10s everywhere;
   Groq/OpenRouter keep DEFAULT_TIMEOUT (10, 60). Reason: a dead Gemini cost ~8 min
   of an ~8-min run before the breaker opened (4 attempts × 2 failures × 60s); at
   20s that path is ~160s and a merely-slow Gemini still survives. Plumbing:
   `ProviderSettings.read_timeout_seconds` (int, strict-validated) → wiring
   `timeout_map` → Router `timeout_by_provider` → `Provider.timeout` →
   transport.post. MockHttpTransport now records the timeout so tests assert the
   real value handed to requests. Suite 522 → **528** (3 validation + 3 flow tests,
   `tests/unit/test_llm_timeout.py`).
2. **Groq's free-tier ~13-call ceiling ACCEPTED as the degradation floor**, not a
   bug to fix — 30 RPM pacing was correct; the free tier's token-per-minute wall is
   the limit. On Gemini-outage days each run covers its priority-ordered top
   ~14/40 clusters. Documented next to groq's rpm/rpd in settings.yaml ("a 429
   here is NOT a pacing bug") and in POSTMORTEMS.md. OpenRouter stays a parachute
   (50 req/day ≈ 1 run), not capacity. **Adding a third provider is a v1.5
   decision taken with failure-rate data, not a knee-jerk patch after one bad
   day.**
3. **Provider-addition question (DeepSeek V4-Flash / V4-Pro) researched
   2026-08-30 and REJECTED for now.** Full dossier: `analysis/deepseek_v4_eval.md`.
   One-liner: the 5M-free-token question is MOOT (wholesale fresh load ~7.4M
   tokens/mo > 5M in every branch — the repo's 3.3M counted input only); quality
   research found family-level disqualifiers for this pipeline's extraction task
   (topic-selective censorship on war/geopolitics = silent intelligence failure,
   documented JSON-discipline defects, Chinese-first behavior on Persian).
   V4-Pro costs ~3x and fixes none of these. Revisit only via the v1.5
   accuracy-gate pilot in cascade position, if Gemini flakiness recurs.

## Session 9b (2026-08-30) — owner's local run reviewed; 4 fixes shipped, suite 528 → 532

Owner mandate: "review all, think deeply on quality and optimization and iterate."
The run was the day's SECOND provider cascade (Gemini fast-503s; Groq again hit
the ~13-call ceiling at exactly 13 — the accepted floor re-confirmed). Forensic:
POSTMORTEMS.md top entry. Fixes:
1. **`date_only_max_age_hours: 72`** (collection block) — collect drops date-only
   items older than this BEFORE dedup. The 9-day-old Lebanon advisory defect:
   `url_hashes_days=7` is shorter than the State Dept listing horizon, so items
   looped back into the digest every ~7 days forever.
2. **summaries.csv `rank` = delivered digest position** (shared `event_order_key`
   in `pipeline/rank.py`) — it was creation order, meaningless against the message.
3. **understand.txt hardened** (contract unchanged): no-new-event clusters →
   `irrelevant`; routine crime → `irrelevant` unless terror/organised violence/
   weapons; when unsure between keeping and dropping, drop.
4. **Router breaker-skip logged once per provider per run** (was 54 lines/run).

## Session 9c (2026-08-30) — owner decision: relevance FILTERS, importance SORTS, count is dynamic

Validated by two independent pro-agent evaluations of the 9 delivered events
(labels aggregated by the orchestrator; disagreements reconciled: Mecca pact =
pass, Ben Gvir prison = drop). Design: an event whose highest relevance tier
(`config/relevance.yaml`: `iran_direct` 8 / `strategic` 4 / `economy` 3) is
below `min_relevance` (3) never reaches the digest — `pipeline/relevance.py`
matches deterministically (zero LLM calls) over headline+summary+entities. The
keyword lists carry the evaluators' calibrations (travel-advisory instrument
terms, the regional state ring) and deliberately exclude blotter magnets
(بازداشت، سلاح، بمب، نتانیاهو — they caught only noise in the evaluation).
Within the gate, importance (restored category 6/4/3/2/0 + corroboration +
tier + recency) sorts; `min_score` 8 is the importance floor; the shown count
is whatever survives both — `max_messages` 3 → 6 as a safety valve only.
`chosen.csv` gains the `relevance_dropped` fate. Suite 532 → **545**.
Detection is not relevance: a commentary piece mentioning Iran still matches
`iran_direct` — the understand prompt's commentary rule is the first defense.
Real calibration still needs the clean-run CSVs.

## Session 9i (2026-08-30) — run-6 evidence: ramble cap, same-run dups, model probe, bai gateway

1. **`max_tokens: 700` on the chat adapters** — nemotron-3.5-lightning generated
   ~16.5K tokens on a ~400-token task (8-minute calls; continuous bytes kept the
   read timeout fed). Truncated JSON fails loudly instead. OpenRouter model
   swapped to inkling-small:free (nemotron disqualified).
2. **Same-run duplicate collapse in validate** — the same Hormuz tanker incident
   was delivered twice in one digest: new events were never compared against
   each other, only against previous runs. Pairwise cosine over summaries; the
   larger cluster survives.
3. **`tools/probe_free_models.py` + `probe-models.yml`** — measures every
   OpenRouter :free model against the real understand prompt, deterministic
   checks only. Manual dispatch; ~28 calls = the parachute quota — never run on
   a Gemini-down day. Joins the tools line-cap owner decision (237 lines).
4. **bai gateway wired** — cascade gemini → groq → bai → openrouter; model
   `qwen3.8-flash` (swap list in the settings comment). Free only; gpt-5.2
   stays out until constraint-15 caps. Env var `BAI_API_KEY`. Suite 547 → 558.

## Sessions 9j+9k (2026-08-30) — null-content crash, state persistence, OpenRouter verdict

1. **content:null crash fixed at both boundaries** — adapters raise SchemaError
   on null content; _extract_json raises ValueError on non-str. "Provider did
   not answer" is now retry → rotate, never a crash.
2. **Crashed runs persist state** — pipeline.yml encrypt/backup/push steps run
   `if: always()` with self-guards; BAI_API_KEY added to the run env.
3. **OpenRouter is DEAD under zero cost** — 404/404/403 sequence settled it
   (403 on a live-list model = balance policy). Parachute role is now bai's;
   the settings comment carries the runbook (404 = swap ID, 402/403 = policy).
4. **Fatal responses open the breaker** — the 403 run burned 34 identical
   fatal calls; after two, the provider is skipped for the run. Suite 562.

## Session 9l (2026-08-30) — bai live; output contract enforced in the pipeline

First run with BAI_API_KEY: full chain worked (gemini down, groq 13 clusters,
bai caught the 429-rotations, digest shipped). Bai's live answers rambled
(6,587 / 2,626 tokens on a ~400-token task) -- the gateway ignores max_tokens,
so `within_bounds` (headline 2-25 / summary 2-60 words) now runs after parse;
violations skip the cluster with an `oversized` fate. The probe measures the
same contract. Suite 562 → 564.

## Phases 6–10 (2026-08-29) — v1 CODE COMPLETE. Suite 522, 0 failed, shim-verified

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

- [ ] **NEXT SESSION — owner workflow: push → verify → analysis.** The session-9
      + 9b + 9c batches (suite 542) are the unpushed work: `git show
      origin/main:src/agent/llm/call.py | findstr provider.timeout` and
      `git show origin/main:src/agent/pipeline/collect.py | findstr date_only_max_age_hours`
      must both print; CI must be green at 542. Then the analysis session, on a
      CLEAN run only (the first two runs' CSVs are polluted by provider
      cascades): owner downloads run-reports (read / chosen / summaries / run),
      posts them; tune `digest_rank.min_score`, `config/relevance.yaml` keyword
      tiers, `cluster_similarity_threshold` (0.62 proven too strict — 67→51 and
      83→60 clusters; set 0.55 PROVISIONAL 2026-08-30, re-tune on clean CSVs),
      source pruning -- all owner-editable YAML. Gates in progress: 3-run gate,
      1-week gate, 60-day cron reset (RUNBOOK.md §6–8).
- [ ] **Batch-2 accepted edges, self-correcting ≤2026-09-02.** First run after
      deploy had no delivered markers for the last 72h (one near-duplicate may
      re-surface); format_split truncation over-marks lowest-priority items
      ≤72h. POSTMORTEMS 2026-08-30 (batch 2) references this line as the
      documentation. Delete this item once past 2026-09-02.
- [ ] **Owner decision — the `group: null` contradiction is now executed code.**
      `pipeline/validate.py` resolves null → own-id (fully independent) per the
      documented fallback, and 17 owner channels still carry `group: null` in
      `credibility.yaml`. Harmless while all stay tier 3/lead; a tier-2 promotion
      of any one opens the fabricated-signal path. Options on record
      (session-5 facts): leave, shared `unlisted_tg` group, or loader defaults.
- [ ] **Owner decision — approve `ARCHITECTURE.md`, or close the question.**
      Open since session 1; all ten phases were built and shipped against it.
      Either way this line dies. (Merges the two duplicate approval items.)
- [ ] **Owner decision — line cap on six files.** `tools/check_feeds.py` (217),
      `tools/dump_body.py` (213), `tools/pytest_shim.py` (465),
      `memory/schema.sql` (226, comment-only), `tests/unit/test_pipeline_compose.py`
      (295), `tests/unit/test_pipeline_validate.py` (378). Overage is comments and
      test growth, no logic split needed. Decide: trim, split, or grant explicit
      exceptions per category (dev tools / schema comments / tests).
- [ ] **v1.5 (Phase 11) scope — risk engine, accuracy gate, markets fetcher.**
      Hand-label the 5 backtest scenario dates, measure Gemini extraction
      precision/recall BEFORE paying any adjudicator; paid cascade stays disabled.
      Markets fetcher needs a free FRED API key (no card) → add to SETUP_ACCOUNTS.md
      (OpenRouter key is OWNED since 2026-08-30 -- first live use 404'd on a
      delisted model; runbook in the settings.yaml openrouter comment).
- [ ] **v1.1/v2 deferred, recorded:** (a) evidence-computed lead independence
      (LEAD_HANDLING rev 2) -- its 30-minute near-duplicate window must skip
      date-only items; all-midnight UTC is not simultaneity. (b) Metis stays out
      -- revisit only under the third-provider decision if Gemini is revoked.
      (c) X/Twitter: no legal free path -- deferred to v2; the collector interface
      accommodates it. (d) Maritime coverage is ZERO (UKMTO mirror disabled
      2026-08-19, all 4 MARAD paths blocked) -- only a Google News `site:` proxy
      or owner-supplied mirror could close it.
