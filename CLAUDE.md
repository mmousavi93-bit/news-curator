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
| `SETUP_ACCOUNTS.md` | Owner-facing signup walkthrough for every external service. |
| `config/sources.yaml` | The one file the owner edits to add a feed. **Written 2026-08-17: 51 staged, 10 enabled.** Generated from probe verdicts — do not hand-edit a url without re-probing. |
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
| `agents/briefs/` | One Architect brief per phase. `PHASE_1_BRIEF.md` (2026-08-01), `PHASE_2_BRIEF.md` (2026-08-12). An Implementer runs only from a brief. |
| `tools/` | Dev utilities, stdlib-only, never imported by the pipeline. `check_feeds.py` probes every URL in `sources_candidates.csv`. |
| `.github/workflows/probe-feeds.yml` | Manual `workflow_dispatch` run of `check_feeds.py --tag ci` from a US runner. Authoritative feed-liveness verdict. Uploads artifact, commits nothing. |
| `config/sources_probe_<tag>.csv` | Probe output, one file per environment (`local` = owner's PC in Iran, `ci` = GitHub US runner). |
| `src/agent/collectors/` | One file per source type, all implement `base.py`. |
| `src/agent/pipeline/` | Linear stages: filter → vision → embed → cluster → understand → validate → compose. |
| `src/agent/memory/` | SQLite schema, models, retention pruning. |
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
  **Predicted pytest count 165** (100 Phase-2 baseline + 65), computed independently three
  times by different routes and agreeing each time. `pytest` is NOT installed in the agent
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
- Phases 4–10 — not started.
- Build-agent roster written 2026-08-01 (`agents/`). Four roles: Architect (strongest model,
  brief + gate review only, never writes code), Implementer (mid-tier for phases 4–8,
  light for 1/2/3/9/10, fresh context per phase), Verifier (light, adversarial, never the
  agent that wrote the code), Scout (light, mechanical research, CSV output only).
  Loop: brief → build → attack → gate review → commit → discard Implementer context.
  Estimated one-time build cost ~$50 at these tiers; cost is driven by context reuse, not
  model tier.

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
- **`tg_ukmto_mirror` is now the only live path to UKMTO advisories** — both official URLs
  are CUT_BOT_BLOCKED. It is tier 3 and carries `group: ukmto`, so UKMTO content can no
  longer corroborate anything or score. Maritime B-class signals are effectively
  single-sourced through an unofficial mirror. Accept or find another path at Phase 6.
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

## Pending / unresolved

- [ ] Owner to approve `ARCHITECTURE.md`.
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
      **Source discovery is now CLOSED at 56 usable feeds. No further probe rounds.**
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
- [ ] **Answer whether `telegram.org/robots.txt` disallows `/s/` — in `tools/check_feeds.py`,
      not in the collector.** The one genuinely open fact behind the decision above. Cheap:
      a one-line fetch added to the probe tool on whatever CI round happens next. Kept out
      of the pipeline deliberately — a robots fetch failing on that host would take out 23
      of 51 sources. If it *is* disallowed, that is a real input to whether `t.me/s/`
      scraping stays the Telegram path, and constraint 6 leaves no alternative, so the
      answer would be a scope question, not a code change.
- [ ] **NEXT ACTION — owner runs the Phase 3 gate.** Code is built and reviewed (see Status).
      Three steps, in order: (1) delete `.git\index.lock` on Windows — a stale lock is
      present and the agent VM cannot unlink it, and this exact file silently ate probe
      round ci3; (2) `set PYTHONPATH=src` then `python -m pytest -q`, **expect exactly 165**,
      then `python -m agent.run --dry-run` for the single summary line; (3) commit, push,
      verify the fix is inside origin (`git show origin/main:...`, not `HEAD == origin/main`),
      then run `collect-test.yml` from the Actions tab. **Do not run `--collect-only`
      locally** — the Iran network fails feeds CI reaches and would produce a false verdict
      that cuts good sources. Two things still unverifiable from any sandbox and decided only
      by that CI run: whether Ynet's date format actually parses, and whether the
      `t.me/s/` class names and `<time datetime=...>` selectors match the live page. Both are
      marked UNVERIFIED in `telegram_web.py`'s header comment; the new required-source
      timestamp assertion is what turns a wrong guess into a red gate instead of silent nulls.
- [x] ~~build Phase 3 from the brief~~ (revised 2026-08-18 after the Fable
      review: 1 CRITICAL + 5 MAJOR fixed). Collectors only. Gate is owner-run pytest on
      Windows plus a `--collect-only` CI run asserting **≥160 post-cap items**; the owner's
      Iran network cannot verify most of these feeds and would give a false verdict.
      Deliverables now include `run.py --collect-only`, `.github/workflows/collect-test.yml`
      and the `settings.yaml` UA edit — without them the gate cannot be run at all.
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
- [ ] **Phase 3 collector — `DATE_RE` does not match Ynet's date format.** Both `ynet` and
      `ynet_he` return 30 items with an empty `newest`, so neither can be staleness-checked.
      Related and already known: the `t.me/s/` preview has no `pubDate` at all — post times
      live in a `<time datetime=...>` attribute, and the 30-minute near-duplicate window in
      `LEAD_HANDLING.md` depends on parsing it.
      Deferred, not blocking: the 7 CUT_BOT_BLOCKED tier-1 mechanical feeds (ISW, UKMTO ×2,
      CENTCOM alt, Times of Israel, Trading Economics) are reachable only via the Google
      News `site:` proxy already used for Reuters/AP — same `USE_CAVEAT`, decide at Phase 6.
      The 3 NEEDS_BODY_DUMP rows (radio_farda, rferl_iran, safeairspace) need a probe flag
      that saves raw bytes; one round, worth it only for safeairspace.
- [ ] **`tools/check_feeds.py` is 217 lines, over the ~200 cap in constraint 12.** Overage
      is comment, and it is a dev tool not pipeline code. Owner to decide: trim comments,
      split the fetch layer into `tools/_fetch.py`, or grant an explicit exception.
- [x] **Untracked strays — already gone, item was stale.** Checked 2026-08-17: no CSV in the
      repo root, no `config/sources_probe_sandboxnet.csv`. All 12 probe CSVs are in `config/`.
- [x] **The agent VM CAN delete files in the mounted folder** — it needs delete permission
      granted once per folder, which was done 2026-08-17. The earlier "cannot unlink" note was
      wrong. Relevant because `git commit` from the VM leaves stale `.git/index.lock`,
      `.git/HEAD.lock` and `.git/objects/maintenance.lock` behind, and a stale `index.lock`
      is what silently ate probe round ci3. Clear them after any VM-side commit and confirm
      with `git status -sb` before trusting the result.
- [x] Owner installed `requests` on Windows 2026-08-13. Offline guarantee is now asserted by
      `tests/integration/test_no_requests.py`, not by the package being absent.
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
- [x] Warning-fatigue under sustained conflict — RESOLVED 2026-08-01: WARTIME alert regime
      (SCORING_RULEBOOK.md Step 12, ESCALATION_SCORING.md §4). Enter: TACT ≥75 for 7
      consecutive days; exit: <55 for 7 days. In-regime, daily message is a one-liner;
      full alert only on delta ≥ mean+10, category silent ≥14d firing, or STRAT tier rise.
      Scores never suppressed — message layer only. Regression-tested in backtest_weights.py.
- [x] Extraction prompt hardened 2026-08-01: AGENT_PROMPT.md catalog rewritten as per-signal
      FIRES/NOT tests + state_update field (Rule 10) for stateful posture re-confirmations.
- [ ] Clustering similarity threshold needs empirical tuning on real data → Phase 6.
      Placeholder 0.62 in settings.yaml is a guess, not a measurement.
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
      STILL OPEN: Groq key not obtained (second-tier fallback, 30 RPM / 14,400 RPD, no
      card). Get it on the same access path while that path works. OpenRouter and FRED
      also unowned. Paid providers remain unconfirmed and unneeded.
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
