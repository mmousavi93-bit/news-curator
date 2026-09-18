# Session 18 — Third provider: wire Cerebras (the capacity-wall fix)

## Problem (settled with production data, not opinion)
Run 35343007845 proved free-LLM capacity — not breaker logic — is the binding
constraint. 42 LLM calls -> 10 ok (24%); gemini 14/16 failed (503 saturation),
groq 18/26 failed (429 TPM/daily wall). Both providers walled SIMULTANEOUSLY
for ~40 of 51 minutes, so 40/90 clusters (44%) went unavailable and the digest
collapsed to 1 message (7 events). The 503-transient fix (72dfdbc) is correct
and verified live, but it is a band-aid on a capacity wall: with only two
free providers, a simultaneous wall loses ~half the clusters.

## Decision: add Cerebras as a third rung
Cerebras Inference free tier (verified from Cerebras docs, 2026-09-18):
- 5 RPM / 30K TPM / 1M tokens per hour / 1M tokens per day.
- Free models: gpt-oss-120b (OpenAI open-weight 120B), gemma-4-31b (Google).
- OpenAI-compatible endpoint -> reuses the existing chat adapter (3-line class).
- New accounts also get $5 credit (30-day).

Why Cerebras over the other candidates:
- Mistral (Experiment free plan, ~1 RPS / 500K TPM) = viable backup, NOT wired.
- Together AI = dynamic per-model limits, the useful tier needs a funded
  account -> fails the no-card constraint.
- DeepInfra = pay-as-you-go (no ongoing free tier) + blocked from Iran.

TPM 30,000 is 3.75x groq's 8,000: a batch-5 call (~5.8K tokens) no longer sits
at the wall, so Cerebras absorbs the overflow when groq 429s and gemini 503s.

## Cascade position
order becomes ["gemini", "groq", "cerebras"]. Cerebras sits THIRD: it is
untested (no key at wiring time) and must earn a higher rung on probe +
quality evidence, same discipline as every prior provider. It only fires when
gemini AND groq are both walled/out — exactly the failure mode that lost 40
clusters.

## Caps safety
- Zero cost: free tier, no card. Enforced by rpm=5 (12s pacing), tpm=30000,
  rpd=150 (1M TPD / ~5.8K per call ~= 172 calls/day; 150 for headroom).
- Inert without a key: build_adapters skips any provider with no env key, so
  pushing this config changes NOTHING until CEREBRAS_API_KEY exists in GitHub
  secrets. Runs stay gemini+groq until then.
- No TPH field in the schema; the 1M tokens/hour limit is documented in the
  config comment and approximated by the rpd/tpm guards.

## Scope
- src/agent/llm/providers.py: CerebrasAdapter + API_KEY_ENV entry.
- src/agent/llm/wiring.py: _ADAPTERS registration.
- config/settings.yaml: order + cerebras provider block.
- tests/unit/test_llm_providers.py: Cerebras coverage.
- tools/probe_cerebras.py + .github/workflows/probe-cerebras.yml: liveness
  probe (gpt-oss-120b + gemma-4-31b), same "listed != serves 200" discipline
  as probe_gemini.py.
- Redaction: no change — register_env_secrets auto-covers any *_API_KEY name.

## Acceptance
- Full suite passes with Cerebras registered.
- config/settings.yaml loads and validates (cerebras in order + providers).
- A run with no CEREBRAS_API_KEY logs "no CEREBRAS_API_KEY ... skipped" and
  proceeds gemini+groq unchanged (inert-until-key, verified by build_adapters).

## NOT done (blocked on owner)
- Probing Cerebras live (needs CEREBRAS_API_KEY).
- Adding CEREBRAS_API_KEY to GitHub secrets.
- Live run with Cerebras in the cascade.
- Re-ordering Cerebras above groq (needs quality + latency evidence first).
