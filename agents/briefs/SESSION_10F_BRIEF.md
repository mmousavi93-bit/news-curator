# SESSION_10F_BRIEF — risk signal persistence + eval harness (stage 3a, mechanical only)

## TASK
Implement stage 3a of `analysis/SESSION_10D_EXTRACTION_PLAN.md`: the persistence
layer + eval harness for the stage-2 offline extraction core. READ the plan §3
(persistence contract) and stage 2 modules FIRST. This is MECHANICAL ONLY — NO
hand-labeled histories (those are human work, deferred). No production wiring
(stage 4).

## READ FIRST
- `analysis/SESSION_10D_EXTRACTION_PLAN.md` §3 + §4 (persistence contract, retention).
- `src/agent/pipeline/signal_history.py`, `signal_attribution.py`,
  `signal_contract.py`, `signal_calendar.py` — the stage-2 modules whose state
  this persists.
- Existing memory layer: `src/agent/memory/` (`schema.sql`, `db.py`,
  `retention.py`, any `*_models.py`) and the existing `signal_events` /
  `speaker_statements` / `scheduled_events` tables — ADAPT them additively.

## SCOPE (create/modify ONLY these)
- `src/agent/memory/signal_models.py` — new models (raw candidate record, item/quote
  rows, occurrence/episode rows, novelty ledger + canonical-day checkpoint).
- `src/agent/memory/signal_state_models.py` — state-episode rows (open/end,
  last_confirmed_at, last_checked_at) if not folded into signal_models.py.
- `src/agent/memory/schema.sql` — ADDITIVE migration only (never drop/rename
  existing columns/tables; existing `signal_events` etc. stay intact).
- `src/agent/memory/db.py`, `retention.py` — repository helpers + retention rules
  (follow the existing 180-day signal / 45-day statement policy; never delete an
  ACTIVE state or a baseline needed for the 30-day windows).
- `tests/unit/test_signal_models.py` — model round-trip + idempotent restart/replay.
- `tests/fixtures/risk_extraction/` — fixture DIRECTORY + a README describing the
  label schema (labels themselves deferred to the human).
- `analysis/eval_signal_extraction.py` — replay harness that (a) replays
  source-grounded clusters through `pipeline/signals.py` extraction, (b) loads a
  label file if present, (c) reports the plan §4 metrics. Labels are EMPTY/absent
  for now — the harness must report "no labels" cleanly, not crash.
- `analysis/SESSION_10D_EXTRACTION_EVAL.md` — short doc: how to run the replay,
  the label schema, what is still pending (human labels).

## GATE (make it pass locally; Hermes re-runs)
1. All new + existing unit tests green (Py3.12 pytest).
2. A stage-2 in-memory `SignalHistory` round-trips through the DB: materialize →
   persist → reload → identical `SignalEvent`s (same six fields + novelty).
3. Restart/replay idempotence: persisting the same run twice produces no
   duplicates (unique occurrence/run keys).
4. Additive migration: on a fresh DB, existing tables still created; the new
   columns/tables are added without breaking prior schema.
5. `config/risk_weights.yaml` and `src/agent/risk/engine.py` UNTOUCHED.

## CONSTRAINTS (hard)
- Additive schema only. Never drop/rename existing tables or columns.
- No hand-labels, no fabricated gold data. The harness must run with zero labels.
- Deterministic: identical accepted inputs -> identical persisted state.
- Persist source/group snapshots and `event_date` independently of `observed_at`.
- No git commit (Hermes does).

## REPORT
Files changed + `git diff --stat` + pytest pass count + confirmation the
round-trip + idempotence gates pass, and which existing tables were adapted.
