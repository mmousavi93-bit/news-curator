# Session 17 — Relevance rework: model-judged `significance` replaces keyword country-match

## Problem
Sessions 15–16 optimized a relevance axis the owner has now rejected:
"which country does the story name" (iran_direct > strategic > economy).
That axis cannot separate signal from noise:

- Israel bombs Gaza (routine)          -> "strategic", gets a lift   -> WRONG
- Lebanon bombardment (routine)        -> "strategic", gets a lift   -> WRONG
- Al-Taher checkpoint destroyed        -> "strategic", same weight   -> WRONG

Same words, same countries, opposite value. Keyword matching is structurally
unable to capture "high impact people associate with an Iran/US-Israel war".

## Owner decision (2026-09-18, verbatim)
- "not necessary to mention iran specifically"
- "israel and gaza is not relevant anymore"
- "lebanon bombardment is not relevant except sth like al taher mountains
  which was heavily invested and was a checkpoint"
- "consider things with high impacts which people think of iran us-israel
  war relevance"

## New concept: war-picture impact, 4 model-judged tiers
The understand LLM already returns structured judgments (category, clickbait,
irrelevant). Add ONE structured field — `significance` — judged in the SAME
batched call (zero new LLM calls):

1. `escalation` — Iran directly in play: strikes on Iran, Iranian offensive
   action, nuclear/enrichment/IAEA/JCPOA, decapitation of a senior figure.
2. `balance` — the balance shifts WITHOUT Iran hit directly: a strategic
   asset destroyed/captured (fortification, tunnel, checkpoint, base, radar,
   air defence), force posture (carriers, bombers, mobilisation), regional
   realignment (defence pacts, normalisation, US-Russia deals). Al-Taher
   checkpoint lands here.
3. `economy` — oil, sanctions, rial, fuel, markets.
4. `none` — routine combat with no strategic consequence ("war continues"),
   roundups, anything that does not move the war picture. Routine Israel/Gaza
   and Lebanon bombardment default here. DROPPED from the digest.

Weights (added to the sort): escalation 8, balance 5, economy 3, none 0.
Missing/invalid field -> "economy" (keep-and-rank-low; dropping is
irreversible, under-ranking is recoverable).

## Why this is caps-safe (owner asked explicitly)
- ZERO new LLM calls: the field rides the existing batched understand call.
- Output tokens: one enum word + JSON key ~= ~10 tokens/cluster; ~40 clusters
  -> ~400 tokens/run. Against groq's 8,000 TPM binder and ~46K tokens/run,
  this is <1%.
- Input tokens: ~1 line of prompt instruction, paid once per batch (~8
  batches) -> ~400 tokens/run. <1%.
- max_tokens=2000 ceiling: worst case 220 tok/cluster x batch 5 = 1100;
  +10/cluster = 1150. Safe.
The DANGEROUS alternative — a separate relevance-judge LLM pass — would
DOUBLE calls (70->140, blowing max_calls_per_run/RPM/RPD/TPM) and is NOT done.

## Scope
- `config/prompts/understand.txt` + `understand_batch.txt`: add `significance`
  field + rules.
- `event_models.py`: add in-memory `significance` field (default "economy").
- `batch.py` build_event: parse + validate significance.
- `rank.py`: replace keyword relevance gate+sort with significance gate+sort.
- `settings.yaml` + `settings_schema.py`: add `significance_weights`.
- `compose.py`: drop the relevance arg from rank_events.
- `repeat_decision.py`: drop the keyword relevance term (significance is now
  inside score_event).
- Fixture (`settings_minimal.yaml`) + `test_pipeline_rank.py` updated.

## Kept keyword-based (out of scope, deliberate)
- `priority.py` on_mission: PRE-understand cluster triage — the LLM field does
  not exist yet, so keyword on_mission is forced, not a choice.
- `deescalation.py` is_escalation + `report_csv_chosen.py` on_mission column:
  separate "is this a war story" proxy, not digest ranking. Left unchanged;
  flagged as a follow-up inconsistency.

## Acceptance
- Full suite passes (858 + new significance tests).
- A story naming neither Iran nor a ring state (Al-Taher checkpoint) can
  outrank a routine-combat story that names Israel/Lebanon.
