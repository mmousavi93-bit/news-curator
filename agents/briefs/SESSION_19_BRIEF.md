# SESSION 19 — Pivot to Mistral free "Free mode" (no-card third rung)

## Context

Session 18 wired Cerebras as the third rung to fix the capacity wall
(run 35343007845: 42 calls → 10 ok, 40/90 clusters unavailable — groq 429
and gemini 503 walled simultaneously). The live probe killed it:

- `gpt-oss-120b` → **402 payment_required** ("Visit your billing tab").
- `gemma-4-31b` → **404 archived** (dead model).
- `qwen-3.8-27b` → **402 payment_required** (same billing wall).

The owner confirmed **no card** (`no cards. go` / `and i dont have card`).
Cerebras requires a payment method on file — same wall as Together AI.
**Cerebras is dropped for this project.**

## Decision

Pivot the third rung to **Mistral "Free mode"** (verified 2026-09-18 from
docs.mistral.ai + mistral.ai/pricing):

- **No payment method required** — "Free mode lets you create API keys and
  use included monthly usage within the limits shown on the Limits page."
- Budget ceiling ≈ **$10/mo in API credits** (the pricing "Free plan").
- Candidate model: `mistral-small-latest` (cost-sensitive, Apache-2.0);
  quality fallback `mistral-medium-latest` (frontier).
- `api.mistral.ai` is reachable **directly from Iran** (401 auth path, no
  geo-block, no Cloudflare UA trap) — unlike Cerebras.

## Gate (CLAUDE.md — still in force)

**Do not wire until model id + RPM + TPM are confirmed.** Mistral free-mode
RPM/TPM are account-specific (Admin Panel → API → Limits), so they cannot be
confirmed until the owner creates a key. Sequence:

1. Owner creates a Mistral account (free mode, no card) → API key →
   `MISTRAL_API_KEY` GitHub secret.
2. Dispatch `probe-mistral` (tools/probe_mistral.py) → confirm the exact
   alias the /models list reports + a chat.completions 200.
3. Owner reads the Limits page → RPM / TPM / RPD.
4. Swap `cerebras` → `mistral` in providers.py / wiring.py / settings.yaml /
   test_llm_providers.py with the confirmed values; delete probe_cerebras.py
   and probe-cerebras.yml.

## This commit

Adds `tools/probe_mistral.py` + `.github/workflows/probe-mistral.yml` only.
No wiring yet — the gate blocks it until the key lands.

## Constraints carried forward (verbatim, from earlier sessions)

- No credential/token/password/connection string may be persisted anywhere;
  only `[REDACTED]` placeholders.
- Free-LLM capacity must stay sufficient — the third rung exists to absorb
  the groq+gemini simultaneous wall, not to replace either.
- Every code change needs a brief first; this file is it.
