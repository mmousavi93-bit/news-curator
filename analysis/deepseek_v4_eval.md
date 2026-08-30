# DeepSeek V4-Flash / V4-Pro — provider-addition evaluation

Researched 2026-08-30 with two parallel research agents (cost + quality).
**Verdict: REJECTED for now.** Revisit only via the v1.5 accuracy-gate pilot in
cascade position, if Gemini flakiness recurs across clean runs.

## 1. Pricing (verified 2026-08-30 — the repo's Aug-1 rate card is SUPERSEDED)

DeepSeek repriced V4 on 2026-08-17 to peak/off-peak billing (peak = 2x off-peak;
Beijing 09:00–12:00 + 14:00–18:00 = 7h/day; weekends off-peak since Aug 23).

| Model | Old rate (repo, ≤Aug 16) | Off-peak USD/Mtok | Peak USD/Mtok |
|---|---|---|---|
| V4-Flash (`deepseek-chat` alias retired Jul 24) | $0.14 in / $0.28 out / $0.0028 cache | **$0.21 in / $0.63 out / $0.007 cache** | $0.42 in / $1.27 out / $0.014 cache |
| V4-Pro (`deepseek-reasoner` alias retired Jul 24) | $0.435 in / $0.87 out | **$0.63 in / $1.90 out / ~$0.021 cache** | $1.27 in / $3.80 out / ~$0.042 cache |

CNY official, USD = CNY/7.1 ±3%. Cache-hit prices for V4-Pro are derived, unconfirmed.

## 2. Monthly cost for THIS pipeline

Assumptions: ~36 calls/run × 9 runs/day ≈ 324 calls/day ≈ 9,850 calls/mo; per call
2,700 cacheable catalog-in + 350 fresh-in + 400 out; 23.8% of calls at Beijing peak
(the 06:00, 12:00 Tehran runs + 07:00 digest).

| Scenario | V4-Flash | V4-Pro |
|---|---|---|
| (a) Wholesale — all extraction calls | **~$4.4/mo** | **~$13.1/mo** |
| (b) Cascade — 3rd rung after Groq's 13 free calls, ~207 calls/outage-day | **~$0.09/outage-day** | ~$0.27/outage-day |
| (b) at 1 / 3 Gemini-outage days per month | $0.09 / **$0.27/mo** | $0.27 / $0.80/mo |

Cascade usage is ~0.16M fresh tokens per outage-day — trivial against the 5M-free
allotment. In cascade position cost is effectively zero (free allotment) for Flash.

## 3. The 5M-free-token question: MOOT

Wholesale fresh load ≈ 7.4M tokens/mo (3.45M input + 3.9M output) > 5M **in every
branch** — whether cached reads count or not. The repo's earlier "~3.3M/mo may be
covered" counted fresh INPUT only; output tokens are always fresh. If cache reads
count against the allotment, the quota dies in ~4.5 days. The question no longer
decides anything: DeepSeek is not a second zero-cost provider in wholesale position.

## 4. Quality — fit for the cluster-extraction task

Task: temperature 0, strict-JSON output (~400 tok), multilingual cluster text
(en/fa/ar/he), Persian output. Failure modes that matter: invented facts
(constraint 10), softened/omitted military developments, JSON schema violations.

- **Aggregate:** V4-Flash ≈ tied with Gemini 3.6 Flash on the Artificial
  Analysis-style index; V4-Pro ≈ 1 point above Flash. The Pro-Flash gap is hard
  reasoning — irrelevant to mechanical extraction.
- **Censorship — decisive.** Official 0731 release is ~12 pts more censored than
  its preview, selectively (ctgt.ai); SpeechMap tracks elevated refusal/censorship
  on both V4 models; documented topic-selective softening on PRC-sensitive
  subjects. For this pipeline, a softened war/geopolitics item is a *silent*
  intelligence failure — worse than a loud refusal. No public test of Iran-Israel
  content specifically, but the family pattern is disqualifying.
- **JSON discipline — documented defect class.** Unquoted enum literals →
  invalid JSON on ~40–60% of long-prompt calls (deepseek-ai #1541); booleans as
  quoted strings (vllm #41122). This pipeline parses strict JSON with zero retry
  budget.
- **Thinking-mode trap.** API ships a thinking mode that breaks strict-output
  operations on OpenAI-compatible paths; reported case of thinking consuming the
  entire max_tokens budget → empty content. Temperature 0 does not reliably
  disable it.
- **Hallucination red flag.** Release-day community test reported 94% → 0%
  hallucination drop (mechanism unverified; serving-trap registries warn
  reasoning-field outputs produce exactly such figures). Unresolved red flag
  against constraint 10.
- **Multilingual:** Chinese-first behavior — documented responses in Chinese to
  Persian prompts + RTL rendering issues (#1522). Persian output cleanliness
  (the output language) is unproven.
- **Gemini Flash baseline:** adequate and production-known-good on this exact
  task (Persian output, JSON contract, temp 0, no refusals in live runs).

**Fit verdicts: V4-Flash — disqualifying for primary duty; V4-Pro — disqualifying
for the same reasons, at ~3x cost that fixes none of them.** The only defensible
use of either is as a measured research candidate behind the v1.5 hand-labeled
eval gate.

## 5. Other facts

- **Rate limits:** no published RPM/RPD — dynamic throttling by account balance;
  zero-balance accounts get lowest priority and community-reported 429s. The
  router's backoff is mandatory.
- **Vision:** V4-Flash-Vision-Exp live 2026-08-21 at Flash text prices (images
  cap 384 tokens each) — the repo's "text-only" note is outdated for Flash.
  V4-Pro is text-only. Irrelevant either way: vision stays on Gemini by design.
- **Payment:** official top-up = Alipay/WeChat with Chinese real-name ID;
  PayPal country-limited and does not serve Iran. No official path for an
  Iran-based owner — only resellers (third-party trust surface). Blocks the
  wholesale scenario at account level; does not bind the cascade scenario
  (free allotment needs no top-up).
- **Schedule risk:** this provider repriced twice in 30 days (Aug 1 → Aug 17 →
  peak/off-peak). Cost estimates carry rate-volatility risk, not just rate risk.

## 6. Sources (research agents, 2026-08-30)

Pricing: official pricing docs mirror (thevibeworks/deepseek-docs), Sina/Eastmoney/
Wallstreetcn repricing reports, TechWeb, QZ, ofox.ai, Infoworld; V4-Pro cut
(therouter.ai); name migration (ofox.ai); aihubmix legacy prices. Free allotment:
tokenmix.ai; payment gap: deepseek-ai issue #347.
Quality: ofox.ai Flash-vs-Gemini comparison; officechai V4-Pro benchmarks;
Reuters V4-Pro GA; ctgt.ai censorship drift; SpeechMap refusal scores;
deepseek-ai issues #1541/#1522/#1610; vllm #41122 + PRs #47449/#48922;
dev.to release-day hallucination test; Artificial Analysis V4 article;
OrcaRouter Flash release notes; chinaz Chinese eval.
