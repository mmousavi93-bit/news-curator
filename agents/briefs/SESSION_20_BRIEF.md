# SESSION 20 — Signal extraction stage (Phase 11a), offline-measurable, wiring deferred

## Context

Phase 11 risk engine (`src/agent/risk/engine.py`) is built and gate-verified
(33 tests, exact reproduction of all 5 backtest state-mode values). It consumes
`SignalEvent`s; **nothing yet produces them**. The accuracy gate (CLAUDE.md
v1.5) measures extraction precision/recall against 5 hand-labeled dates, and
is the next unblocked step.

## The trap this brief exists to avoid

"Add a signal-extraction stage" read naively means +1 LLM call per cluster.
That breaks constraint 2 (~40 calls/run design target; `max_calls_per_run: 70`
hard cap, of which understand already burns 30 at 150 clusters / batch 5) AND
doubles token throughput on the groq 8K-TPM binding limit. ARCHITECTURE.md
line 83 already decided the answer: understand emits `summary, entities,
signals, claim status` in ONE call. But understand.txt is a tuned Persian
digest prompt (87 lines, clickbait/irrelevance/significance logic) that cannot
absorb the 70-line English strict-schema catalog (`analysis/AGENT_PROMPT.md`)
without degrading the shipping digest.

Resolution: **do not wire extraction into the live run this session.** Build it
as a standalone, offline-measurable unit. Production wiring (same-call vs
separate-call vs subset-gated) is decided AFTER we have (a) measured
precision/recall and (b) measured per-call token cost. Until then the digest
path is untouched and the `score` stage stays `NoopStage`.

## This commit (Phase 11a)

1. **Migrate the prompt** — `analysis/AGENT_PROMPT.md` §PROMPT block moves
   verbatim to `config/prompts/signals.txt` (the Phase 7 migration that never
   happened). The catalog stays byte-identical; calibration reminders stay in
   AGENT_PROMPT.md, not in the prompt.
2. **`src/agent/pipeline/signals.py`** — extraction stage (lives in `pipeline/`
   because it calls the LLM; `risk/` is a no-LLM package). One router call per
   cluster, renders signals.txt, parses strict JSON, validates every signal_id
   against the `risk_weights.yaml` catalog, emits `SignalEvent`s, writes
   `signal_events` when a db is present. Reuses the same router contract as
   understand (no exception reaches the stage; `refused_cap`/`unavailable`
   stop the loop, logged once).
3. **`src/agent/memory/signal_models.py`** — `SignalEvent` dataclass +
   insert/read, mirroring `event_models.py`. Writes `signal_id`, `event_id`,
   `observed_at`; `state_ended_at` is null on write (set by the score stage
   when a stateful signal stops being re-confirmed — the stateful decay rule).
4. **Gate tests** (`tests/unit/test_pipeline_signals.py`) — mock router returns
   fixed JSON; assert: valid signal parses, unknown signal_id rejected,
   `none:true` → zero signals, stateful `state_update` semantics, RUMOUR
   passthrough (extraction emits, scoring filters — constraint 10 is a Step 1
   scoring rule, not extraction), corrupt JSON → skip, no crash.

## Open item to resolve during implementation (not a blocker)

The `signal_events` schema stores `source_group TEXT` (one group) but the
rulebook Step 1 needs ">=2 independent sources" PER signal. Extraction's LLM
output carries a `sources` list. Likely need one additive column
(`independent_source_count INTEGER`, resolved at write time from the sources
list against `credibility.yaml` groups) — an additive SCHEMA_VERSION 3→4
upgrade, consistent with the additive-only policy. Decide exactly when writing
`signal_models.py`; do not silently widen the schema without recording it.

## Deferred (deliberately)

- Production wiring into `build_stages()`; same-call vs separate-call decision.
- MSTRESS (separate spec, unchanged).
- Accuracy gate execution (needs the 5 hand-labeled dates — prerequisite 2).
- Markets fetcher (needs a free FRED key — prerequisite 3).

## Constraints carried forward (verbatim)

- No credential/token/password/connection string persisted; only `[REDACTED]`.
- Risk scores are deterministic Python, never LLM output — extraction only.
- Every code change needs a brief first; this file is it.
- ~200-line cap per file (constraint 12); split rather than exceed.
