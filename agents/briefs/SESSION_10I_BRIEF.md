# SESSION_10I_BRIEF — plan the complete remaining path (3b → 4 → 5)

## Context
Stages 1–3a are done and pushed. Remaining: 3b (accuracy gate = data
reconstruction + human labels + measurement), 4 (gated production integration),
5 (formatting). The owner wants this remaining work FULLY PLANNED by you (astra)
and EXECUTED by sol — not hand-written piecemeal.

## What already exists (verify, do NOT re-plan or duplicate)
- Stage 2 offline core: `src/agent/pipeline/signal_contract.py`,
  `signal_attribution.py`, `signal_history.py`, `signal_calendar.py`, `signals.py`,
  `config/prompts/signal_extraction.txt`.
- Stage 3a persistence + harness: `src/agent/memory/signal_models.py`,
  `signal_state_models.py`, `analysis/eval_signal_extraction.py`.
- `tools/reconstruct_dataset.py` — hash-based membership recovery (verified:
  87/87 singletons + 23/23 small clusters recovered exactly; large clusters n≥4
  flagged "needs CI re-cluster"). Owner wants this REPLACED-or-REFINED by you and
  executed by sol, not hand-kept.
- Verdicts already decided: 10G (re-date accuracy gate to real Aug–Oct windows;
  scorer fidelity stays synthetic), 10H (hash recovery first, re-cluster fallback,
  no live-rerun marker so fresh extraction precedes the harness).

## Produce: `analysis/SESSION_10I_PLAN.md` — a complete, executable plan covering
1. **Reconstruction**: finalize the membership-recovery approach (hash first) and
   specify the CI fallback for the ~14% large clusters (a workflow that
   re-embeds + re-clusters the unrecovered ones). State the exact modules/scripts.
2. **Dataset assembly**: the end-to-end flow to assemble 5 real windows
   (currently 4 local/fetched September windows + CI download of the rest via
   `fetch-run-reports.yml`), producing `dataset.json` in the harness's schema.
3. **Labeling workflow**: the human process — schema, tooling, sequencing (blind).
   Mark explicitly HUMAN-ONLY (sol must NOT generate labels).
4. **Measurement**: the harness invocation + the acceptance thresholds + the
   fresh-extraction-then-persist-capture order (10H: no live-rerun marker).
5. **Stage 4 integration**: the production wiring plan (`understand.py` same-call
   extraction + `score.py` adapter + `run.py` RunContext), gated on accuracy/budget.
6. **Stage 5 formatting**: RUNBOOK + fixture README + eval doc cleanup.

## Output requirements
- File-by-file build order, with a gate per step.
- An explicit HUMAN vs SOL split: which steps sol executes in which order, and
  which steps are human-only (labels, final acceptance).
- Do NOT edit any code/config. Report the plan + a 10-line summary + the sol
  execution order.
