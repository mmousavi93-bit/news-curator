# Phase 5 Brief — LLM Router

> **STATUS: BUILT and gate-green 2026-08-29 (session 8). Suite 361 = 274 baseline + 87
> new; predicted 362, reconciled — one test miscounted, none missing. Owner decision
> landed: `llm.max_calls_per_run = 51`. All 10 gate items pass, including the redaction
> trap and the vision-gating refusal. Details in POSTMORTEMS.md §Status (Phase 5) and
> CLAUDE.md §Session 8 decisions.**
>
> Deviations from this brief's file table, all structural (constraint 12):
> `router.py` (339-line draft) split into `router.py` + `call.py` (one attempt +
> structured logging) + `wiring.py` (build_router + build_adapters — the adapter factory
> moved out of providers.py); `limits.py` split into `limits.py` + `breaker.py`; nested
> llm-block validation lives in new `settings_llm.py` + `leaf_types.py` (the latter
> holds the leaf-type checks shared with settings.py). `tools/pytest_shim.py` is a
> permanent stdlib test runner — see the pending tools-cap decision.
> One open item flagged, do not "fix" while implementing Phase 6: `max_retries: 3`
> against `circuit_breaker_failures: 5` means the breaker never opens at shipped
> defaults — reconcile the two numbers at pipeline wiring (CLAUDE.md Pending).

Architect brief, written 2026-08-21 after Phase 4 closed green (`56e3dda`). An Implementer
runs only from this file plus `CLAUDE.md` and `ARCHITECTURE.md`. Do not read
`POSTMORTEMS.md` unless a requirement below points you at it.

Model routing: Sonnet Implementer, fresh context. Verifier must be a different agent than
the Implementer.

---

## Why this phase is fifth

Phases 1–4 built a pipeline that collects 184 items and remembers them. Nothing in it has
ever spoken to a model. Phase 6 (clustering + summarisation) is the first phase that must,
and it will need failover, pacing and a call cap on day one — so those get built and
attacked here, against fakes, where a bug costs a test run instead of a quota.

This phase is also where constraints 2 and 15 stop being prose. Session-5 decision 1 found
that `~40 LLM calls per run` is enforced by no code anywhere. The router is the only
chokepoint that sees every call, so the counter belongs here and nowhere else.

---

## Deliverable

One `complete()` and one `see()`, behind which providers fail over, rate limits are paced,
budgets are enforced, and total provider collapse degrades the run instead of ending it.
No caller ever learns which provider answered.

### Files (all under ~200 lines — split rather than exceed)

```
src/agent/llm/errors.py       typed outcomes: Retryable / Fatal / Unavailable
src/agent/llm/transport.py    lazy-imported HTTP POST + a recording mock
src/agent/llm/limits.py       call counter, RPM pacer, budget guard — clock injected
src/agent/llm/providers.py    one adapter per provider: request shape, response parse
src/agent/llm/router.py       selection, failover, circuit breaker, degrade
tests/unit/test_llm_*.py      offline, no keys, no network
```

If three provider adapters push `providers.py` past the cap, split to
`providers_<name>.py` with a shared base — that split is pre-authorised, cramming is not.

**`src/agent/run.py` is at exactly 200 lines.** Any CLI smoke flag you add breaches
constraint 12. Move argument parsing to `src/agent/cli.py` first, or add no flag. Do not
quietly ship a 214-line `run.py` — `collectors/dates.py` did that at 211 and it has been an
open owner decision for two days.

---

## Requirements

### 1. Zero new dependencies. Do not install a provider SDK.

Gemini, Groq and OpenRouter are all HTTPS + JSON. `google-generativeai`, `groq` and
`openai` each drag dozens of transitive packages into a repo whose entire runtime dependency
set is PyYAML and requests. Constraint 13 asks for a one-line justification per dependency;
the correct number of new lines in `pyproject.toml` this phase is zero.

`import requests` goes **inside** the transport constructor, never at module level — the
pattern is already set in `delivery/transport.py` and `collectors/fetch.py`. Then add every
`agent.llm.*` module to `OFFLINE_MODULES` in `tests/integration/test_no_requests.py`. That
file exists because this exact bug shipped in Phase 2 and passed in the sandbox.

Do **not** import from `agent.delivery`. Its `Transport` protocol is nearly the shape you
want, and coupling the two means a change to Telegram retry plumbing can break LLM calls.
The shared abstraction, if it is ever worth extracting, is `util/http.py`, and that is a
refactor of gate-green code needing its own owner decision. Duplicate ~40 lines instead.

### 2. The call cap is enforced in the router, not requested of the caller

A single global per-run counter, checked before the request is built. Call N+1 is
**refused, logged, and the run continues degraded** — it is never attempted and it never
raises into `run.py`.

Global, not per-stage, because the pot is shared. Note the arithmetic before you code:
`max_clusters_per_run: 40` plus `max_vision_calls_per_run: 10` plus one compose call is
**51 potential calls against constraint 2's ~40**. The free tier permits 51 (9 runs × 51 =
459, against 1,500 RPD); the constraint does not. That is an owner decision, flagged below.
Build the mechanism — one counter, one configurable ceiling, priority-ordered spending —
and read the ceiling from a new `llm.max_calls_per_run` key. Do not pick the number.

Per-run enforcement is sufficient and cross-run accounting is deliberately **not** built:
even at 51, nine runs a day cannot reach 1,500 RPD. Tracking daily quota would need a table
Phase 4 did not pre-create, i.e. a migration against encrypted state — the precise thing
Phase 4 avoided by creating twelve tables up front. Do not add one.

### 3. Failover has two failure classes and confusing them burns three quotas

Rotate to the next provider on: 429, 5xx, connection failure, timeout.

Do **not** rotate on: 400, 401, 403. A malformed request or a bad key fails identically on
all three providers, so rotating turns one visible fault into three wasted calls and a
misleading log. Fatal means fatal — surface it, stop.

A response that parses but fails schema validation is a **model** failure, not a transport
one: retry once on the same provider, then rotate. Bound total attempts; Phase 2's precedent
is a hard clamp (`MAX_RETRY_AFTER_SECONDS = 60`) because GitHub kills a job at 6 hours and an
unbounded backoff finds that limit unattended.

### 4. `see()` must be structurally incapable of reaching a text-only provider

Groq and OpenRouter carry `supports_vision: false`. Vision has no failover chain — it is
Gemini or nothing. If Gemini is unavailable, `see()` degrades to *no image transcription*,
which is honest, rather than to a text model inventing a caption, which violates constraints
10 and 11 at the source.

Assert this in code, not in a comment: selecting a provider for `see()` filters on the
capability flag, and a test attempts the illegal selection and asserts it is refused.

### 5. Pacing is proactive. RPM binds long before RPD does.

Gemini free is 10 RPM. Forty calls therefore cost a **minimum of four minutes of wall
clock** against an ~8 min/run budget. A router that only reacts to 429s spends that budget
on retries and discovers the limit the expensive way.

Maintain a minimum interval per provider derived from its configured `rpm`. Calls are
serial and stay serial — parallelism would break the pacer and buys nothing against a 10
RPM ceiling.

The clock is **injected**, exactly as `retention.py`'s is. No `time.time()`, no
`datetime.now()`, no real `sleep` inside the pacing logic. A pacer that reads the wall clock
cannot be tested and will be trusted anyway.

### 6. Circuit breaker degrades the run. It does not crash it.

`circuit_breaker_failures: 5`, then the router reports unavailable for the rest of the run.
Per `ARCHITECTURE.md` §8 the response is: collect and store as normal, deliver a one-line
"AI unavailable" notice, resume next run.

The router returns a typed result and lets the caller decide — no exception from this
package reaches `run.py`. Phase 2's delivery layer already holds this line; match it.

### 7. Secrets: the Gemini key must not travel in a URL

Keys come from environment variables and route through the redaction filter in
`util/logging.py` (constraint 9 — every log line on this repo is world-readable).

The specific trap: Gemini accepts `?key=<secret>` in the query string, and `requests`
exception messages **contain the URL**. Send it as an `x-goog-api-key` header. Any URL that
carries a credential is scrubbed before it can reach a log line or an exception message.
This is the same class as Phase 4's finding that `/proc/<pid>/cmdline` is world-readable.

### 8. Never log a prompt or a response body

Log provider, stage, latency, call index, token counts, outcome. Not content.

Source text is untrusted input (§9) and the logs are public. Structured, bodiless logging
is also what makes the redaction filter reliable rather than hopeful.

### 9. Temperature 0, and record what produced each answer

Extraction output feeds a deterministic score in Phase 7 (constraint 3). Return the model
id and a hash of the prompt alongside every response so a Phase 7 disagreement can be
reproduced. It costs nothing now and is unrecoverable retroactively.

### 10. Tighten the settings schema for the `llm` block

`settings_schema.py` currently types `providers` as `Mapping[str, Any]`, so `rpm: "ten"`,
`supports_vision: 1` and `enabled: "no"` all load silently. Phase 1's review found exactly
this class twice — leaf types unchecked, and `True == 1` passing an integer check. Type the
provider sub-keys, reject bool for numerics, and reject a provider named in `order` that has
no `providers:` entry.

Also enforce constraint 15's guard rails generically: `max_calls_per_run`,
`max_spend_usd_per_month` and `halt_on_budget_exceeded` are honoured by the router for **any**
provider carrying them, tested against a fake metered provider. Anthropic itself is
`enabled: false` and is **not implemented this phase** — the enforcement path is, so that
enabling a metered provider later is a config edit and not a new code path written under
pressure.

### 11. Mock mode is the default in tests, and it is not a stub of the router

Mock mode replaces the **transport**, so router logic — selection, failover, counting,
pacing, breaker — is the code actually under test. A mock that short-circuits the router
tests nothing. Canned responses are fixtures; the mock records calls so a test can assert
that provider 3 was never contacted.

---

## Gate

The Implementer does not decide when this is done. All asserted by tests in CI, not by
inspection:

1. **Every provider force-failed** → run exits 0, the unavailable path is taken, no
   exception escapes the package.
2. **Failover order honoured** on 429/5xx/timeout; **401 does not rotate** — assert provider
   2 was never contacted.
3. **Cap enforced.** With the ceiling at N, call N+1 is refused before a request is built,
   logged once, and the run continues.
4. **Pacing** with an injected clock: no eleventh call inside a 60-second window at
   `rpm: 10`. No test sleeps.
5. **`see()` cannot select a text-only provider**, asserted by attempting it.
6. **No key in any log line or exception**, including a simulated provider error whose body
   echoes the request URL. **No prompt or response body in any log line.**
7. **Budget guard fires** for a fake metered provider at `max_calls_per_run` and halts per
   `halt_on_budget_exceeded`.
8. **Offline and keyless.** All `agent.llm.*` modules added to `OFFLINE_MODULES`; suite
   passes with `requests` unimportable.
9. **Grep gates**, cheap and prone to creeping back: no `datetime.now()` and no `time.time()`
   under `src/agent/llm/`, and no prompt string literal in any `.py` there.
10. Full suite green on the owner's Windows. Baseline is **274**. State the new expected
    number before running it, then reconcile — a number stated afterwards is not a
    prediction.

The Verifier must attack items 2, 3 and 5 specifically. Those are the ones a broken
implementation passes by doing nothing.

---

## Out of scope — do not build

- Prompt text. The router takes prompt strings as parameters and never constructs one.
  `config/prompts/` does not exist yet and is Phase 6's to create.
- The extraction JSON schema, embeddings, clustering, summarisation. Phase 6.
- Any risk score or signal extraction. Phases 7–9.
- The Anthropic provider adapter. Config-disabled; only the budget machinery is built.
- Cross-run / daily quota persistence, and any new table. Phase 4's schema is frozen.
- Git state branch, force-push, workflow changes. Phase 7.
- `util/http.py` extraction, or any edit to `delivery/transport.py`.

---

## Open owner decisions that touch this phase

Neither blocks the build; both are recorded so the Implementer does not "fix" them.

- **`llm.max_calls_per_run` has no value yet.** 40 clusters + 10 vision + 1 compose = 51
  against constraint 2's ~40. Owner picks the ceiling; the router reads it.
- **`run.py` is at the 200-line cap**, so a smoke-test flag needs `cli.py` split first.
  Related and still open: `collectors/dates.py` at 211 lines. Do not perform either split as
  a drive-by — both touch gate-green code.
