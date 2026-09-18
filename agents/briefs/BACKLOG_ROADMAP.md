# Backlog / Roadmap — News Curator

Owner ideas captured 2026-09-18 (Session 17). Unstarted. Order = impact on digest quality.

## 1. Free-LLM capacity is a single point of failure (top risk, observed in run 35335314225)

Evidence from the Session 17 live run log: `calls_gemini=2 fails_gemini=2`, `calls_groq=8 fails_groq=2`.
- gemini `gemini-3.6-flash` → `status_503` twice → breaker opened → "skipped for rest of this run".
- groq `qwen/qwen3.8-27b` → `status_429` twice (free-tier token wall; pipeline waits 30s and retries, but with gemini dead there is no second provider).
- Net: `understand` stage went "unavailable after 4 attempts" → **one batch of 5 clusters skipped with no LLM answer** (`unavailable=5`; 5/34 = 15% of clusters lost, unrecoverable).
- groq recovered on the next batch (`call #8 ok`), so the loss is burst-dependent — but gemini is the only fallback and it is dead.

Fix options (probe first, do not wire unconfirmed — CLAUDE.md): (a) repair gemini 503 — **DONE 2026-09-18** (`72dfdbc`: 503 now transient like 429, never trips the breaker; probe `35338974460` confirmed gemini-3.6-flash green http=200). (b) add a third free provider — **gated, no viable candidate today**: bai removed 09-14 (batched calls >90s, ~58s single-call latency), openrouter dead 09-05 (zero-balance, monthly recheck due ~10-05), bai_deepseek wrong model id. (c) lengthen 429 backoff so a groq token wall alone no longer exhausts the stage. This is the "llm free enough in the system" risk the owner flagged — it caused real signal loss this run, not just ranking noise.

## 2. Cap is dropping signal before the LLM sees it (highest-impact next lever)

Evidence (baseline run 2026-09-18T100118Z, old keyword code): 188 clusters → **98 cap_dropped (52%)**, all with reason `over max_clusters_per_run; no LLM call`. Among them, clearly war-relevant signal with `on_mission=1`:
- "The damage caused by the Houthis strikes to the Saudi East-West pipeline"
- "U.S. military has begun its withdrawal from Iraqi Kurdistan"
- "Lockheed Martin received the first mission critical PAC-3 MSE components" (🇺🇸 ❌ 🇮🇷)

The significance gate (Session 17) only reorders the ~90 clusters that survive the cap. It cannot rescue signal that `max_clusters_per_run=90` drops before `understand` runs. Lever: raise cap / improve the pre-understand `on_mission` triage so real signal is not silently discarded. **Constraint: more clusters = more LLM calls (owner flagged caps as pipe-endangering).** Needs a cost-vs-coverage decision, not a silent raise.

## 2. Event-sequence pattern recognition (war-period signal mining)

Owner: "look after news and event sequences before and during 12 days and 40 days Iran wars and look for patterns which signify a single news or sequence of news and consider them as a signal, maybe scoring showed to user or index for showing or not showing an specific news."

Interpretation: mine the time-ordered event stream around the 12-day and 40-day war windows for temporal patterns that mark an individual item OR a run of items as significant. Deliverable: a per-item score/index that can drive show/don't-show, surfaced to the user. Distinct from Session 17's per-event semantic `significance` — this is temporal/sequence signal on top of it.

## 3. Input source enrichment

Owner: "consider input news sources and enrich them further." Add/improve the ingest sources and their metadata (coverage, dedupe, provenance, tiers).

## Reusable practice (from Session 17 testing)

Offline replay testing: download a past run's `run-reports` artifact, replay its understood clusters through the new code/weights, evaluate the digest delta before waiting on a fresh live run. `chosen_*.csv` carries fate + score + headline + summary; `summaries_*.csv` is the sent digest; `read_*.csv` is raw ingest. Fates that got an LLM `understand` call: `sent`, `rank_dropped`, `relevance_dropped` (= significance `none` gate as of Session 17), `repeat_dropped`, `sent_followup`. Pre-LLM fates: `cap_dropped`, `irrelevant`, `clickbait`, `oversized`.
