# Session 10A — Model-aware provider health (un-poison gemini's stale demotion)

## Decision (owner-delegated 2026-09-14)

Gemini was health-demoted because the dead `gemini-flash-latest` alias failed
~100% over the 7-day window. Pinning `gemini-3.8-flash` (commit `1fd1508`) fixed
the model but did NOT reset the stale failures, so gemini stayed dormant and the
free 20-RPD head-start was wasted. Owner: "go with whichever plan is better, but
keep enough free LLM in the system."

Chosen plan (code, self-healing, no encrypted-state surgery): key the health
record by **provider + model id**, so a model change resets the provider's stale
failure sample instead of indicting the new model. This is principled (a health
record is about a *model*, not a provider name), autonomous (no `age` secret
needed), and reusable (any future model pin self-heals).

## What it does

`save_health` and `cascade_order` each gain an optional `models` mapping
(`name -> model id`). Both are backward-compatible (omitting `models` preserves
current behavior, so existing callers/tests are untouched):

- `cascade_order`: a provider is demoted only when its stored `model` equals the
  currently-configured `model`. A stale/absent stored model (legacy record or a
  changed id) is treated as "no evidence against this model" -> not demoted.
- `save_health`: when the stored model differs from the current one, the stored
  calls/failed are discarded (start the sample fresh) and the new model id is
  written. Storing the model is what makes the reset stick on the next run.

The first run after deploy un-demotes every provider whose record predates this
change (no stored model), giving each a fresh chance; the cost is bounded to one
run of re-verification for any genuinely-sick provider, after which it re-demotes.

## Rationale

- **Both active providers are free tiers** (gemini 20 RPD head-start, groq the
  workhorse). A dormant gemini is free capacity being thrown away; restoring it
  directly serves the owner's "enough free LLM" constraint.
- **Bounded risk**: if `gemini-3.8-flash` were somehow also dead, the per-run
  circuit breaker caps the wasted attempts and the provider re-demotes after
  `_MIN_SAMPLES` failures — one run of minor latency, no data loss (groq
  carries the run). Gemini's daily quota also resets tomorrow, unblocking the
  fresh chance.

## Scope — files touched

- `src/agent/llm/health.py` — `save_health(..., models=None)`,
  `cascade_order(configured, health, models=None)`; model-aware reset + store.
- `src/agent/llm/wiring.py` — pass `{name: cfg.model}` to `cascade_order`.
- `src/agent/run.py` — pass `{name: cfg.model}` to `save_health`.
- `tests/unit/test_llm_health.py` — new tests for model-change reset and
  model-store roundtrip; existing tests (no `models`) must still pass unchanged.

## Invariants respected

- **Backward-compatible**: omitting `models` is byte-for-byte the old behavior.
- **Deterministic**: identical state + stats + models -> identical order.
- **Degrade, don't crash**: absent/corrupt `model` fields degrade to the old
  fail-rate rule, never raise.
- **No schema change**: `provider_health_v1` record just gains a per-provider
  `model` key; `load_health` already tolerates unknown fields.

## What this does NOT do

- No manual state-DB surgery / no `age` secret — the reset is derived, not forced.
- No change to the daily-quota record (`provider_daily_v1`): quota is account-level,
  not model-level, so a model pin must not reset it.
- No new provider added. Free capacity after this change = groq (workhorse) +
  gemini (restored 20-RPD head-start); a third free provider still needs an owner
  key and is out of scope.
