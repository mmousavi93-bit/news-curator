# SESSION_10D_BRIEF — design the risk signal-extraction stage (PLANNING ONLY)

## TASK
Design — do NOT implement — the missing signal-extraction stage that turns a
news CLUSTER into discrete `SignalEvent`s for the risk engine. Produce a written
plan (architecture + mapping rules + test plan + build order). No code changes.

## CONTEXT (verified facts; do not re-derive)
- `src/agent/risk/engine.py` is BUILT and gate-verified: `RiskEngine.score(events,
  eval_date, which)` scores STRAT + TACT deterministically, consuming
  `SignalEvent` dataclasses. NO LLM calls are permitted in this package (constraint 3).
- `SignalEvent` fields (exact): `signal_id: str`, `event_date: date`,
  `source_tier: int` (1/2/3), `independent_source_count: int = 2`,
  `novelty: float = 1.0`, `state_end_date: date | None = None`.
- `config/risk_weights.yaml` is the signal catalog (A1-A9, B1-B6, C1-C6, D1-D4,
  E1-E5, H1-H3; G1/G2 = markets fetcher). Values are `[BACKTESTED]` — DO NOT
  change any of them; changing one invalidates the only validation the scorer has.
- The extraction stage DOES NOT EXIST. Nothing turns a cluster into
  `SignalEvent`s, so the engine has never scored a real news cycle. This is the
  highest-impact unblocked step.

## READ THESE FIRST (in repo)
- `src/agent/risk/engine.py` — the exact consumer interface.
- `config/risk_weights.yaml` — the signal catalog + stateful list + tier multipliers.
- `analysis/ESCALATION_SCORING.md`, `analysis/SCORING_RULEBOOK.md`,
  `analysis/WAR_SIGNALS_PAPER.md` — the rulebooks that define each signal's
  trigger conditions (what news text maps to which signal_id).
- `analysis/backtest_weights.py` — the 5 calibration scenarios + how the gate works.
- `agents/briefs/PHASE_9_BRIEF.md` and `references/risk-engine-phase-11.md` (if present).

## THE CORE DESIGN DECISION (answer this explicitly)
How does a cluster map to a `signal_id`? Two candidate approaches — pick one and
JUSTIFY it against the project's "no fabrication / deterministic where possible /
measure-don't-guess" doctrine:
1. **Deterministic keyword rules** (like the flash matcher / topic gate): a
   per-signal rule table mapping Persian/English/Arabic keyword buckets to
   signal ids. Cheap, auditable, zero-LLM — but brittle and needs the rulebook
   trigger conditions translated by hand.
2. **LLM classification** (the pipeline already runs an LLM `understand` stage
   per cluster): a prompt that maps the cluster summary to a signal_id + the
   derived fields. Higher recall, but breaks "deterministic" only if it feeds
   the SCORE (constraint 3 is about the scorer, not extraction) — state clearly
   whether extraction may use LLM while scoring stays deterministic.

Also specify, for EACH SignalEvent field, how it is derived from cluster data:
- `signal_id` (the mapping above)
- `event_date` (from the cluster's item published_at — which item? earliest? median?)
- `source_tier` (from credibility.yaml tier of the corroborating sources)
- `independent_source_count` (count of DISTINCT credibility `group`s in the
  cluster — NOT distinct source ids; see DECISION 4 / the 2026-08-17 unlisted-group incident)
- `novelty` (dedup vs the previous cycle's signals)
- `state_end_date` (for the `stateful` signals A1/A2/A3/A4/A7/H1 — how is the
  state-end detected?)

## DELIVERABLE (the gate — a written plan, not code)
Write `analysis/SESSION_10D_EXTRACTION_PLAN.md` containing:
1. The chosen mapping approach + justification.
2. Per-field derivation rules for all 6 SignalEvent fields.
3. Where the stage hooks into the pipeline (which module in `src/agent/pipeline/`,
   before/after `understand`, before/after clustering).
4. Test plan: unit tests for the field derivations, PLUS how the 5 backtest
   scenarios are reused, PLUS the 5 hand-labeled-history accuracy gate shape.
5. Build order split by model tier: `astra` (this plan) → `sol` (core
   implementation) → `luna` (formatting/boilerplate). Mark which files each tier owns.

## CONSTRAINTS (hard)
- Do NOT edit `config/risk_weights.yaml` values.
- Do NOT modify `src/agent/risk/engine.py` scoring logic.
- The SCORER stays deterministic (constraint 3). State explicitly whether the
  EXTRACTOR may use LLM and why that does/doesn't violate the constraint.
- No other files touched. This is a plan document only.

## REPORT
Print the plan file path + a 5-line summary of the chosen approach and the build order.
